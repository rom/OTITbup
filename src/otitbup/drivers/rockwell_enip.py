"""Rockwell / Allen-Bradley Logix driver (pycomm3), read-only.

The .ACD project cannot be uploaded over EtherNet/IP — Studio 5000 exports
should be versioned with the generic_file driver. What CAN be read over the
wire, and is captured here:

- controller_info.yml (metadata): product name/code, revision, serial,
  keyswitch position, controller name, program and task names
- tags.yml (config): the controller tag list (name, data type, dimensions)
  — changes whenever the program structure changes
- fingerprint.yml (metadata): sha256 over tags + programs + revision, a
  reliable change signal even without the project file

    options:
      slot: 0              # backplane slot of the controller
      include_tags: true

Credentials are not used by EtherNet/IP; pass none.
"""
from __future__ import annotations

import hashlib
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError

_INFO_FIELDS = [
    "name", "product_name", "product_code", "product_type",
    "vendor", "serial", "revision", "keyswitch",
]


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return value.decode(errors="replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _reduce_tags(tags: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tag in tags or []:
        name = tag.get("tag_name") or tag.get("name")
        if not name:
            continue
        entry: dict[str, Any] = {
            "data_type": tag.get("data_type_name")
                         or _plain(tag.get("data_type")),
        }
        if tag.get("dim"):
            entry["dim"] = _plain(tag.get("dimensions") or tag["dim"])
        if tag.get("external_access"):
            entry["external_access"] = tag["external_access"]
        out[str(name)] = entry
    return dict(sorted(out.items()))


class RockwellENIPDriver(Driver):
    name = "rockwell_enip"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        try:
            from pycomm3 import LogixDriver
        except ImportError as exc:
            raise DriverError(
                "rockwell_enip requires pycomm3 "
                "(pip install 'otitbup[rockwell]')"
            ) from exc

        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")

        options = device.options
        path = f"{device.address}/{int(options.get('slot', 0))}"
        try:
            with LogixDriver(path) as plc:
                info = dict(plc.info or {})
                tags = (
                    plc.get_tag_list()
                    if options.get("include_tags", True) else []
                )
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(
                f"{device.qualified_name}: EtherNet/IP collection failed: {exc}"
            ) from exc

        controller = {
            k: _plain(info[k]) for k in _INFO_FIELDS if k in info
        }
        revision = controller.get("revision")
        if isinstance(revision, dict):
            controller["revision"] = (
                f"{revision.get('major', '?')}.{revision.get('minor', '?')}"
            )
        for group in ("programs", "tasks"):
            value = info.get(group)
            if value:
                controller[group] = sorted(str(n) for n in value)

        reduced = _reduce_tags(tags)
        program_view = yaml.safe_dump(
            {
                "programs": controller.get("programs", []),
                "revision": controller.get("revision"),
                "tags": reduced,
            },
            sort_keys=True,
        ).encode()
        fingerprint = {
            "tag_count": len(reduced),
            "program_sha256": hashlib.sha256(program_view).hexdigest(),
        }

        artifacts = [
            Artifact(
                name="controller_info.yml",
                data=yaml.safe_dump(controller, sort_keys=True).encode(),
                kind="metadata",
            ),
            Artifact(
                name="fingerprint.yml",
                data=yaml.safe_dump(fingerprint, sort_keys=True).encode(),
                kind="metadata",
            ),
        ]
        if options.get("include_tags", True):
            artifacts.insert(1, Artifact(
                name="tags.yml",
                data=yaml.safe_dump(reduced, sort_keys=True).encode(),
                kind="config",
            ))
        return artifacts
