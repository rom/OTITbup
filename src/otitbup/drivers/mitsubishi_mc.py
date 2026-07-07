"""Mitsubishi MELSEC driver via MC protocol (3E binary frame), stdlib-only.

Program upload is not possible over MC protocol — GX Works2/3 project
exports are versioned with generic_file. What the open protocol provides,
and what this driver captures, is the CPU identity:

- cpu_info.yml (metadata): CPU model name and model code from the
  "CPU model name read" command (0x0101)
- fingerprint.yml (metadata): sha256 over the identity

Works with Q, L, iQ-R and iQ-F series CPUs (built-in Ethernet or Ethernet
module) with an MC-protocol connection configured for binary 3E frames.

    options:
      port: 5007         # match the port configured on the CPU/module
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


def build_cpu_model_request() -> bytes:
    """3E binary frame, command 0x0101 (CPU model name read)."""
    body = struct.pack(
        "<HHH",
        0x0010,   # monitoring timer (x 250 ms)
        0x0101,   # command: CPU model name read
        0x0000,   # subcommand
    )
    return (
        struct.pack(
            "<HBBHB",
            0x0050,   # subheader: request, 3E binary
            0x00,     # network number
            0xFF,     # PC number
            0x03FF,   # request destination module I/O (own station CPU)
            0x00,     # request destination module station
        )
        + struct.pack("<H", len(body))
        + body
    )


class MitsubishiMCDriver(Driver):
    name = "mitsubishi_mc"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        try:
            with socket.create_connection(
                (device.address, int(options.get("port", 5007))),
                timeout=float(options.get("timeout", 10)),
            ) as sock:
                sock.sendall(build_cpu_model_request())
                header = _recv_exact(sock, 9)
                subheader, _net, _pc, _io, _station = struct.unpack(
                    "<HBBHB", header[:7]
                )
                if subheader != 0x00D0:
                    raise DriverError(
                        f"unexpected MC response subheader 0x{subheader:04x}"
                    )
                length = struct.unpack("<H", header[7:9])[0]
                payload = _recv_exact(sock, length)
        except DriverError:
            raise
        except OSError as exc:
            raise DriverError(
                f"{device.qualified_name}: MC protocol connection failed: "
                f"{exc}"
            ) from exc

        end_code = struct.unpack("<H", payload[:2])[0]
        if end_code != 0:
            raise DriverError(
                f"{device.qualified_name}: MC protocol error, end code "
                f"0x{end_code:04x}"
            )
        data = payload[2:]
        if len(data) < 18:
            raise DriverError(
                f"{device.qualified_name}: short CPU model response"
            )
        info = {
            "cpu_model": data[:16].decode(errors="replace").strip("\x00 "),
            "cpu_model_code": f"0x{struct.unpack('<H', data[16:18])[0]:04x}",
        }

        info_yaml = yaml.safe_dump(info, sort_keys=True).encode()
        fingerprint = yaml.safe_dump(
            {"cpu_sha256": hashlib.sha256(info_yaml).hexdigest()},
            sort_keys=True,
        ).encode()
        return [
            Artifact(name="cpu_info.yml", data=info_yaml, kind="metadata"),
            Artifact(name="fingerprint.yml", data=fingerprint, kind="metadata"),
        ]
