"""Detect configuration-file changes between runs.

The YAML config is the source of truth, and it is easy for it to change
underneath a scheduled backup (an edit on disk, a git pull, another admin).
This module keeps a JSON snapshot of the last *accepted* configuration next
to the data directory and, at process start, diffs the file on disk against
that snapshot:

  * minor changes (tunables, alerts, themes, added devices, ...) are listed
    as warnings and the snapshot is refreshed automatically;
  * major changes (removed/renamed inventory, driver/address/credential
    edits, data_dir, secrets, encryption, git remote, retention locks,
    users/auth) require an explicit acceptance — an interactive [y/N]
    dialogue on a TTY, or the --accept-config-changes flag in unattended
    runs. Until accepted, every run keeps warning.

Everything here is pure dict-diffing over the raw YAML; nothing imports the
runner, so it is trivially testable.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Dotted-path prefixes whose *modification or removal* is a major change.
# These either move/expose data (data_dir, secrets, encryption, git,
# offsite), weaken guarantees (retention lock), or change who can act
# (webui auth/users, ldap).
MAJOR_PREFIXES = (
    "data_dir",
    "secrets",
    "encryption",
    "git",
    "offsite",
    "retention.lock_days",
    "webui.auth",
    "webui.users",
    "ldap",
)

# Device fields whose modification is major (they change what is backed up,
# where, and with which credentials).
_MAJOR_DEVICE_FIELDS = ("driver", "address", "credentials", "options")


@dataclass
class ConfigChange:
    """The classified difference between the accepted and current config."""

    minor: list[str] = field(default_factory=list)
    major: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.minor or self.major)


def state_path(config) -> Path:
    """Where the accepted-config snapshot lives (next to the data dir)."""
    return Path(config.data_dir).parent / "config-snapshot.json"


def _load_raw(config_path: str | Path) -> dict:
    with open(config_path) as fh:
        raw = yaml.safe_load(fh) or {}
    return raw if isinstance(raw, dict) else {}


def _index_inventory(raw: dict) -> dict:
    """Rewrite the sites/zones/devices lists as name-keyed mappings so the
    diff survives reordering and reports stable paths."""
    out = dict(raw)
    sites = {}
    for site in raw.get("sites") or []:
        sname = str(site.get("name", "?"))
        zones = {}
        for zone in site.get("zones") or []:
            zname = str(zone.get("name", "?"))
            devices = {
                str(d.get("name", "?")): {k: v for k, v in d.items()
                                          if k != "name"}
                for d in zone.get("devices") or []
            }
            zdict = {k: v for k, v in zone.items()
                     if k not in ("name", "devices")}
            zdict["devices"] = devices
            zones[zname] = zdict
        sdict = {k: v for k, v in site.items() if k not in ("name", "zones")}
        sdict["zones"] = zones
        sites[sname] = sdict
    out["sites"] = sites
    return out


def _flatten(data, prefix: str = "") -> dict[str, object]:
    """Flatten nested dicts to {dotted.path: leaf}; lists stay leaves."""
    out: dict[str, object] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(_flatten(value, path))
            else:
                out[path] = value
        if not data and prefix:
            out[prefix] = {}
    else:
        out[prefix] = data
    return out


def _device_prefix(path: str) -> str | None:
    """The `sites.<site>.zones.<zone>.devices.<device>` prefix of a flattened
    inventory path, or None if the path is not inside a device."""
    parts = path.split(".")
    if (len(parts) >= 6 and parts[0] == "sites" and parts[2] == "zones"
            and parts[4] == "devices"):
        return ".".join(parts[:6])
    return None


def _is_major(path: str, kind: str, old_device_prefixes: set[str]) -> bool:
    """kind: 'added' | 'removed' | 'changed'. `old_device_prefixes` is the set
    of device prefixes that existed in the previous config, so a field *added*
    to an existing device (e.g. a new address/credentials) is distinguished
    from a brand-new device (whose every field reads as 'added')."""
    if path == "sites" or path.startswith("sites."):
        parts = path.split(".")
        # sites.<site>.zones.<zone>.devices.<device>[.field]
        if kind == "removed":
            return True                      # inventory removal/rename
        if "devices" in parts:
            di = parts.index("devices")
            fields = parts[di + 2:]
            if not fields:
                return False
            if kind == "added":
                # A new device is routine; a new field on a device that
                # already existed (re-pointing address/driver/credentials)
                # is a major change and must not slip through as 'added'.
                dev_prefix = ".".join(parts[:di + 2])
                if dev_prefix not in old_device_prefixes:
                    return False
            return fields[0] in _MAJOR_DEVICE_FIELDS
        # New/removed site or zone (handled above); a zone/site attribute
        # merely added is routine.
        return False
    for prefix in MAJOR_PREFIXES:
        if path == prefix or path.startswith(prefix + "."):
            return True
    return False


def diff_raw(old: dict, new: dict) -> ConfigChange:
    """Diff two raw config dicts into classified, human-readable changes."""
    flat_old = _flatten(_index_inventory(old))
    flat_new = _flatten(_index_inventory(new))
    old_device_prefixes = {
        p for p in (_device_prefix(k) for k in flat_old) if p
    }
    change = ConfigChange()
    for path in sorted(set(flat_old) | set(flat_new)):
        if path in flat_old and path not in flat_new:
            kind, desc = "removed", f"removed {path} (was {flat_old[path]!r})"
        elif path not in flat_old and path in flat_new:
            kind, desc = "added", f"added {path} = {flat_new[path]!r}"
        elif flat_old[path] != flat_new[path]:
            kind = "changed"
            desc = (f"changed {path}: {flat_old[path]!r} -> "
                    f"{flat_new[path]!r}")
        else:
            continue
        major = _is_major(path, kind, old_device_prefixes)
        (change.major if major else change.minor).append(desc)
    return change


def _fingerprint(raw: dict) -> str:
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, default=str).encode()
    ).hexdigest()


def check(config_path: str | Path, snapshot_path: Path) -> ConfigChange | None:
    """Compare the config file with the accepted snapshot.

    Returns None on the first run (no snapshot yet — the snapshot is
    written), an empty ConfigChange when nothing changed, or the classified
    changes."""
    raw = _load_raw(config_path)
    if not snapshot_path.exists():
        accept(config_path, snapshot_path)
        return None
    try:
        stored = json.loads(snapshot_path.read_text())
    except (OSError, ValueError):
        accept(config_path, snapshot_path)
        return None
    old = stored.get("config") if isinstance(stored, dict) else None
    if not isinstance(old, dict):
        accept(config_path, snapshot_path)
        return None
    if _fingerprint(old) == _fingerprint(raw):
        return ConfigChange()
    return diff_raw(old, raw)


def accept(config_path: str | Path, snapshot_path: Path) -> None:
    """Record the current config file as the accepted snapshot."""
    import time
    raw = _load_raw(config_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = snapshot_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(
        {"config": raw, "accepted_at": time.time(),
         "sha256": _fingerprint(raw)},
        indent=2, sort_keys=True, default=str))
    tmp.replace(snapshot_path)


def review(
    config_path: str | Path, snapshot_path: Path, *,
    assume_yes: bool = False, events=None,
    prompt=input, out=None,
) -> bool:
    """The CLI entry point: check, report, and (for major changes) run the
    acceptance dialogue. Returns False when the operator declined the
    changes (the caller should abort)."""
    out = out or sys.stderr
    try:
        change = check(config_path, snapshot_path)
    except Exception as exc:                    # never block a backup on this
        print(f"config-change check failed: {exc}", file=out)
        return True
    if change is None or not change:
        return True

    for desc in change.minor:
        print(f"config change (minor): {desc}", file=out)
    for desc in change.major:
        print(f"config change (MAJOR): {desc}", file=out)
    if events is not None:
        from .events import CONFIG_CHANGED
        summary = (f"{len(change.major)} major / {len(change.minor)} minor "
                   f"change(s) in {config_path}")
        events.emit(CONFIG_CHANGED, f"configuration changed: {summary}",
                    severity="warning" if change.major else "notice",
                    detail="; ".join(change.major + change.minor)[:500])

    if not change.major:
        # Minor-only: warn and move on; the new config becomes the baseline.
        accept(config_path, snapshot_path)
        return True

    if assume_yes:
        print("major config changes accepted (--accept-config-changes)",
              file=out)
        accept(config_path, snapshot_path)
        return True
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            answer = prompt(
                f"{len(change.major)} MAJOR configuration change(s) detected "
                "— accept and continue? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer.strip().lower() in ("y", "yes"):
            accept(config_path, snapshot_path)
            return True
        print("configuration changes declined — aborting", file=out)
        return False
    # Unattended (daemon/cron): keep running, but do NOT accept the new
    # config as baseline — every run keeps warning until an operator
    # accepts interactively or with --accept-config-changes.
    print(
        "major configuration changes NOT yet accepted (non-interactive); "
        "re-run interactively or pass --accept-config-changes", file=out)
    return True
