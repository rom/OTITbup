"""Omron PLC driver via FINS/TCP, stdlib-only.

Covers CJ/CS/CP series and NJ/NX controllers with the FINS service
enabled. Program upload needs Sysmac
Studio / CX-Programmer, so projects are versioned with generic_file. Over
FINS this driver captures the controller identity:

- controller_info.yml (metadata): controller model and firmware version
  from CPU UNIT DATA READ (05 01)
- fingerprint.yml (metadata): sha256 over the identity

    options:
      port: 9600
      timeout: 10
"""
from __future__ import annotations

import hashlib
import socket
import struct
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("connection closed mid-response")
        data += chunk
    return data


def _fins_tcp(sock: socket.socket, command: int, body: bytes) -> bytes:
    """Send one FINS/TCP frame and return the response body."""
    sock.sendall(
        b"FINS"
        + struct.pack(">III", 8 + len(body), command, 0)
        + body
    )
    magic = _recv_exact(sock, 4)
    if magic != b"FINS":
        raise DriverError("bad FINS/TCP response magic")
    length, resp_command, error = struct.unpack(">III", _recv_exact(sock, 12))
    if error != 0:
        raise DriverError(f"FINS/TCP error code {error} (command {resp_command})")
    return _recv_exact(sock, length - 8)


class OmronFINSDriver(Driver):
    name = "omron_fins"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        try:
            with socket.create_connection(
                (device.address, int(options.get("port", 9600))),
                timeout=float(options.get("timeout", 10)),
            ) as sock:
                # Handshake: request an auto-assigned client node address.
                nodes = _fins_tcp(sock, 0, struct.pack(">I", 0))
                if len(nodes) < 8:
                    raise DriverError("short FINS/TCP handshake response")
                client_node, server_node = struct.unpack(">II", nodes[:8])

                fins = (
                    bytes([
                        0x80, 0x00, 0x02,          # ICF, RSV, GCT
                        0x00, server_node, 0x00,   # DNA, DA1, DA2
                        0x00, client_node, 0x00,   # SNA, SA1, SA2
                        0x00,                      # SID
                        0x05, 0x01,                # CPU UNIT DATA READ
                        0x00,                      # param: all data
                    ])
                )
                response = _fins_tcp(sock, 2, fins)
        except DriverError:
            raise
        except OSError as exc:
            raise DriverError(
                f"{device.qualified_name}: FINS connection failed: {exc}"
            ) from exc

        if len(response) < 14 or response[10:12] != b"\x05\x01":
            raise DriverError(
                f"{device.qualified_name}: unexpected FINS response"
            )
        mres, sres = response[12], response[13]
        if mres != 0 or sres != 0:
            raise DriverError(
                f"{device.qualified_name}: FINS end code "
                f"{mres:02x}:{sres:02x}"
            )
        data = response[14:]
        if len(data) < 40:
            raise DriverError(
                f"{device.qualified_name}: short CPU UNIT DATA response"
            )
        info = {
            "controller_model":
                data[:20].decode(errors="replace").strip("\x00 "),
            "controller_version":
                data[20:40].decode(errors="replace").strip("\x00 "),
        }

        info_yaml = yaml.safe_dump(info, sort_keys=True).encode()
        fingerprint = yaml.safe_dump(
            {"controller_sha256": hashlib.sha256(info_yaml).hexdigest()},
            sort_keys=True,
        ).encode()
        return [
            Artifact(
                name="controller_info.yml", data=info_yaml, kind="metadata"
            ),
            Artifact(name="fingerprint.yml", data=fingerprint, kind="metadata"),
        ]
