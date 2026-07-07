"""Generic DNP3 device-attribute fingerprint driver, stdlib-only.

Reads DNP3 group 0 device attributes (READ g0v254 = "all attributes") —
vendor, product, serial number, software/hardware versions, user-assigned
names — from any conforming outstation: Schneider SCADAPack, GE, SEL RTAC,
Kingfisher/Semaphore and most water/power RTUs. This is the fingerprint
level of the capture ladder: the RTU's full configuration stays with its
engineering-tool exports (generic_file), but any firmware or identity
change shows up here.

Minimal, read-only DNP3: one link-layer frame carrying one application
READ, no time sync, no confirmations, no writes. Unsolicited responses
that arrive first are skipped (a note is recorded). Multi-fragment
responses are reassembled.

    options:
      port: 20000
      outstation: 1        # DNP3 destination (outstation) address
      master: 3            # DNP3 source (master) address
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

_ATTRIBUTE_NAMES = {
    242: "device_manufacturer_software_version",
    243: "device_manufacturer_hardware_version",
    245: "user_assigned_location",
    246: "user_assigned_id",
    247: "user_assigned_device_name",
    248: "device_serial_number",
    249: "dnp3_subset_and_conformance",
    250: "product_name_and_model",
    252: "device_manufacturer_name",
}
_MAX_FRAMES = 32


def dnp3_crc(data: bytes) -> int:
    """CRC-16/DNP (reversed poly 0xA6BC, final complement)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
    return (~crc) & 0xFFFF


def _with_crc(block: bytes) -> bytes:
    return block + struct.pack("<H", dnp3_crc(block))


def build_frame(dst: int, src: int, payload: bytes, ctrl: int = 0xC4) -> bytes:
    """Link frame: header + CRC, then payload in 16-byte CRC'd blocks."""
    header = struct.pack("<BBBBHH", 0x05, 0x64, 5 + len(payload), ctrl, dst, src)
    frame = _with_crc(header)
    for offset in range(0, len(payload), 16):
        frame += _with_crc(payload[offset : offset + 16])
    return frame


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("connection closed mid-frame")
        data += chunk
    return data


def read_frame(sock: socket.socket) -> tuple[int, bytes]:
    """Read one link frame; returns (link_ctrl, user payload without CRCs)."""
    header = _recv_exact(sock, 10)
    if header[0] != 0x05 or header[1] != 0x64:
        raise DriverError("bad DNP3 frame start")
    if struct.unpack("<H", header[8:10])[0] != dnp3_crc(header[:8]):
        raise DriverError("link header CRC error")
    length = header[2]
    remaining = length - 5  # bytes of user data after the header
    payload = b""
    while remaining > 0:
        block_len = min(16, remaining)
        block = _recv_exact(sock, block_len + 2)
        if struct.unpack("<H", block[block_len:])[0] != dnp3_crc(block[:block_len]):
            raise DriverError("data block CRC error")
        payload += block[:block_len]
        remaining -= block_len
    return header[3], payload


def parse_attribute_objects(data: bytes) -> tuple[dict[int, Any], list[str]]:
    """Parse g0 attribute objects from an application response body."""
    attributes: dict[int, Any] = {}
    notes: list[str] = []
    pos = 0
    while pos + 3 <= len(data):
        group, variation, qualifier = data[pos], data[pos + 1], data[pos + 2]
        pos += 3
        if group != 0:
            notes.append(f"skipped non-attribute object g{group}v{variation}")
            break
        if qualifier == 0x00:      # 8-bit start/stop
            pos += 2
        elif qualifier == 0x01:    # 16-bit start/stop
            pos += 4
        else:
            notes.append(
                f"unsupported qualifier 0x{qualifier:02x} for v{variation}"
            )
            break
        if variation == 255:       # attribute-list report, skip its data
            if pos + 2 > len(data):
                break
            length = data[pos + 1]
            pos += 2 + length
            continue
        if pos + 2 > len(data):
            break
        data_type, length = data[pos], data[pos + 1]
        raw = data[pos + 2 : pos + 2 + length]
        pos += 2 + length
        if data_type == 1:                        # visible string
            value: Any = raw.decode(errors="replace").strip("\x00 ")
        elif data_type in (2, 3):                 # uint / int
            value = int.from_bytes(raw, "little", signed=(data_type == 3))
        else:
            value = raw.hex()
        attributes[variation] = value
    return attributes, notes


class GenericDNP3Driver(Driver):
    name = "generic_dnp3"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        outstation = int(options.get("outstation", 1))
        master = int(options.get("master", 3))
        timeout = float(options.get("timeout", 10))

        # Transport FIR|FIN, app FIR|FIN seq 0, READ, g0v254 all-objects.
        request = build_frame(
            outstation, master,
            bytes([0xC0, 0xC0, 0x01, 0x00, 0xFE, 0x06]),
        )

        notes: list[str] = []
        try:
            with socket.create_connection(
                (device.address, int(options.get("port", 20000))),
                timeout=timeout,
            ) as sock:
                sock.sendall(request)
                app = self._read_response(sock, notes)
        except DriverError:
            raise
        except OSError as exc:
            raise DriverError(
                f"{device.qualified_name}: DNP3 connection failed: {exc}"
            ) from exc

        iin = struct.unpack("<H", app[2:4])[0] if len(app) >= 4 else 0
        raw_attrs, parse_notes = parse_attribute_objects(app[4:])
        notes.extend(parse_notes)
        if not raw_attrs:
            raise DriverError(
                f"{device.qualified_name}: outstation returned no device "
                f"attributes ({'; '.join(notes) or 'empty response'})"
            )

        attributes: dict[str, Any] = {
            _ATTRIBUTE_NAMES.get(var, f"attribute_{var}"): value
            for var, value in sorted(raw_attrs.items())
        }
        if iin:
            attributes["iin_flags"] = f"0x{iin:04x}"
        if notes:
            attributes["collection_notes"] = notes

        attrs_yaml = yaml.safe_dump(attributes, sort_keys=True).encode()
        fingerprint = yaml.safe_dump(
            {
                "attributes_sha256": hashlib.sha256(attrs_yaml).hexdigest(),
                "attribute_count": len(raw_attrs),
            },
            sort_keys=True,
        ).encode()
        return [
            Artifact(
                name="device_attributes.yml", data=attrs_yaml, kind="metadata"
            ),
            Artifact(name="fingerprint.yml", data=fingerprint, kind="metadata"),
        ]

    def _read_response(self, sock: socket.socket, notes: list[str]) -> bytes:
        """Reassemble transport segments until a solicited response (FC
        0x81) is complete; skip unsolicited responses."""
        segments: list[bytes] = []
        for _ in range(_MAX_FRAMES):
            _ctrl, payload = read_frame(sock)
            if not payload:
                continue
            transport, body = payload[0], payload[1:]
            if transport & 0x40:  # FIR: start of a new fragment
                segments = []
            segments.append(body)
            if not transport & 0x80:  # not FIN yet
                continue
            app = b"".join(segments)
            segments = []
            if len(app) >= 2 and app[1] == 0x82:
                notes.append("skipped an unsolicited response")
                continue
            if len(app) >= 2 and app[1] == 0x81:
                return app
            notes.append(f"skipped application FC 0x{app[1]:02x}")
        raise DriverError("no solicited DNP3 response received")
