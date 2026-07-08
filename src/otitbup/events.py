"""Event bus: structured operational events fanned out to the audit log,
Python logging, syslog, and SNMPv2c traps.

Every noteworthy action emits one Event. Sinks are configured under
`events:` in the config:

    events:
      syslog:
        address: 10.0.0.1
        port: 514
        facility: local0
      snmp_trap:
        address: 10.0.0.2
        port: 162
        community: public
        enterprise_oid: 1.3.6.1.4.1.99999   # your enterprise OID base

Both sinks are stdlib-only (no pysnmp): syslog via SysLogHandler, SNMP via
a minimal BER-encoded SNMPv2-Trap PDU over UDP. Sink failures are logged,
never fatal.
"""
from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from logging.handlers import SysLogHandler
from typing import Any

log = logging.getLogger("otitbup.events")

# Event type -> (numeric id for the SNMP trap OID suffix, default severity).
# Severity is a syslog-style level name.
PROCESS_START = "process.start"
PROCESS_STOP = "process.stop"
WEBUI_START = "webui.start"
WEBUI_STOP = "webui.stop"
BACKUP_START = "backup.start"
BACKUP_STOP = "backup.stop"
BACKUP_ERROR = "backup.error"
CONFIG_READ = "config.read"
CONFIG_RELOAD = "config.reload"
LOGIN = "auth.login"
LOGOUT = "auth.logout"
USER_CREATE = "user.create"
USER_DELETE = "user.delete"
USER_PASSWD = "user.passwd"

_EVENT_IDS: dict[str, int] = {
    PROCESS_START: 1, PROCESS_STOP: 2,
    WEBUI_START: 3, WEBUI_STOP: 4,
    BACKUP_START: 5, BACKUP_STOP: 6, BACKUP_ERROR: 7,
    CONFIG_READ: 8, CONFIG_RELOAD: 9,
    LOGIN: 10, LOGOUT: 11,
    USER_CREATE: 12, USER_DELETE: 13, USER_PASSWD: 14,
}
_DEFAULT_SEVERITY = {
    BACKUP_ERROR: "error",
    USER_CREATE: "notice", USER_DELETE: "notice", USER_PASSWD: "notice",
}
_SYSLOG_LEVEL = {
    "emergency": 0, "alert": 1, "critical": 2, "error": 3,
    "warning": 4, "notice": 5, "info": 6, "debug": 7,
}
_FACILITIES = {
    "kern": 0, "user": 1, "daemon": 3,
    "local0": 16, "local1": 17, "local2": 18, "local3": 19,
    "local4": 20, "local5": 21, "local6": 22, "local7": 23,
}


@dataclass
class Event:
    type: str
    message: str
    severity: str = "info"
    actor: str | None = None
    detail: str | None = None


# --------------------------------------------------------------- BER / SNMP

def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(value)) + value


def _ber_int(n: int, tag: int = 0x02) -> bytes:
    if n == 0:
        return _tlv(tag, b"\x00")
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if body[0] & 0x80:              # keep it unsigned-looking
        body = b"\x00" + body
    return _tlv(tag, body)


def _ber_oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.split(".")]
    encoded = [40 * parts[0] + parts[1]] + parts[2:]
    body = b""
    for value in encoded:
        if value < 0x80:
            body += bytes([value])
            continue
        chunk = [value & 0x7F]
        value >>= 7
        while value:
            chunk.append((value & 0x7F) | 0x80)
            value >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


def _varbind(oid: str, value: bytes) -> bytes:
    return _tlv(0x30, _ber_oid(oid) + value)


def build_snmpv2_trap(
    community: str, enterprise_oid: str, event: Event, request_id: int,
    uptime_ticks: int,
) -> bytes:
    """Encode an SNMPv2c Trap PDU. Varbinds: sysUpTime.0, snmpTrapOID.0
    (= <enterprise>.0.<event-id>), plus message and severity strings."""
    trap_oid = f"{enterprise_oid}.0.{_EVENT_IDS.get(event.type, 0)}"
    varbinds = (
        _varbind("1.3.6.1.2.1.1.3.0", _ber_int(uptime_ticks, tag=0x43))
        + _varbind("1.3.6.1.6.3.1.1.4.1.0", _ber_oid(trap_oid))
        + _varbind(f"{enterprise_oid}.1.1",
                   _tlv(0x04, event.type.encode()))
        + _varbind(f"{enterprise_oid}.1.2",
                   _tlv(0x04, event.message.encode()))
        + _varbind(f"{enterprise_oid}.1.3",
                   _tlv(0x04, event.severity.encode()))
    )
    pdu = _tlv(
        0xA7,                                    # SNMPv2-Trap-PDU [7]
        _ber_int(request_id) + _ber_int(0) + _ber_int(0)
        + _tlv(0x30, varbinds),
    )
    return _tlv(
        0x30,
        _ber_int(1) + _tlv(0x04, community.encode()) + pdu,  # version v2c=1
    )


# ------------------------------------------------------------- EventBus

class EventBus:
    def __init__(self, cfg: dict[str, Any] | None = None, runstore=None):
        self.cfg = cfg or {}
        self.runstore = runstore
        self._start = time.monotonic()
        self._request_id = 0

    def emit(
        self, event_type: str, message: str, *,
        severity: str | None = None, actor: str | None = None,
        detail: str | None = None,
    ) -> None:
        event = Event(
            type=event_type,
            message=message,
            severity=severity or _DEFAULT_SEVERITY.get(event_type, "info"),
            actor=actor,
            detail=detail,
        )
        log.info("event %s: %s", event.type, event.message)
        self._to_audit(event)
        if self.cfg.get("syslog"):
            self._to_syslog(event)
        if self.cfg.get("snmp_trap"):
            self._to_snmp(event)

    def _to_audit(self, event: Event) -> None:
        if self.runstore is None:
            return
        try:
            self.runstore.audit(
                time.time(), event.type, actor=event.actor,
                detail=event.detail or event.message,
            )
        except Exception as exc:
            log.debug("audit sink failed: %s", exc)

    def _to_syslog(self, event: Event) -> None:
        cfg = self.cfg["syslog"]
        facility = _FACILITIES.get(cfg.get("facility", "local0"), 16)
        level = _SYSLOG_LEVEL.get(event.severity, 6)
        try:
            handler = SysLogHandler(
                address=(cfg.get("address", "127.0.0.1"),
                         int(cfg.get("port", 514))),
                facility=facility,
            )
            record = logging.LogRecord(
                "otitbup", logging.INFO, "", 0,
                f"{event.type} {event.message}"
                + (f" actor={event.actor}" if event.actor else ""),
                None, None,
            )
            record.levelno = level  # map to syslog severity
            handler.emit(record)
            handler.close()
        except Exception as exc:
            log.warning("syslog sink failed: %s", exc)

    def _to_snmp(self, event: Event) -> None:
        cfg = self.cfg["snmp_trap"]
        self._request_id = (self._request_id + 1) & 0x7FFFFFFF
        uptime = int((time.monotonic() - self._start) * 100)
        try:
            datagram = build_snmpv2_trap(
                community=cfg.get("community", "public"),
                enterprise_oid=cfg.get("enterprise_oid",
                                       "1.3.6.1.4.1.99999"),
                event=event, request_id=self._request_id or 1,
                uptime_ticks=uptime,
            )
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(
                    datagram,
                    (cfg.get("address", "127.0.0.1"), int(cfg.get("port", 162))),
                )
            finally:
                sock.close()
        except Exception as exc:
            log.warning("snmp trap sink failed: %s", exc)


class NullEventBus(EventBus):
    """No-op bus for contexts without event configuration."""

    def emit(self, *args, **kwargs) -> None:  # noqa: D401
        pass
