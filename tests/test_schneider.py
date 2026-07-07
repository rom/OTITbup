"""Tests the Schneider driver against a real TCP socket speaking canned
Modbus Read Device Identification responses."""
import socketserver
import struct
import threading

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device

_OBJECTS = {
    0x00: b"Schneider Electric",
    0x01: b"BMX P34 2020",
    0x02: b"v3.20",
    0x04: b"Modicon M340",
    0x06: b"WaterTreatment_v12",
}


def _identification_response(unit_id, read_code, tid):
    objects = sorted(_OBJECTS.items())
    body = bytes([0x2B, 0x0E, read_code, 0x02, 0x00, 0x00, len(objects)])
    for obj_id, value in objects:
        body += bytes([obj_id, len(value)]) + value
    return struct.pack(">HHHB", tid, 0, len(body) + 1, unit_id) + body


class _ModbusHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            header = self.request.recv(7)
            if len(header) < 7:
                return
            tid, _proto, length, unit_id = struct.unpack(">HHHB", header)
            pdu = self.request.recv(length - 1)
            read_code = pdu[2]
            if self.server.supported_codes and read_code not in self.server.supported_codes:
                # Modbus exception: illegal data value
                body = bytes([0x2B | 0x80, 0x03])
                self.request.sendall(
                    struct.pack(">HHHB", tid, 0, len(body) + 1, unit_id) + body
                )
                continue
            self.request.sendall(
                _identification_response(unit_id, read_code, tid)
            )


@pytest.fixture
def modbus_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ModbusHandler)
    server.supported_codes = None  # all codes supported
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _device(port):
    return Device(
        name="plc-03", driver="schneider_modbus", site="plant-a", zone="cell-3",
        address="127.0.0.1", options={"port": port, "unit_id": 255},
    )


def test_collects_identification_and_fingerprint(modbus_server):
    port = modbus_server.server_address[1]
    artifacts = get_driver("schneider_modbus").collect(_device(port), None)
    by_name = {a.name: a for a in artifacts}
    assert set(by_name) == {"device_identification.yml", "fingerprint.yml"}

    ident = yaml.safe_load(by_name["device_identification.yml"].data)
    assert ident["VendorName"] == "Schneider Electric"
    assert ident["ProductCode"] == "BMX P34 2020"
    assert ident["UserApplicationName"] == "WaterTreatment_v12"

    fp = yaml.safe_load(by_name["fingerprint.yml"].data)
    assert fp["identification_sha256"]
    assert fp["object_count"] == len(_OBJECTS)


def test_unsupported_categories_are_noted_not_fatal(modbus_server):
    modbus_server.supported_codes = {0x01}
    port = modbus_server.server_address[1]
    artifacts = get_driver("schneider_modbus").collect(_device(port), None)
    ident = yaml.safe_load(
        {a.name: a for a in artifacts}["device_identification.yml"].data
    )
    assert ident["VendorName"] == "Schneider Electric"
    assert len(ident["collection_notes"]) == 2  # regular + extended refused


def test_fingerprint_tracks_application_name(modbus_server):
    port = modbus_server.server_address[1]
    driver = get_driver("schneider_modbus")
    first = {a.name: a.data for a in driver.collect(_device(port), None)}
    _OBJECTS[0x06] = b"WaterTreatment_v13"
    try:
        second = {a.name: a.data for a in driver.collect(_device(port), None)}
    finally:
        _OBJECTS[0x06] = b"WaterTreatment_v12"
    assert first["fingerprint.yml"] != second["fingerprint.yml"]


def test_connection_refused_is_driver_error():
    device = _device(1)  # nothing listens on port 1
    with pytest.raises(DriverError, match="connection failed"):
        get_driver("schneider_modbus").collect(device, None)
