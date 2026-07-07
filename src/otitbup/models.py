"""Core data model: site -> zone -> device.

The hierarchy is multi-site-ready from day one even though the first
deployments are single-site (see docs/REQUIREMENTS.md section 4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Device:
    name: str
    driver: str
    site: str
    zone: str
    address: str | None = None
    schedule: str = "1d"
    credentials: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str:
        """Relative path of this device inside the backup repository."""
        return f"sites/{self.site}/{self.zone}/{self.name}"

    @property
    def qualified_name(self) -> str:
        return f"{self.site}/{self.zone}/{self.name}"


@dataclass
class Zone:
    name: str
    site: str
    devices: list[Device] = field(default_factory=list)
    maintenance_window: str | None = None
    max_concurrent: int = 1


@dataclass
class Site:
    name: str
    zones: list[Zone] = field(default_factory=list)


@dataclass
class AppConfig:
    data_dir: str
    sites: list[Site] = field(default_factory=list)
    secrets: dict[str, Any] = field(default_factory=dict)
    alerts: dict[str, Any] = field(default_factory=dict)
    git: dict[str, Any] = field(default_factory=dict)
    webui: dict[str, Any] = field(default_factory=dict)

    def all_devices(self) -> list[Device]:
        return [d for s in self.sites for z in s.zones for d in z.devices]

    def find_zone(self, device: Device) -> Zone:
        for site in self.sites:
            if site.name != device.site:
                continue
            for zone in site.zones:
                if zone.name == device.zone:
                    return zone
        raise KeyError(f"zone not found for device {device.qualified_name}")

    def find_devices(self, names: list[str]) -> list[Device]:
        """Resolve device names; accepts bare names or site/zone/name paths."""
        result = []
        for name in names:
            matches = [
                d for d in self.all_devices()
                if d.name == name or d.qualified_name == name
            ]
            if not matches:
                raise KeyError(f"unknown device: {name}")
            if len(matches) > 1:
                qualified = ", ".join(d.qualified_name for d in matches)
                raise KeyError(f"ambiguous device name {name!r}: {qualified}")
            result.extend(matches)
        return result
