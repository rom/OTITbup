"""Core data model: site -> zone -> device.

The hierarchy is multi-site-ready from day one even though the first
deployments are single-site (see docs/REQUIREMENTS.md section 4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Built-in retention defaults. 0 means "unlimited" for keep_versions and
# keep_days; large_file_threshold is the size at or above which artifacts
# are offloaded to the blob store (0 disables offloading).
DEFAULT_RETENTION: dict[str, int] = {
    "keep_versions": 0,
    "keep_days": 0,
    "large_file_threshold": 1024 * 1024,
}


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
    retention: dict[str, int] = field(default_factory=dict)

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
    retention: dict[str, int] = field(default_factory=dict)


@dataclass
class Site:
    name: str
    zones: list[Zone] = field(default_factory=list)
    retention: dict[str, int] = field(default_factory=dict)


@dataclass
class AppConfig:
    data_dir: str
    sites: list[Site] = field(default_factory=list)
    secrets: dict[str, Any] = field(default_factory=dict)
    alerts: dict[str, Any] = field(default_factory=dict)
    git: dict[str, Any] = field(default_factory=dict)
    webui: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, int] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    reports: dict[str, Any] = field(default_factory=dict)
    events: dict[str, Any] = field(default_factory=dict)
    tickets: dict[str, Any] = field(default_factory=dict)
    netbox: dict[str, Any] = field(default_factory=dict)
    strategy: dict[str, Any] = field(default_factory=dict)

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

    def _retention_chain(
        self, device: Device
    ) -> list[tuple[str, dict[str, int]]]:
        """(level, settings) pairs from weakest to strongest override."""
        site = next(s for s in self.sites if s.name == device.site)
        zone = self.find_zone(device)
        return [
            ("default", DEFAULT_RETENTION),
            ("global", self.retention),
            ("site", site.retention),
            ("zone", zone.retention),
            ("device", device.retention),
        ]

    def retention_for(self, device: Device) -> dict[str, int]:
        """Effective retention policy: device > zone > site > global >
        built-in defaults, merged per field."""
        merged: dict[str, int] = {}
        for _level, settings in self._retention_chain(device):
            merged.update(settings)
        return merged

    def retention_sources(self, device: Device) -> dict[str, str]:
        """Which level set each effective retention field (for display)."""
        sources: dict[str, str] = {}
        for level, settings in self._retention_chain(device):
            for key in settings:
                sources[key] = level
        return sources

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

    def select(
        self,
        names: list[str] | None = None,
        sites: list[str] | None = None,
        zones: list[str] | None = None,
    ) -> list[Device]:
        """Select devices by explicit names and/or by --site/--zone
        filters. With no criteria, returns every device."""
        if not names and not sites and not zones:
            return self.all_devices()
        selected: dict[str, Device] = {}
        if names:
            for device in self.find_devices(names):
                selected[device.qualified_name] = device
        if sites or zones:
            for device in self.all_devices():
                if sites and device.site not in sites:
                    continue
                if zones and device.zone not in zones:
                    continue
                selected[device.qualified_name] = device
        return [d for _, d in sorted(selected.items())]
