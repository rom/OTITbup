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
        version: v2c                        # v1 | v2c | v3
        community: public                   # v1/v2c
        enterprise_oid: 1.3.6.1.4.1.99999   # your enterprise OID base
        v3:                                 # only for version: v3 (USM)
          engine_id: 8000270b0102030405     # hex; must match the receiver
          username: otitbup
          auth_protocol: sha256             # none | md5 | sha1 | sha256
          auth_key_file: /etc/otitbup/snmpv3.auth
          priv_protocol: none               # none | aes128
          priv_key_file: /etc/otitbup/snmpv3.priv

Both sinks are stdlib-only (no pysnmp): syslog via SysLogHandler, SNMP via
minimal BER-encoded trap PDUs over UDP (RFC 1157 Trap-PDU for v1,
SNMPv2-Trap for v2c, USM per RFC 3414/3826 for v3 — authPriv needs the
`crypto` extra for AES). Sink failures are logged, never fatal.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
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
CONFIG_CHANGED = "config.changed"
CHANGE_UNEXPECTED = "change.unexpected"
ANOMALY = "anomaly.detected"
INTEGRITY_OK = "integrity.ok"
INTEGRITY_ERROR = "integrity.error"
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
    CHANGE_UNEXPECTED: 15, ANOMALY: 16,
    INTEGRITY_OK: 17, INTEGRITY_ERROR: 18,
    CONFIG_CHANGED: 19,
}
_DEFAULT_SEVERITY = {
    BACKUP_ERROR: "error",
    CONFIG_CHANGED: "warning",
    CHANGE_UNEXPECTED: "warning",
    ANOMALY: "warning",
    INTEGRITY_ERROR: "error",
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


class Broadcaster:
    """In-process fan-out of live events to connected web clients (SSE).

    Each subscriber gets its own bounded queue; a slow or dead client fills
    its queue and is silently dropped rather than blocking the emitter. This
    is deliberately memory-only and single-process — it matches the
    appliance's single web process and needs no broker."""

    def __init__(self, maxsize: int = 100):
        import queue
        import threading
        self._queue = queue
        self._subscribers: set = set()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def subscribe(self):
        q = self._queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except self._queue.Full:
                self.unsubscribe(q)


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


def _event_varbinds(enterprise_oid: str, event: Event) -> bytes:
    """The enterprise varbinds shared by every trap version: event type,
    message, and severity as strings under <enterprise>.1.x."""
    return (
        _varbind(f"{enterprise_oid}.1.1", _tlv(0x04, event.type.encode()))
        + _varbind(f"{enterprise_oid}.1.2", _tlv(0x04, event.message.encode()))
        + _varbind(f"{enterprise_oid}.1.3", _tlv(0x04, event.severity.encode()))
    )


def build_snmpv1_trap(
    community: str, enterprise_oid: str, event: Event, uptime_ticks: int,
    agent_addr: str = "0.0.0.0",
) -> bytes:
    """Encode an RFC 1157 SNMPv1 Trap-PDU: enterprise OID, agent address,
    generic-trap 6 (enterpriseSpecific) and the event id as specific-trap."""
    addr = bytes(int(p) for p in agent_addr.split("."))
    pdu = _tlv(
        0xA4,                                    # Trap-PDU [4]
        _ber_oid(enterprise_oid)
        + _tlv(0x40, addr)                       # agent-addr (IpAddress)
        + _ber_int(6)                            # generic-trap: enterpriseSpecific
        + _ber_int(_EVENT_IDS.get(event.type, 0))  # specific-trap
        + _ber_int(uptime_ticks, tag=0x43)       # time-stamp (TimeTicks)
        + _tlv(0x30, _event_varbinds(enterprise_oid, event)),
    )
    return _tlv(
        0x30,
        _ber_int(0) + _tlv(0x04, community.encode()) + pdu,  # version v1=0
    )


def _v2_trap_pdu(enterprise_oid: str, event: Event, request_id: int,
                 uptime_ticks: int) -> bytes:
    """The SNMPv2-Trap-PDU (also carried inside v3 messages). Varbinds:
    sysUpTime.0, snmpTrapOID.0 (= <enterprise>.0.<event-id>), plus the
    event strings."""
    trap_oid = f"{enterprise_oid}.0.{_EVENT_IDS.get(event.type, 0)}"
    varbinds = (
        _varbind("1.3.6.1.2.1.1.3.0", _ber_int(uptime_ticks, tag=0x43))
        + _varbind("1.3.6.1.6.3.1.1.4.1.0", _ber_oid(trap_oid))
        + _event_varbinds(enterprise_oid, event)
    )
    return _tlv(
        0xA7,                                    # SNMPv2-Trap-PDU [7]
        _ber_int(request_id) + _ber_int(0) + _ber_int(0)
        + _tlv(0x30, varbinds),
    )


def build_snmpv2_trap(
    community: str, enterprise_oid: str, event: Event, request_id: int,
    uptime_ticks: int,
) -> bytes:
    """Encode a community-based SNMPv2c Trap message."""
    pdu = _v2_trap_pdu(enterprise_oid, event, request_id, uptime_ticks)
    return _tlv(
        0x30,
        _ber_int(1) + _tlv(0x04, community.encode()) + pdu,  # version v2c=1
    )


# --------------------------------------------------------------- SNMPv3/USM

# auth protocol -> (hashlib name, HMAC truncation length per RFC 3414/7860)
_V3_AUTH = {
    "md5": ("md5", 12),
    "sha1": ("sha1", 12),
    "sha": ("sha1", 12),
    "sha256": ("sha256", 24),
}


def usm_localized_key(password: str, engine_id: bytes, hash_name: str) -> bytes:
    """RFC 3414 password-to-key: hash the password stream expanded to 1 MiB
    (Ku), then localize it to the engine: H(Ku || engineID || Ku)."""
    import hashlib
    if not password:
        raise ValueError("empty SNMPv3 passphrase")
    data = (password.encode() * (1048576 // len(password.encode()) + 1))
    ku = hashlib.new(hash_name, data[:1048576]).digest()
    return hashlib.new(hash_name, ku + engine_id + ku).digest()


def build_snmpv3_trap(
    v3: dict[str, Any], enterprise_oid: str, event: Event, request_id: int,
    uptime_ticks: int,
) -> bytes:
    """Encode an SNMPv3 (USM) trap. The trap sender is the authoritative
    engine, so `engine_id` here must match the user configured on the
    receiver. Supports noAuthNoPriv, authNoPriv (HMAC-MD5/SHA1/SHA-256) and
    authPriv (AES-128-CFB per RFC 3826; needs the `crypto` extra)."""
    engine_id = bytes.fromhex(str(v3.get("engine_id", "8000270b01")).strip())
    username = str(v3.get("username", "otitbup"))
    auth_proto = str(v3.get("auth_protocol", "none")).lower()
    priv_proto = str(v3.get("priv_protocol", "none")).lower()
    auth_pass = str(v3.get("auth_key", "") or "")
    priv_pass = str(v3.get("priv_key", "") or "")
    if priv_proto not in ("", "none") and auth_proto in ("", "none"):
        raise ValueError("SNMPv3 privacy requires an auth protocol")

    boots = int(v3.get("engine_boots", 1))
    etime = uptime_ticks // 100
    scoped_pdu = _tlv(
        0x30,
        _tlv(0x04, engine_id) + _tlv(0x04, b"")     # contextEngineID, name
        + _v2_trap_pdu(enterprise_oid, event, request_id, uptime_ticks),
    )

    flags = 0
    auth_len = 0
    hash_name = ""
    if auth_proto not in ("", "none"):
        if auth_proto not in _V3_AUTH:
            raise ValueError(f"unknown SNMPv3 auth protocol {auth_proto!r}")
        hash_name, auth_len = _V3_AUTH[auth_proto]
        flags |= 0x01
    priv_params = b""
    msg_data = scoped_pdu
    if priv_proto not in ("", "none"):
        if priv_proto not in ("aes", "aes128"):
            raise ValueError(f"unknown SNMPv3 priv protocol {priv_proto!r}")
        flags |= 0x02
        import struct
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
            try:  # cryptography >= 49 keeps CFB under decrepit
                from cryptography.hazmat.decrepit.ciphers.modes import CFB
            except ImportError:
                from cryptography.hazmat.primitives.ciphers.modes import CFB
        except ImportError as exc:
            raise ValueError(
                "SNMPv3 authPriv needs the cryptography package "
                "(pip install \"otitbup[crypto]\")") from exc
        priv_key = usm_localized_key(priv_pass, engine_id, hash_name)[:16]
        # RFC 3826 §3.1.2.1: the AES-CFB IV is boots||time||salt, and the
        # 8-byte salt MUST NOT repeat for a given key. Deriving it from the
        # per-process request_id counter reused the keystream after any
        # restart (boots/time reset); a random salt per trap is unique
        # regardless of restarts or a racing counter.
        priv_params = os.urandom(8)
        iv = struct.pack(">II", boots, etime) + priv_params
        cipher = Cipher(algorithms.AES(priv_key), CFB(iv))
        enc = cipher.encryptor()
        msg_data = _tlv(0x04, enc.update(scoped_pdu) + enc.finalize())

    def assemble(auth_params: bytes) -> bytes:
        usm = _tlv(0x30, (
            _tlv(0x04, engine_id)
            + _ber_int(boots) + _ber_int(etime)
            + _tlv(0x04, username.encode())
            + _tlv(0x04, auth_params)
            + _tlv(0x04, priv_params)
        ))
        header = _tlv(0x30, (
            _ber_int(request_id)                 # msgID
            + _ber_int(65507)                    # msgMaxSize
            + _tlv(0x04, bytes([flags]))         # msgFlags (not reportable)
            + _ber_int(3)                        # msgSecurityModel: USM
        ))
        return _tlv(0x30, _ber_int(3) + header + _tlv(0x04, usm) + msg_data)

    if not auth_len:
        return assemble(b"")
    import hmac as _hmac
    auth_key = usm_localized_key(auth_pass, engine_id, hash_name)
    # RFC 3414: HMAC over the whole message with the auth field zeroed,
    # then the truncated MAC replaces the zeros. Same length -> the
    # message structure is identical, so rebuilding is safe.
    zeroed = assemble(b"\x00" * auth_len)
    mac = _hmac.new(auth_key, zeroed, hash_name).digest()[:auth_len]
    return assemble(mac)


def _read_key_file(path) -> str:
    from pathlib import Path
    return Path(path).read_text().strip()


def build_trap(cfg: dict[str, Any], event: Event, request_id: int,
               uptime_ticks: int) -> bytes:
    """Build a trap datagram in the configured version (events.snmp_trap:
    version: v1 | v2c (default) | v3)."""
    version = str(cfg.get("version", "v2c")).lower()
    enterprise_oid = cfg.get("enterprise_oid", "1.3.6.1.4.1.99999")
    if version in ("v1", "1"):
        return build_snmpv1_trap(
            community=cfg.get("community", "public"),
            enterprise_oid=enterprise_oid, event=event,
            uptime_ticks=uptime_ticks,
            agent_addr=str(cfg.get("agent_addr", "0.0.0.0")),
        )
    if version in ("v3", "3"):
        v3 = dict(cfg.get("v3") or {})
        # Passphrases come from files (kept out of the YAML) unless given
        # inline (tests / secrets templating).
        if not v3.get("auth_key") and v3.get("auth_key_file"):
            v3["auth_key"] = _read_key_file(v3["auth_key_file"])
        if not v3.get("priv_key") and v3.get("priv_key_file"):
            v3["priv_key"] = _read_key_file(v3["priv_key_file"])
        return build_snmpv3_trap(
            v3, enterprise_oid, event, request_id, uptime_ticks)
    if version in ("v2c", "v2", "2c", "2"):
        return build_snmpv2_trap(
            community=cfg.get("community", "public"),
            enterprise_oid=enterprise_oid, event=event,
            request_id=request_id, uptime_ticks=uptime_ticks,
        )
    raise ValueError(f"unknown snmp_trap.version {version!r} "
                     "(use v1, v2c or v3)")


# ------------------------------------------------------------- EventBus

class EventBus:
    def __init__(
        self, cfg: dict[str, Any] | None = None, runstore=None,
        tickets: dict[str, Any] | None = None,
    ):
        self.cfg = cfg or {}
        self.runstore = runstore
        self._start = time.monotonic()
        self._request_id = 0
        # emit() runs concurrently from the backup thread pool and the web
        # server's handler threads; guard the trap request-id counter.
        self._id_lock = threading.Lock()
        # Optional in-process sink for live UI streaming (SSE). The web UI
        # attaches a Broadcaster here; None keeps the CLI path allocation-free.
        self.broadcaster: Broadcaster | None = None
        from .tickets import TicketManager
        self.tickets = TicketManager(tickets)

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
        self._to_event_log(event)
        if self.broadcaster is not None:
            try:
                self.broadcaster.publish(event)
            except Exception as exc:
                log.debug("broadcast sink failed: %s", exc)
        if self.cfg.get("syslog"):
            self._to_syslog(event)
        if self.cfg.get("snmp_trap"):
            self._to_snmp(event)
        if self.tickets.wants(event.type):
            self.tickets.open_ticket(
                event.type, event.message, event.detail or event.message
            )

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

    def _to_event_log(self, event: Event) -> None:
        """Persist the full event message so the web UI can show a durable
        operational log (errors shown to the user, backups, config reloads,
        …), independent of the short-detail audit chain."""
        if self.runstore is None:
            return
        try:
            self.runstore.record_event(
                time.time(), event.type, event.message,
                severity=event.severity, actor=event.actor,
                detail=event.detail,
            )
        except Exception as exc:
            log.debug("event-log sink failed: %s", exc)

    def _to_syslog(self, event: Event) -> None:
        cfg = self.cfg["syslog"]
        facility = _FACILITIES.get(cfg.get("facility", "local0"), 16)
        level = _SYSLOG_LEVEL.get(event.severity, 6)
        protocol = str(cfg.get("protocol", "udp")).lower()
        address = cfg.get("address", "127.0.0.1")
        port = int(cfg.get("port", 514))
        message = (f"{event.type} {event.message}"
                   + (f" actor={event.actor}" if event.actor else ""))
        try:
            if protocol == "tls":
                self._syslog_tls(cfg, address, port, facility, level, message)
                return
            socktype = (socket.SOCK_STREAM if protocol == "tcp"
                        else socket.SOCK_DGRAM)
            handler = SysLogHandler(
                address=(address, port), facility=facility, socktype=socktype)
            record = logging.LogRecord(
                "otitbup", logging.INFO, "", 0, message, None, None)
            record.levelno = level  # map to syslog severity
            handler.emit(record)
            handler.close()
        except Exception as exc:
            log.warning("syslog sink failed: %s", exc)

    def _syslog_tls(self, cfg, address, port, facility, level, message):
        """Send one syslog message over TLS (RFC 5425), octet-framed
        (RFC 6587). Uses a verified TLS context by default; set
        syslog.cafile for a private CA, or syslog.verify: false to skip
        verification (not recommended)."""
        import ssl
        pri = facility * 8 + level
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        host = socket.gethostname()
        # RFC 5424 header + message; RFC 6587 octet counting for stream.
        line = f"<{pri}>1 {stamp} {host} otitbup - - - {message}"
        frame = f"{len(line.encode())} {line}".encode()
        if cfg.get("verify") is False:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx = ssl.create_default_context(cafile=cfg.get("cafile") or None)
        with socket.create_connection((address, port), timeout=10) as raw:
            with ctx.wrap_socket(raw, server_hostname=address) as tls:
                tls.sendall(frame)

    def _to_snmp(self, event: Event) -> None:
        cfg = self.cfg["snmp_trap"]
        with self._id_lock:
            self._request_id = (self._request_id + 1) & 0x7FFFFFFF
            request_id = self._request_id or 1
        # sysUpTime is a 32-bit TimeTicks; wrap so a long-lived daemon
        # (>497 days) never emits a 5-byte value strict receivers reject.
        uptime = int((time.monotonic() - self._start) * 100) & 0xFFFFFFFF
        try:
            datagram = build_trap(
                cfg, event, request_id=request_id,
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
