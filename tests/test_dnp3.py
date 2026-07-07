"""Tests the DNP3 driver against a real TCP server speaking canned
frames. The test builds frames with its own independent CRC code so the
driver's CRC is externally checked, not self-confirmed."""
import socketserver
import struct
import threading

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.drivers.generic_dnp3 import dnp3_crc
from otitbup.models import Device

_ATTRS = {
    252: b"Schneider Electric",
    250: b"SCADAPack 474",
    248: b"SN-00042",
    242: b"fw 8.15.1",
}


def _crc(data):  # independent CRC-16/DNP implementation for the test
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
    return (~crc) & 0xFFFF


def _frame(dst, src, payload, ctrl):
    header = struct.pack("<BBBBHH", 0x05, 0x64, 5 + len(payload), ctrl, dst, src)
    out = header + struct.pack("<H", _crc(header))
    for offset in range(0, len(payload), 16):
        block = payload[offset:offset + 16]
        out += block + struct.pack("<H", _crc(block))
    return out


def _attribute_response(unsolicited_first=False):
    body = bytes([0xC0, 0x81, 0x00, 0x00])  # app ctrl, FC response, IIN
    for var, value in sorted(_ATTRS.items()):
        body += bytes([0x00, var, 0x00, 0x00, 0x00, 0x01, len(value)]) + value
    frames = b""
    if unsolicited_first:
        null_unsol = bytes([0xC0, 0x10, 0x82, 0x00, 0x00])
        frames += _frame(3, 1, null_unsol, 0x44)
    frames += _frame(3, 1, bytes([0xC0]) + body, 0x44)
    return frames


class _Outstation(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(4096)  # the READ request; canned reply regardless
        self.request.sendall(
            _attribute_response(self.server.unsolicited_first)
        )


@pytest.fixture
def outstation():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Outstation)
    server.unsolicited_first = False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _device(port):
    return Device(
        name="rtu-01", driver="generic_dnp3", site="plant-a", zone="rtus",
        address="127.0.0.1",
        options={"port": port, "outstation": 1, "master": 3},
    )


def test_crc_against_published_vector():
    # Documented link-reset frame: 05 64 05 C0 01 00 00 04 -> CRC E9 21.
    assert dnp3_crc(bytes.fromhex("056405C001000004")) == 0x21E9


def test_reads_device_attributes(outstation):
    port = outstation.server_address[1]
    artifacts = get_driver("generic_dnp3").collect(_device(port), None)
    by_name = {a.name: a for a in artifacts}
    attrs = yaml.safe_load(by_name["device_attributes.yml"].data)
    assert attrs["device_manufacturer_name"] == "Schneider Electric"
    assert attrs["product_name_and_model"] == "SCADAPack 474"
    assert attrs["device_serial_number"] == "SN-00042"
    fp = yaml.safe_load(by_name["fingerprint.yml"].data)
    assert fp["attribute_count"] == len(_ATTRS)


def test_skips_unsolicited_then_reads(outstation):
    outstation.unsolicited_first = True
    port = outstation.server_address[1]
    artifacts = get_driver("generic_dnp3").collect(_device(port), None)
    attrs = yaml.safe_load(
        {a.name: a for a in artifacts}["device_attributes.yml"].data
    )
    assert attrs["product_name_and_model"] == "SCADAPack 474"
    assert any(
        "unsolicited" in n for n in attrs.get("collection_notes", [])
    )


def test_fingerprint_tracks_firmware(outstation):
    port = outstation.server_address[1]
    driver = get_driver("generic_dnp3")
    first = {a.name: a.data for a in driver.collect(_device(port), None)}
    _ATTRS[242] = b"fw 8.16.0"
    try:
        second = {a.name: a.data for a in driver.collect(_device(port), None)}
    finally:
        _ATTRS[242] = b"fw 8.15.1"
    assert first["fingerprint.yml"] != second["fingerprint.yml"]


def test_connection_refused_is_driver_error():
    with pytest.raises(DriverError, match="connection failed"):
        get_driver("generic_dnp3").collect(_device(1), None)
