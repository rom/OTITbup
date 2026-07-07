"""Load and validate the YAML inventory/config file.

The config file is the source of truth for the device inventory
(REQUIREMENTS.md section 7) and is expected to be versioned in git by the
operator.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .models import AppConfig, Device, Site, Zone
from .windows import parse_interval, parse_window


class ConfigError(Exception):
    pass


def _require(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"{context}: missing required key {key!r}")
    return mapping[key]


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    data_dir = raw.get("data_dir", "./data")
    # Resolve relative to the config file so runs are cwd-independent.
    data_dir = str((path.parent / os.path.expanduser(data_dir)).resolve())

    sites: list[Site] = []
    seen_devices: set[str] = set()
    for site_raw in raw.get("sites", []) or []:
        site_name = _require(site_raw, "name", "site")
        zones: list[Zone] = []
        for zone_raw in site_raw.get("zones", []) or []:
            zone_name = _require(zone_raw, "name", f"site {site_name}: zone")
            zone = Zone(
                name=zone_name,
                site=site_name,
                maintenance_window=zone_raw.get("maintenance_window"),
                max_concurrent=int(zone_raw.get("max_concurrent", 1)),
            )
            if zone.maintenance_window:
                # Validate early: a bad window should fail at load time,
                # not silently skip backups at 3am.
                parse_window(zone.maintenance_window)
            if zone.max_concurrent < 1:
                raise ConfigError(
                    f"zone {site_name}/{zone_name}: max_concurrent must be >= 1"
                )
            for dev_raw in zone_raw.get("devices", []) or []:
                dev_name = _require(
                    dev_raw, "name", f"zone {site_name}/{zone_name}: device"
                )
                device = Device(
                    name=dev_name,
                    driver=_require(dev_raw, "driver", f"device {dev_name}"),
                    site=site_name,
                    zone=zone_name,
                    address=dev_raw.get("address"),
                    schedule=str(dev_raw.get("schedule", "1d")),
                    credentials=dev_raw.get("credentials"),
                    options=dev_raw.get("options") or {},
                )
                parse_interval(device.schedule)
                if device.qualified_name in seen_devices:
                    raise ConfigError(
                        f"duplicate device: {device.qualified_name}"
                    )
                seen_devices.add(device.qualified_name)
                zone.devices.append(device)
            zones.append(zone)
        sites.append(Site(name=site_name, zones=zones))

    return AppConfig(
        data_dir=data_dir,
        sites=sites,
        secrets=raw.get("secrets") or {},
        alerts=raw.get("alerts") or {},
        git=raw.get("git") or {},
    )
