"""SNMP protocol + fingerprint driver + a live UDP agent."""
import socket
import threading

import pytest
import yaml

from otitbup import snmp
from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device


def test_ber_roundtrip_oid():
    oid = "1.3.6.1.2.1.1.1.0"
    enc = snmp._enc_oid(oid)
    assert enc[0] == 0x06
    assert snmp._decode_oid(enc[2:]) == oid


def test_build_get_structure():
    pdu = snmp.build_get("public", [snmp.SYS_DESCR], request_id=5)
    assert pdu[0] == 0x30
    assert b"public" in pdu
    assert b"\xa0" in pdu           # GetRequest-PDU tag


class _FakeAgent(threading.Thread):
    """A tiny SNMP responder that answers any GET with canned values."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.values = {
            snmp.SYS_DESCR: (0x04, b"Cisco IOS Software, C2960"),
            snmp.SYS_NAME: (0x04, b"core-sw-01"),
            snmp.SYS_UPTIME: (0x43, (12345).to_bytes(3, "big")),
        }

    def run(self):
        self.sock.settimeout(5)
        try:
            _data, addr = self.sock.recvfrom(65535)
        except OSError:
            return
        # A fake agent: reply with every canned value (ignoring the exact
        # request), which is all the driver needs.
        self.sock.sendto(self._response(), addr)

    def _response(self):
        varbinds = b""
        for oid, (tag, body) in sorted(self.values.items()):
            varbinds += snmp._tlv(
                0x30, snmp._enc_oid(oid) + snmp._tlv(tag, body))
        pdu = snmp._tlv(
            0xA2,
            snmp._enc_int(1) + snmp._enc_int(0) + snmp._enc_int(0)
            + snmp._tlv(0x30, varbinds))
        return snmp._tlv(
            0x30, snmp._enc_int(1) + snmp._tlv(0x04, b"public") + pdu)


@pytest.fixture
def agent():
    a = _FakeAgent()
    a.start()
    yield a


def test_snmp_get_decodes_values(agent):
    values = snmp.snmp_get(
        "127.0.0.1", [snmp.SYS_DESCR, snmp.SYS_NAME], port=agent.port)
    assert values[snmp.SYS_DESCR].startswith("Cisco IOS")
    assert values[snmp.SYS_NAME] == "core-sw-01"


def test_snmp_fingerprint_driver(agent):
    device = Device(
        name="sw", driver="snmp_fingerprint", site="s", zone="net",
        address="127.0.0.1", options={"port": agent.port},
    )
    artifacts = get_driver("snmp_fingerprint").collect(device, None)
    by_name = {a.name: a for a in artifacts}
    info = yaml.safe_load(by_name["snmp_system.yml"].data)
    assert info["sysDescr"].startswith("Cisco IOS")
    assert info["sysName"] == "core-sw-01"
    assert yaml.safe_load(by_name["fingerprint.yml"].data)["snmp_sha256"]


def test_snmp_fingerprint_excludes_uptime(agent):
    device = Device(
        name="sw", driver="snmp_fingerprint", site="s", zone="net",
        address="127.0.0.1", options={"port": agent.port},
    )
    driver = get_driver("snmp_fingerprint")
    first = {a.name: a.data for a in driver.collect(device, None)}
    # Change only uptime; fingerprint must be stable.
    agent2 = _FakeAgent()
    agent2.values[snmp.SYS_UPTIME] = (0x43, (99999).to_bytes(3, "big"))
    agent2.start()
    device2 = Device(name="sw", driver="snmp_fingerprint", site="s",
                     zone="net", address="127.0.0.1",
                     options={"port": agent2.port})
    second = {a.name: a.data for a in driver.collect(device2, None)}
    assert first["fingerprint.yml"] == second["fingerprint.yml"]


def test_snmp_no_response_is_driver_error():
    device = Device(
        name="sw", driver="snmp_fingerprint", site="s", zone="net",
        address="127.0.0.1", options={"port": 9, "timeout": 0.3},
    )
    with pytest.raises(DriverError):
        get_driver("snmp_fingerprint").collect(device, None)
