"""Tests the Mitsubishi MC-protocol driver against a real TCP server."""
import socketserver
import struct
import threading

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device

_CPU_MODEL = b"R08CPU          "  # 16 bytes, space padded
_MODEL_CODE = 0x4141


def _response(end_code=0):
    data = struct.pack("<H", end_code)
    if end_code == 0:
        data += _CPU_MODEL + struct.pack("<H", _MODEL_CODE)
    return (
        struct.pack("<HBBHB", 0x00D0, 0x00, 0xFF, 0x03FF, 0x00)
        + struct.pack("<H", len(data))
        + data
    )


class _MelsecCPU(socketserver.BaseRequestHandler):
    def handle(self):
        request = self.request.recv(4096)
        # Verify the driver sent a well-formed 3E request for cmd 0x0101.
        subheader, = struct.unpack("<H", request[:2])
        command, = struct.unpack("<H", request[11:13])
        if subheader != 0x0050 or command != 0x0101:
            self.request.sendall(_response(end_code=0xC059))
            return
        self.request.sendall(_response(self.server.end_code))


@pytest.fixture
def melsec():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _MelsecCPU)
    server.end_code = 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _device(port):
    return Device(
        name="plc-mel", driver="mitsubishi_mc", site="plant-a", zone="cell-4",
        address="127.0.0.1", options={"port": port},
    )


def test_reads_cpu_model(melsec):
    port = melsec.server_address[1]
    artifacts = get_driver("mitsubishi_mc").collect(_device(port), None)
    by_name = {a.name: a for a in artifacts}
    info = yaml.safe_load(by_name["cpu_info.yml"].data)
    assert info["cpu_model"] == "R08CPU"
    assert info["cpu_model_code"] == "0x4141"
    assert yaml.safe_load(by_name["fingerprint.yml"].data)["cpu_sha256"]


def test_plc_error_code_is_driver_error(melsec):
    melsec.end_code = 0xC050
    port = melsec.server_address[1]
    with pytest.raises(DriverError, match="0xc050"):
        get_driver("mitsubishi_mc").collect(_device(port), None)


def test_connection_refused_is_driver_error():
    with pytest.raises(DriverError, match="connection failed"):
        get_driver("mitsubishi_mc").collect(_device(1), None)
