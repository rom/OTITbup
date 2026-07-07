"""Schneider / Modicon driver via Modbus TCP, read-only, stdlib-only.

UMAS (the proprietary protocol Unity/EcoStruxure uses for program upload)
is undocumented, so program logic is versioned via engineer project exports
(generic_file driver). Over the open Modbus protocol this driver captures
Read Device Identification (function 0x2B / MEI 0x0E):

- device_identification.yml (metadata): vendor, product code, revision,
  model, and — key change signal — UserApplicationName, which Modicon CPUs
  set to the loaded project's name
- fingerprint.yml (metadata): sha256 over the identification

    options:
      port: 502
      unit_id: 255        # 255 = direct addressing (typical for Modicon
                          # CPU ports); use the bridge index when going
                          # through a gateway

Credentials are not used by Modbus; pass none.
"""
from __future__ import annotations

import hashlib
import socket
import struct
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError

_OBJECT_NAMES = {
    0x00: "VendorName",
    0x01: "ProductCode",
    0x02: "MajorMinorRevision",
    0x03: "VendorUrl",
    0x04: "ProductName",
    0x05: "ModelName",
    0x06: "UserApplicationName",
}
_READ_CODES = (0x01, 0x02, 0x03)  # basic, regular, extended
_MAX_CONTINUATIONS = 16


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("connection closed mid-response")
        data += chunk
    return data


def _read_identification(
    sock: socket.socket, unit_id: int, read_code: int
) -> dict[int, bytes]:
    """One Read Device Identification conversation, following 'more
    follows' continuations."""
    objects: dict[int, bytes] = {}
    next_object = 0x00
    for _ in range(_MAX_CONTINUATIONS):
        pdu = bytes([0x2B, 0x0E, read_code, next_object])
        header = struct.pack(">HHHB", read_code, 0, len(pdu) + 1, unit_id)
        sock.sendall(header + pdu)

        tid, proto, length, _uid = struct.unpack(">HHHB", _recv_exact(sock, 7))
        payload = _recv_exact(sock, length - 1)
        if payload[0] & 0x80:
            raise DriverError(
                f"modbus exception 0x{payload[1]:02x} for read code {read_code}"
            )
        if len(payload) < 7 or payload[0] != 0x2B or payload[1] != 0x0E:
            raise DriverError("malformed device identification response")

        more_follows, next_object, count = payload[4], payload[5], payload[6]
        pos = 7
        for _ in range(count):
            if pos + 2 > len(payload):
                raise DriverError("truncated identification object list")
            obj_id, obj_len = payload[pos], payload[pos + 1]
            objects[obj_id] = payload[pos + 2 : pos + 2 + obj_len]
            pos += 2 + obj_len
        if more_follows != 0xFF:
            break
    return objects


class SchneiderModbusDriver(Driver):
    name = "schneider_modbus"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        port = int(options.get("port", 502))
        unit_id = int(options.get("unit_id", 255))

        objects: dict[int, bytes] = {}
        notes: list[str] = []
        try:
            with socket.create_connection(
                (device.address, port), timeout=10
            ) as sock:
                for read_code in _READ_CODES:
                    try:
                        objects.update(
                            _read_identification(sock, unit_id, read_code)
                        )
                    except DriverError as exc:
                        # Regular/extended categories are optional.
                        notes.append(str(exc))
        except OSError as exc:
            raise DriverError(
                f"{device.qualified_name}: Modbus connection failed: {exc}"
            ) from exc

        if not objects:
            raise DriverError(
                f"{device.qualified_name}: device identification returned "
                f"nothing ({'; '.join(notes) or 'no categories supported'})"
            )

        identification: dict[str, str] = {}
        for obj_id, value in sorted(objects.items()):
            key = _OBJECT_NAMES.get(obj_id, f"Object_0x{obj_id:02X}")
            identification[key] = value.decode(errors="replace").strip("\x00 ")
        if notes:
            identification["collection_notes"] = notes  # type: ignore[assignment]

        ident_yaml = yaml.safe_dump(identification, sort_keys=True).encode()
        fingerprint = yaml.safe_dump(
            {
                "identification_sha256":
                    hashlib.sha256(ident_yaml).hexdigest(),
                "object_count": len(objects),
            },
            sort_keys=True,
        ).encode()
        return [
            Artifact(
                name="device_identification.yml",
                data=ident_yaml,
                kind="metadata",
            ),
            Artifact(name="fingerprint.yml", data=fingerprint, kind="metadata"),
        ]
