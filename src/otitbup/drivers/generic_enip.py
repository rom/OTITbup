"""Generic EtherNet/IP CIP-identity driver, stdlib-only.

Sends one encapsulation ListIdentity (0x0063) over TCP and records the
CIP Identity Object every EtherNet/IP device must answer with: vendor,
device type, product code, revision, serial number, product name. One
driver fingerprints anything speaking EtherNet/IP — Omron NJ/NX, Keyence,
GE/Emerson PACSystems Ethernet interfaces, drives, I/O adapters — without
a vendor library.

- identity.yml (metadata): the identity object (status/state included
  for the record but EXCLUDED from the fingerprint — they change with
  run mode and would churn diffs)
- fingerprint.yml (metadata): sha256 over the stable identity fields

    options:
      port: 44818
      timeout: 10

Registered aliases: ge_pacsystems / emerson_pacsystems — GE (now Emerson)
PACSystems RX3i/RSTi-EP whose Ethernet interface has EtherNet/IP enabled.
Older SRTP-only CPUs have no open protocol path; version their PAC
Machine Edition project exports with generic_file.
"""
from __future__ import annotations

import hashlib
import socket
import struct
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError

_LIST_IDENTITY = 0x0063
_IDENTITY_ITEM = 0x000C

_VENDORS = {
    1: "Rockwell Automation/Allen-Bradley",
    47: "Omron Corporation",
}
_DEVICE_TYPES = {
    0x02: "AC Drive",
    0x07: "General Purpose Discrete I/O",
    0x0C: "Communications Adapter",
    0x0E: "Programmable Logic Controller",
    0x2B: "Generic Device",
}


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("connection closed mid-response")
        data += chunk
    return data


def parse_identity_item(data: bytes) -> dict[str, Any]:
    """Parse a CIP Identity item body (after the item type/length)."""
    if len(data) < 33:
        raise DriverError("short ListIdentity item")
    (vendor_id, device_type, product_code, major, minor,
     status, serial) = struct.unpack("<HHHBBHI", data[18:32])
    name_length = data[32]
    name_end = 33 + name_length
    if len(data) < name_end:
        raise DriverError("truncated product name in ListIdentity item")
    product_name = data[33:name_end].decode(errors="replace")
    identity: dict[str, Any] = {
        "vendor_id": vendor_id,
        "device_type_id": device_type,
        "product_code": product_code,
        "revision": f"{major}.{minor}",
        "serial_number": f"0x{serial:08x}",
        "product_name": product_name,
        "status": f"0x{status:04x}",
    }
    if vendor_id in _VENDORS:
        identity["vendor"] = _VENDORS[vendor_id]
    if device_type in _DEVICE_TYPES:
        identity["device_type"] = _DEVICE_TYPES[device_type]
    if len(data) > name_end:
        identity["state"] = data[name_end]
    return identity


class GenericENIPDriver(Driver):
    name = "generic_enip"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        request = struct.pack(
            "<HHII8sI", _LIST_IDENTITY, 0, 0, 0, b"otitbup\x00", 0
        )
        try:
            with socket.create_connection(
                (device.address, int(options.get("port", 44818))),
                timeout=float(options.get("timeout", 10)),
            ) as sock:
                sock.sendall(request)
                header = _recv_exact(sock, 24)
                command, length, _session, enc_status = struct.unpack(
                    "<HHII", header[:12]
                )
                if command != _LIST_IDENTITY:
                    raise DriverError(
                        f"unexpected encapsulation command 0x{command:04x}"
                    )
                if enc_status != 0:
                    raise DriverError(
                        f"encapsulation error status 0x{enc_status:08x}"
                    )
                body = _recv_exact(sock, length) if length else b""
        except DriverError:
            raise
        except OSError as exc:
            raise DriverError(
                f"{device.qualified_name}: EtherNet/IP connection failed: "
                f"{exc}"
            ) from exc

        identity = self._identity_from_body(body)
        identity["address"] = device.address

        identity_yaml = yaml.safe_dump(identity, sort_keys=True).encode()
        stable = {
            k: v for k, v in identity.items() if k not in ("status", "state")
        }
        fingerprint = yaml.safe_dump(
            {
                "identity_sha256": hashlib.sha256(
                    yaml.safe_dump(stable, sort_keys=True).encode()
                ).hexdigest(),
            },
            sort_keys=True,
        ).encode()
        return [
            Artifact(name="identity.yml", data=identity_yaml, kind="metadata"),
            Artifact(name="fingerprint.yml", data=fingerprint, kind="metadata"),
        ]

    def _identity_from_body(self, body: bytes) -> dict[str, Any]:
        if len(body) < 2:
            raise DriverError("empty ListIdentity response")
        item_count = struct.unpack("<H", body[:2])[0]
        pos = 2
        for _ in range(item_count):
            if pos + 4 > len(body):
                break
            item_type, item_length = struct.unpack("<HH", body[pos:pos + 4])
            item = body[pos + 4 : pos + 4 + item_length]
            pos += 4 + item_length
            if item_type == _IDENTITY_ITEM:
                return parse_identity_item(item)
        raise DriverError("no CIP Identity item in ListIdentity response")


class GEPACSystemsDriver(GenericENIPDriver):
    """GE (now Emerson) PACSystems RX3i / RSTi-EP via the EtherNet/IP
    identity object — requires EtherNet/IP to be enabled on the CPU's
    Ethernet interface. PAC Machine Edition project exports are versioned
    with generic_file; SRTP-only legacy CPUs are not reachable this way.
    Registered as ge_pacsystems and emerson_pacsystems."""

    name = "ge_pacsystems"
