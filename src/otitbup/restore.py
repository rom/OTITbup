"""Phase 2 restore workflow — guided, not automated.

Per REQUIREMENTS.md sections 3 and 10 the collector never writes to
devices. `otitbup restore` exports a *restore bundle*: the exact versioned
artifacts for a chosen backup, hash-verified against the manifest recorded
at backup time, plus a RESTORE.md checklist with driver-specific
instructions. A human performs the actual restore with vendor tools.
"""
from __future__ import annotations

from pathlib import Path

import hashlib

import yaml

from .gitstore import GitStore
from .models import Device


class RestoreError(Exception):
    pass


_GENERIC_STEPS = """\
1. Confirm this is the intended restore point (commit and date above) —
   compare with `otitbup log {name}`.
2. Follow your management-of-change procedure before touching the device.
3. Verify the device is the same hardware/firmware the backup came from
   (see the metadata artifacts in this bundle).
4. Perform the restore with the vendor tool as described below, inside a
   maintenance window.
5. Afterwards run `otitbup backup {name} --force` and check
   `otitbup diff {name}` shows no unexpected difference.
"""

_DRIVER_INSTRUCTIONS = {
    "generic_file": (
        "This bundle contains engineer-exported project files. Open the "
        "project in the engineering tool it came from (TIA Portal, "
        "Studio 5000, EcoStruxure Control Expert, ...) on an engineering "
        "workstation, verify it, and download to the device from there."
    ),
    "generic_ssh": (
        "This bundle contains the device's configuration as text "
        "(e.g. show running-config output). Restore via the vendor's "
        "config-load mechanism (copy over SCP/TFTP to startup-config, or "
        "paste in configuration mode via console), then compare the "
        "running config against the bundled file."
    ),
    "siemens_s7": (
        "blocks/*.mc7 are the program blocks uploaded from the CPU; "
        "cpu_info.yml identifies the exact CPU and firmware. Preferred "
        "restore path is the corresponding TIA Portal/STEP 7 project "
        "(see any generic_file export of this PLC). Downloading raw MC7 "
        "blocks back to a CPU is possible with snap7 but is NOT automated "
        "by otitbup — only attempt it with the vendor-recommended "
        "procedure and the plant stopped or in a safe state."
    ),
    "rockwell_enip": (
        "This bundle holds controller identity and the tag list — enough "
        "to verify a controller, not to program one. Restore the "
        "program by downloading the matching .ACD project with Studio "
        "5000 (see any generic_file export of this controller), then "
        "compare controller_info.yml and tags.yml against a fresh backup."
    ),
    "schneider_modbus": (
        "This bundle holds device identification (including the loaded "
        "application name). Restore the program by downloading the "
        "matching project with EcoStruxure Control Expert / Unity Pro "
        "(see any generic_file export of this PLC), then verify "
        "device_identification.yml matches a fresh backup."
    ),
}


def export_bundle(
    store: GitStore,
    device: Device,
    out_dir: str | Path,
    commit: str | None = None,
) -> tuple[str, list[str]]:
    """Export the device's artifacts at `commit` (default: latest backup)
    into `out_dir`. Returns (commit, hash_mismatches)."""
    commit = commit or store.last_commit_hash(device)
    if not commit:
        raise RestoreError(f"{device.qualified_name}: no backups in history")

    files = store.list_files_at(commit, device.path)
    if not files:
        raise RestoreError(
            f"{device.qualified_name}: nothing stored at commit {commit}"
        )

    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise RestoreError(f"output directory not empty: {out}")
    artifacts_dir = out / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {}
    manifest_path = f"{device.path}/manifest.yml"
    if manifest_path in files:
        manifest = yaml.safe_load(
            store.read_file_at(commit, manifest_path)
        ) or {}

    mismatches: list[str] = []
    prefix = device.path + "/"
    for repo_path in files:
        relative = repo_path[len(prefix):]
        data = store.read_file_at(commit, repo_path)
        target = artifacts_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        expected = manifest.get(relative, {}).get("sha256")
        if expected and hashlib.sha256(data).hexdigest() != expected:
            mismatches.append(relative)

    commit_info = store.commit_summary(commit)
    verification = (
        "All artifact hashes match the manifest recorded at backup time."
        if not mismatches else
        "HASH MISMATCH — do NOT use these artifacts:\n"
        + "\n".join(f"  - {m}" for m in mismatches)
    )
    instructions = _DRIVER_INSTRUCTIONS.get(
        device.driver,
        "No driver-specific instructions; restore with the vendor tool "
        "appropriate for this equipment.",
    )
    (out / "RESTORE.md").write_text(
        f"# Restore bundle — {device.qualified_name}\n\n"
        f"**Backup:** `{commit}`\n\n"
        f"```\n{commit_info}\n```\n\n"
        f"**Driver:** `{device.driver}` · exported by otitbup; otitbup "
        "performs no device writes — a person restores with vendor "
        "tools.\n\n"
        f"**Integrity:** {verification}\n\n"
        "## Checklist\n\n"
        + _GENERIC_STEPS.format(name=device.name)
        + "\n## Driver-specific instructions\n\n"
        + instructions + "\n\n"
        "## Artifacts\n\n"
        + "\n".join(
            f"- `artifacts/{name}` (sha256 `{meta.get('sha256', '?')[:16]}…`)"
            for name, meta in sorted(manifest.items())
        )
        + "\n"
    )
    return commit, mismatches
