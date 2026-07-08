"""Safe, validated read-modify-write of the YAML config for the web UI.

The inventory file (otitbup.yml) is the source of truth and normally lives
in git, hand-edited. This module lets an admin change it from the web UI
without hand-editing YAML: it loads the raw document, applies a targeted
change, RE-VALIDATES the whole thing through the real config loader, and
only then writes it back atomically — keeping a `.bak` of the previous
version. A change that would produce an invalid config is refused and the
file on disk is left untouched.

Comments are preserved: writes go through ruamel.yaml (a declared
dependency) in round-trip mode, so hand-written comments and key order in
the config survive a GUI edit. The previous version is always kept as
`<config>.bak` regardless. If ruamel is somehow unavailable the code falls
back to PyYAML (which normalises the file), but that is not the shipped
configuration.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError, load_config


class ConfigEditError(Exception):
    pass


# ---------------------------------------------------------------- raw I/O

def _load_roundtrip(path: Path):
    """Load the raw YAML. Uses ruamel (comment-preserving) if available,
    else PyYAML. Returns (data, dumper) where dumper(data)->str."""
    text = path.read_text()
    try:
        from io import StringIO

        from ruamel.yaml import YAML  # optional
        ry = YAML()
        ry.preserve_quotes = True
        data = ry.load(text) or {}

        def dump(obj) -> str:
            buf = StringIO()
            ry.dump(obj, buf)
            return buf.getvalue()

        return data, dump
    except ImportError:
        data = yaml.safe_load(text) or {}

        def dump(obj) -> str:
            return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)

        return data, dump


def load_raw(path: str | Path) -> dict:
    return _load_roundtrip(Path(path))[0]


def save_raw(path: str | Path, raw: dict) -> None:
    """Validate `raw` through the real loader, then write it atomically,
    keeping the previous version as <path>.bak. Refuses invalid configs."""
    path = Path(path)
    _, dump = _load_roundtrip(path)
    serialized = dump(raw)
    # Validate by loading a temp copy in the same directory (so relative
    # data_dir/desired paths resolve identically).
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(serialized)
    try:
        load_config(tmp)
    except ConfigError as exc:
        tmp.unlink(missing_ok=True)
        raise ConfigEditError(f"change rejected (invalid config): {exc}") from exc
    except Exception as exc:                              # pragma: no cover
        tmp.unlink(missing_ok=True)
        raise ConfigEditError(f"change rejected: {exc}") from exc
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        backup.write_text(path.read_text())
    os.replace(tmp, path)


# ------------------------------------------------------ navigation helpers

def _sites(raw: dict) -> list:
    return raw.setdefault("sites", [])


def find_site(raw: dict, site: str) -> dict:
    for s in _sites(raw):
        if s.get("name") == site:
            return s
    raise ConfigEditError(f"no such site: {site}")


def find_zone(raw: dict, site: str, zone: str) -> dict:
    for z in find_site(raw, site).get("zones", []) or []:
        if z.get("name") == zone:
            return z
    raise ConfigEditError(f"no such zone: {site}/{zone}")


def find_device(raw: dict, site: str, zone: str, name: str) -> dict:
    for d in find_zone(raw, site, zone).get("devices", []) or []:
        if d.get("name") == name:
            return d
    raise ConfigEditError(f"no such device: {site}/{zone}/{name}")


# ------------------------------------------------------------- mutations

def _apply(target: dict, fields: dict[str, Any]) -> None:
    """Merge `fields` into `target`. A value of None or "" DELETES the key
    (so a blank form field clears a setting); a dict value is merged
    recursively, and an empty dict deletes the key."""
    for key, value in fields.items():
        if value is None or value == "" or value == [] or value == ():
            target.pop(key, None)
        elif isinstance(value, dict):
            if not value:
                target.pop(key, None)
            else:
                sub = target.get(key)
                if not isinstance(sub, dict):
                    sub = {}
                _apply(sub, value)
                if sub:
                    target[key] = sub
                else:
                    target.pop(key, None)
        else:
            target[key] = value


def set_global(path: str | Path, section: str, fields: dict[str, Any]) -> None:
    """Merge `fields` into a top-level section (offsite, ldap, webui, events,
    logging, netbox, tickets, encryption, anomaly, git, …)."""
    raw = load_raw(path)
    current = raw.get(section)
    if not isinstance(current, dict):
        current = {}
    _apply(current, fields)
    if current:
        raw[section] = current
    else:
        raw.pop(section, None)
    save_raw(path, raw)


def set_site(path: str | Path, site: str, fields: dict[str, Any]) -> None:
    raw = load_raw(path)
    _apply(find_site(raw, site), fields)
    save_raw(path, raw)


def set_zone(path: str | Path, site: str, zone: str,
             fields: dict[str, Any]) -> None:
    raw = load_raw(path)
    _apply(find_zone(raw, site, zone), fields)
    save_raw(path, raw)


def set_device(path: str | Path, site: str, zone: str, name: str,
               fields: dict[str, Any]) -> str:
    """Update a device. A `name` field renames it. Returns the new
    qualified name."""
    raw = load_raw(path)
    dev = find_device(raw, site, zone, name)
    _apply(dev, fields)
    save_raw(path, raw)
    new_name = dev.get("name", name)
    return f"{site}/{zone}/{new_name}"


def add_device(path: str | Path, site: str, zone: str,
               device: dict[str, Any]) -> str:
    raw = load_raw(path)
    z = find_zone(raw, site, zone)
    devices = z.setdefault("devices", [])
    name = device.get("name")
    if any(d.get("name") == name for d in devices):
        raise ConfigEditError(f"device already exists: {site}/{zone}/{name}")
    devices.append(device)
    save_raw(path, raw)
    return f"{site}/{zone}/{name}"


def delete_device(path: str | Path, site: str, zone: str, name: str) -> None:
    raw = load_raw(path)
    z = find_zone(raw, site, zone)
    before = z.get("devices", []) or []
    z["devices"] = [d for d in before if d.get("name") != name]
    if len(z["devices"]) == len(before):
        raise ConfigEditError(f"no such device: {site}/{zone}/{name}")
    save_raw(path, raw)


def add_zone(path: str | Path, site: str, zone: str,
             fields: dict[str, Any] | None = None) -> None:
    raw = load_raw(path)
    s = find_site(raw, site)
    zones = s.setdefault("zones", [])
    if any(z.get("name") == zone for z in zones):
        raise ConfigEditError(f"zone already exists: {site}/{zone}")
    entry = {"name": zone, "devices": []}
    if fields:
        _apply(entry, fields)
    zones.append(entry)
    save_raw(path, raw)


def add_collector(path: str | Path, collector: dict[str, Any]) -> str:
    """Append a federation collector (central roll-up). Rejects a duplicate
    name. Returns the collector name."""
    raw = load_raw(path)
    fed = raw.get("federation")
    if not isinstance(fed, dict):
        fed = {}
    collectors = fed.setdefault("collectors", [])
    name = collector.get("name")
    if not name or not collector.get("url"):
        raise ConfigEditError("collector name and url are required")
    if any(c.get("name") == name for c in collectors):
        raise ConfigEditError(f"collector already exists: {name}")
    collectors.append(collector)
    raw["federation"] = fed
    save_raw(path, raw)
    return name


def delete_collector(path: str | Path, name: str) -> None:
    raw = load_raw(path)
    fed = raw.get("federation") or {}
    collectors = fed.get("collectors") or []
    remaining = [c for c in collectors if c.get("name") != name]
    if len(remaining) == len(collectors):
        raise ConfigEditError(f"no such collector: {name}")
    fed["collectors"] = remaining
    save_raw(path, raw)


def add_site(path: str | Path, site: str,
             fields: dict[str, Any] | None = None) -> None:
    raw = load_raw(path)
    if any(s.get("name") == site for s in _sites(raw)):
        raise ConfigEditError(f"site already exists: {site}")
    entry = {"name": site, "zones": []}
    if fields:
        _apply(entry, fields)
    _sites(raw).append(entry)
    save_raw(path, raw)
