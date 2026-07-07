"""Tests the EtherNet/IP ListIdentity driver against a real TCP server."""
import socketserver
import struct
import threading

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device

_IDENTITY = {
    "vendor_id": 47,
    "device_type": 0x0E,
    "product_code": 1337,
    "major": 1, "minor": 49,
    "status": 0x0030,
    "serial": 0x00C0FFEE,
    "name": b"NX102-9000",
    "state": 3,
}


def _identity_item():
    ident = _IDENTITY
    sockaddr = struct.pack(">HH4s8s", 2, 44818, bytes(4), bytes(8))
    data = (
        struct.pack("<H", 1)             # encapsulation protocol version
        + sockaddr
        + struct.pack(
            "<HHHBBHI",
            ident["vendor_id"], ident["device_type"], ident["product_code"],
            ident["major"], ident["minor"], ident["status"], ident["serial"],
        )
        + bytes([len(ident["name"])]) + ident["name"]
        + bytes([ident["state"]])
    )
    return struct.pack("<HH", 0x000C, len(data)) + data


class _ENIPDevice(socketserver.BaseRequestHandler):
    def handle(self):
        request = self.request.recv(1024)
        command = struct.unpack("<H", request[:2])[0]
        if command != 0x0063:
            return
        body = struct.pack("<H", 1) + _identity_item()
        header = struct.pack(
            "<HHII8sI", 0x0063, len(body), 0, self.server.enc_status,
            request[12:20], 0,
        )
        self.request.sendall(header + body)


@pytest.fixture
def enip_device():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ENIPDevice)
    server.enc_status = 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _device(port, driver="generic_enip"):
    return Device(
        name="enip-01", driver=driver, site="plant-a", zone="cell-8",
        address="127.0.0.1", options={"port": port},
    )


def test_reads_cip_identity(enip_device):
    port = enip_device.server_address[1]
    artifacts = get_driver("generic_enip").collect(_device(port), None)
    by_name = {a.name: a for a in artifacts}
    identity = yaml.safe_load(by_name["identity.yml"].data)
    assert identity["product_name"] == "NX102-9000"
    assert identity["vendor"] == "Omron Corporation"
    assert identity["device_type"] == "Programmable Logic Controller"
    assert identity["revision"] == "1.49"
    assert identity["serial_number"] == "0x00c0ffee"
    assert yaml.safe_load(by_name["fingerprint.yml"].data)["identity_sha256"]


def test_status_and_state_excluded_from_fingerprint(enip_device):
    port = enip_device.server_address[1]
    driver = get_driver("generic_enip")
    first = {a.name: a.data for a in driver.collect(_device(port), None)}
    _IDENTITY["status"], _IDENTITY["state"] = 0x0070, 6  # run-mode change
    try:
        second = {a.name: a.data for a in driver.collect(_device(port), None)}
    finally:
        _IDENTITY["status"], _IDENTITY["state"] = 0x0030, 3
    assert first["identity.yml"] != second["identity.yml"]
    assert first["fingerprint.yml"] == second["fingerprint.yml"]


def test_firmware_change_alters_fingerprint(enip_device):
    port = enip_device.server_address[1]
    driver = get_driver("generic_enip")
    first = {a.name: a.data for a in driver.collect(_device(port), None)}
    _IDENTITY["minor"] = 50
    try:
        second = {a.name: a.data for a in driver.collect(_device(port), None)}
    finally:
        _IDENTITY["minor"] = 49
    assert first["fingerprint.yml"] != second["fingerprint.yml"]


def test_pacsystems_aliases(enip_device):
    port = enip_device.server_address[1]
    for name in ("ge_pacsystems", "emerson_pacsystems"):
        artifacts = get_driver(name).collect(_device(port, driver=name), None)
        identity = yaml.safe_load(
            {a.name: a for a in artifacts}["identity.yml"].data
        )
        assert identity["product_name"] == "NX102-9000"


def test_encapsulation_error_is_driver_error(enip_device):
    enip_device.enc_status = 0x0001
    port = enip_device.server_address[1]
    with pytest.raises(DriverError, match="encapsulation error"):
        get_driver("generic_enip").collect(_device(port), None)


def test_connection_refused_is_driver_error():
    with pytest.raises(DriverError, match="connection failed"):
        get_driver("generic_enip").collect(_device(1), None)
