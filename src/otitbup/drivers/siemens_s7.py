"""Siemens S7 driver (python-snap7), read-only.

Captures, in descending fidelity (docs/REQUIREMENTS.md section 2):

1. Program blocks as binary (MC7) via block upload — S7-300/400 and
   unprotected CPUs. Stored under blocks/<TYPE>_<num>.mc7.
2. CPU identification: module/order code, serial, firmware, CPU state
   (cpu_info.yml).
3. A program fingerprint (fingerprint.yml): sha256 over all uploaded
   blocks, so change is detected even when nobody reads the binaries.

S7-1200/1500 and access-protected CPUs typically refuse block upload; the
driver then still produces cpu_info.yml + fingerprint of whatever was
readable, and records what failed in collection_notes rather than failing
the whole backup.

    options:
      rack: 0            # default 0
      slot: 2            # 2 for S7-300/400, usually 1 for S7-1200/1500
      port: 102
      upload_blocks: true
      block_types: [OB, FB, FC, DB, SDB]

Credentials are not used by S7comm; pass none.
"""
from __future__ import annotations

import hashlib
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError

_DEFAULT_BLOCK_TYPES = ["OB", "FB", "FC", "DB", "SDB"]
_LIST_BUFFER = 8192


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode(errors="replace").strip("\x00 ")
    return str(value)


def _fields(obj: Any, names: list[str]) -> dict[str, str]:
    out = {}
    for name in names:
        if hasattr(obj, name):
            out[name] = _text(getattr(obj, name))
    return out


def _block_type(snap7_mod: Any, name: str) -> Any:
    """Resolve a block-type constant across python-snap7 versions."""
    for attr in ("type", "types"):
        container = getattr(snap7_mod, attr, None)
        if container is None:
            continue
        enum = getattr(container, "Block", None)
        if enum is not None and hasattr(enum, name):
            return getattr(enum, name)
        table = getattr(container, "block_types", None)
        if table is not None and name in table:
            return table[name]
    raise DriverError(f"snap7: unknown block type {name!r}")


class SiemensS7Driver(Driver):
    name = "siemens_s7"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        try:
            import snap7
        except ImportError as exc:
            raise DriverError(
                "siemens_s7 requires python-snap7 "
                "(pip install otitbup[siemens])"
            ) from exc

        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")

        options = device.options
        client = snap7.client.Client()
        notes: list[str] = []
        try:
            client.connect(
                device.address,
                int(options.get("rack", 0)),
                int(options.get("slot", 2)),
                int(options.get("port", 102)),
            )
        except Exception as exc:
            raise DriverError(
                f"{device.qualified_name}: S7 connect failed: {exc}"
            ) from exc

        try:
            artifacts = [self._cpu_info(client, notes)]
            blocks: list[Artifact] = []
            if options.get("upload_blocks", True):
                blocks = self._upload_blocks(
                    snap7, client,
                    options.get("block_types") or _DEFAULT_BLOCK_TYPES,
                    notes,
                )
                artifacts.extend(blocks)
            artifacts.append(self._fingerprint(blocks, notes))
            return artifacts
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def _cpu_info(self, client: Any, notes: list[str]) -> Artifact:
        info: dict[str, Any] = {}
        try:
            cpu = client.get_cpu_info()
            info.update(_fields(cpu, [
                "ModuleTypeName", "SerialNumber", "ASName",
                "ModuleName", "Copyright",
            ]))
        except Exception as exc:
            notes.append(f"get_cpu_info failed: {exc}")
        try:
            order = client.get_order_code()
            info["OrderCode"] = _text(getattr(order, "OrderCode", ""))
            version = [getattr(order, f"V{i}", None) for i in (1, 2, 3)]
            if all(v is not None for v in version):
                info["FirmwareVersion"] = ".".join(str(v) for v in version)
        except Exception as exc:
            notes.append(f"get_order_code failed: {exc}")
        try:
            info["CpuState"] = _text(client.get_cpu_state())
        except Exception as exc:
            notes.append(f"get_cpu_state failed: {exc}")
        return Artifact(
            name="cpu_info.yml",
            data=yaml.safe_dump(info, sort_keys=True).encode(),
            kind="metadata",
        )

    def _upload_blocks(
        self, snap7_mod: Any, client: Any, type_names: list[str],
        notes: list[str],
    ) -> list[Artifact]:
        artifacts = []
        for type_name in type_names:
            try:
                block_type = _block_type(snap7_mod, type_name)
                numbers = client.list_blocks_of_type(block_type, _LIST_BUFFER)
            except Exception as exc:
                notes.append(f"list {type_name} blocks failed: {exc}")
                continue
            for number in sorted(int(n) for n in numbers):
                try:
                    data = client.full_upload(block_type, number)
                    if isinstance(data, tuple):  # (buffer, size) variants
                        data = bytes(data[0][: data[1]])
                    artifacts.append(
                        Artifact(
                            name=f"blocks/{type_name}_{number}.mc7",
                            data=bytes(data),
                            kind="logic",
                        )
                    )
                except Exception as exc:
                    notes.append(
                        f"upload {type_name}{number} failed: {exc}"
                    )
        if not artifacts and type_names:
            notes.append(
                "no blocks uploaded (CPU may be access-protected or an "
                "S7-1200/1500); falling back to metadata + fingerprint"
            )
        return artifacts

    def _fingerprint(
        self, blocks: list[Artifact], notes: list[str]
    ) -> Artifact:
        digest = hashlib.sha256()
        for block in sorted(blocks, key=lambda a: a.name):
            digest.update(block.name.encode())
            digest.update(block.data)
        fingerprint = {
            "block_count": len(blocks),
            "program_sha256": digest.hexdigest() if blocks else None,
            "upload_complete": not any("failed" in n for n in notes),
        }
        if notes:
            # All degradations (cpu info and block upload) end up here so a
            # partially-collected backup is visible in the diff.
            fingerprint["collection_notes"] = list(notes)
        return Artifact(
            name="fingerprint.yml",
            data=yaml.safe_dump(fingerprint, sort_keys=True).encode(),
            kind="metadata",
        )
