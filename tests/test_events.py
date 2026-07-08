import socket
import threading
import time

import pytest

from otitbup.events import (BACKUP_START, LOGIN, EventBus, build_snmpv2_trap,
                            Event, _ber_oid)
from otitbup.runstore import RunStore


def _udp_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)
    return sock


def test_event_recorded_in_audit(tmp_path):
    rs = RunStore(tmp_path / "r.db")
    bus = EventBus({}, runstore=rs)
    bus.emit(BACKUP_START, "backup started: plant-a/x", detail="plant-a/x")
    rows = rs.recent_audit()
    assert any(r["action"] == BACKUP_START for r in rows)


def test_syslog_sink_sends_datagram(tmp_path):
    sock = _udp_receiver()
    port = sock.getsockname()[1]
    bus = EventBus(
        {"syslog": {"address": "127.0.0.1", "port": port, "facility": "local1"}}
    )
    bus.emit(LOGIN, "login: alice", actor="alice")
    data, _ = sock.recvfrom(4096)
    sock.close()
    text = data.decode(errors="replace")
    assert "login" in text and "alice" in text
    assert text.startswith("<")          # syslog priority prefix


def test_snmp_trap_sink_sends_pdu(tmp_path):
    sock = _udp_receiver()
    port = sock.getsockname()[1]
    bus = EventBus({
        "snmp_trap": {"address": "127.0.0.1", "port": port,
                      "community": "otcomm", "enterprise_oid": "1.3.6.1.4.1.4242"}
    })
    bus.emit(BACKUP_START, "backup started: dev1", detail="dev1")
    data, _ = sock.recvfrom(4096)
    sock.close()
    assert data[0] == 0x30                # SEQUENCE
    assert b"otcomm" in data             # community string
    assert b"backup started: dev1" in data
    assert b"backup.start" in data


def test_snmp_trap_encoding_structure():
    trap = build_snmpv2_trap(
        "public", "1.3.6.1.4.1.99999",
        Event("backup.start", "hello", "info"), request_id=7, uptime_ticks=1234,
    )
    # Outer SEQUENCE, then version INTEGER(1), community, and a [7] PDU.
    assert trap[0] == 0x30
    assert b"public" in trap
    assert b"\xa7" in trap               # SNMPv2-Trap-PDU context tag [7]


def test_ber_oid_encoding():
    # 1.3.6.1.2.1.1.3.0 is a well-known OID; first byte 0x2b (40*1+3).
    encoded = _ber_oid("1.3.6.1.2.1.1.3.0")
    assert encoded[0] == 0x06            # OID tag
    assert encoded[2] == 0x2B            # 40*1 + 3


def test_null_bus_is_noop(tmp_path):
    from otitbup.events import NullEventBus
    NullEventBus().emit("x", "y")        # must not raise
