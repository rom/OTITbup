"""Tests the Omron FINS driver against a real FINS/TCP server."""
import socketserver
import struct
import threading

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device

_MODEL = b"NJ501-1300".ljust(20, b"\x00")
_VERSION = b"V1.49".ljust(20, b"\x00")


def _tcp_frame(command, body):
    return b"FINS" + struct.pack(">III", 8 + len(body), command, 0) + body


class _OmronPLC(socketserver.BaseRequestHandler):
    def _read_frame(self):
        header = self.request.recv(16)
        if len(header) < 16:
            return None, None
        length, command, _error = struct.unpack(">III", header[4:16])
        body = self.request.recv(length - 8) if length > 8 else b""
        return command, body

    def handle(self):
        # Handshake: assign client node 11, server node 1.
        command, _ = self._read_frame()
        if command != 0:
            return
        self.request.sendall(_tcp_frame(1, struct.pack(">II", 11, 1)))

        command, body = self._read_frame()
        if command != 2:
            return
        # body: 10-byte FINS header + MRC/SRC + param
        fins_header, mrc, src = body[:10], body[10], body[11]
        response = (
            bytes([0xC0]) + fins_header[1:10]   # response ICF, mirrored route
            + bytes([mrc, src])
            + bytes(self.server.end_code)
            + _MODEL + _VERSION + b"\x00" * 40
        )
        self.request.sendall(_tcp_frame(2, response))


@pytest.fixture
def omron():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _OmronPLC)
    server.end_code = (0, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _device(port):
    return Device(
        name="plc-omr", driver="omron_fins", site="plant-a", zone="cell-5",
        address="127.0.0.1", options={"port": port},
    )


def test_reads_controller_identity(omron):
    port = omron.server_address[1]
    artifacts = get_driver("omron_fins").collect(_device(port), None)
    by_name = {a.name: a for a in artifacts}
    info = yaml.safe_load(by_name["controller_info.yml"].data)
    assert info["controller_model"] == "NJ501-1300"
    assert info["controller_version"] == "V1.49"
    assert yaml.safe_load(by_name["fingerprint.yml"].data)["controller_sha256"]


def test_fins_end_code_is_driver_error(omron):
    omron.end_code = (0x01, 0x01)
    port = omron.server_address[1]
    with pytest.raises(DriverError, match="end code"):
        get_driver("omron_fins").collect(_device(port), None)


def test_connection_refused_is_driver_error():
    with pytest.raises(DriverError, match="connection failed"):
        get_driver("omron_fins").collect(_device(1), None)
