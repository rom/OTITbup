"""Desired-config / config-as-code drift.

The `baseline` feature approves a *captured* backup as the golden copy —
"whatever it looked like on Tuesday is correct." Config-as-code is the
other direction: you commit the *intended* configuration as files in a
directory (itself version-controlled in your own GitOps repo), and OTITbup
tells you where the live device has drifted from that declared intent.

Layout mirrors the backup repo — one file per artifact:

    <desired.dir>/<site>/<zone>/<device>/<artifact-name>

Config:

    desired:
      dir: ./desired            # directory of intended configs
      strip_trailing_ws: true   # ignore trailing-whitespace-only diffs

Only devices (and artifacts) that have a desired file are checked; anything
without one is simply "undeclared" and skipped. Comparison is against each
device's latest backup, resolving blob-offloaded artifacts transparently.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from .gitstore import GitStore, GitStoreError
from .models import AppConfig, Device


@dataclass
class ArtifactDrift:
    artifact: str
    status: str                 # "match" | "drift" | "missing"
    diff: str = ""


@dataclass
class DeviceDrift:
    device: str
    checked: int = 0
    drifted: int = 0
    artifacts: list[ArtifactDrift] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return self.drifted == 0 and self.checked > 0


def _desired_files(base: Path, device: Device) -> dict[str, bytes]:
    """Desired artifacts for a device, keyed by artifact name."""
    ddir = base / device.site / device.zone / device.name
    if not ddir.is_dir():
        return {}
    out = {}
    for path in sorted(ddir.iterdir()):
        if path.is_file():
            out[path.name] = path.read_bytes()
    return out


def _normalize(data: bytes, strip_ws: bool) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if strip_ws:
        lines = [ln.rstrip() for ln in lines]
    return lines


def check_device(
    config: AppConfig, store: GitStore, device: Device, base: Path,
    blobstore=None, strip_ws: bool = True,
) -> DeviceDrift:
    from .blobstore import parse_pointer
    result = DeviceDrift(device=device.qualified_name)
    desired = _desired_files(base, device)
    if not desired:
        return result
    commit = store.last_commit_hash(device)
    for name, want in desired.items():
        result.checked += 1
        try:
            have = store.read_file_at(commit, f"{device.path}/{name}") \
                if commit else b""
        except GitStoreError:
            have = b""
        if not have and commit is None:
            result.artifacts.append(ArtifactDrift(name, "missing"))
            result.drifted += 1
            continue
        pointer = parse_pointer(have)
        if pointer and blobstore is not None and blobstore.has(pointer[0]):
            have = blobstore.get(pointer[0])
        want_lines = _normalize(want, strip_ws)
        have_lines = _normalize(have, strip_ws)
        if want_lines == have_lines:
            result.artifacts.append(ArtifactDrift(name, "match"))
            continue
        diff = "\n".join(difflib.unified_diff(
            want_lines, have_lines,
            fromfile=f"desired/{name}", tofile=f"live/{name}", lineterm="",
        ))
        result.artifacts.append(ArtifactDrift(name, "drift", diff))
        result.drifted += 1
    return result


def check_all(
    config: AppConfig, store: GitStore, blobstore=None,
) -> list[DeviceDrift]:
    """Drift for every device that has a desired-config declaration."""
    desired_cfg = config.desired or {}
    base = Path(desired_cfg.get("dir", "desired"))
    strip_ws = bool(desired_cfg.get("strip_trailing_ws", True))
    out = []
    for device in config.all_devices():
        drift = check_device(
            config, store, device, base, blobstore=blobstore,
            strip_ws=strip_ws,
        )
        if drift.checked:
            out.append(drift)
    return out
