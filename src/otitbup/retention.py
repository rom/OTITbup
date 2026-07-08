"""Retention: prune expired blob-store content without touching git.

Policies resolve hierarchically (device > zone > site > global > built-in
defaults, per field — see models.DEFAULT_RETENTION):

    retention:
      keep_versions: 30           # keep blobs of the newest N backups
      keep_days: 365              # ... and of any backup newer than this
      large_file_threshold: 1048576   # offload artifacts >= this to the
                                      # blob store (0 disables offloading)

keep_versions and keep_days are OR'd: a backup's blobs survive if it is
recent enough by either rule. 0 means unlimited. Git history itself is
never rewritten — text artifacts and pointer files stay forever (cheap);
only expired blob content is deleted. A blob shared by several devices
(content-addressed dedup) survives as long as ANY device still retains it.

`otitbup retention` is a dry run; `otitbup retention --apply` deletes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import yaml

from .blobstore import BlobStore
from .gitstore import GitStore
from .models import AppConfig, Device


@dataclass
class DevicePlan:
    device: str
    policy: dict[str, int]
    sources: dict[str, str]
    backups: int = 0
    kept_backups: int = 0
    referenced: set[str] = field(default_factory=set)
    kept: set[str] = field(default_factory=set)


@dataclass
class PrunePlan:
    devices: list[DevicePlan]
    deletable: dict[str, int]          # sha256 -> size
    blob_count: int = 0
    blob_bytes: int = 0

    @property
    def deletable_bytes(self) -> int:
        return sum(self.deletable.values())


def describe_policy(policy: dict[str, int]) -> str:
    def _limit(value: int, unit: str) -> str:
        return f"{value}{unit}" if value else "unlimited"

    threshold = policy.get("large_file_threshold", 0)
    if not threshold:
        offload = "off"
    elif threshold >= 1024:
        offload = f"{threshold // 1024} KiB"
    else:
        offload = f"{threshold} B"
    return (
        f"versions: {_limit(policy.get('keep_versions', 0), '')} · "
        f"age: {_limit(policy.get('keep_days', 0), 'd')} · "
        f"offload ≥ {offload}"
    )


def _offloaded_shas(store: GitStore, device: Device, commit: str) -> set[str]:
    """Blob hashes referenced by one backup, via its manifest."""
    try:
        manifest = yaml.safe_load(
            store.read_file_at(commit, f"{device.path}/manifest.yml")
        )
    except Exception:
        return set()
    if not isinstance(manifest, dict):
        return set()
    from .gitstore import manifest_artifacts
    return {
        str(entry["sha256"])
        for entry in manifest_artifacts(manifest).values()
        if isinstance(entry, dict) and entry.get("offloaded")
        and entry.get("sha256")
    }


def plan(
    config: AppConfig,
    store: GitStore,
    blobstore: BlobStore,
    now: float | None = None,
) -> PrunePlan:
    now = now if now is not None else time.time()
    device_plans: list[DevicePlan] = []
    keep_all: set[str] = set()

    for device in config.all_devices():
        policy = config.retention_for(device)
        dplan = DevicePlan(
            device=device.qualified_name,
            policy=policy,
            sources=config.retention_sources(device),
        )
        keep_versions = policy.get("keep_versions", 0)
        keep_days = policy.get("keep_days", 0)
        cutoff = now - keep_days * 86400 if keep_days else None

        commits = store.device_commits(device)
        dplan.backups = len(commits)
        for index, (commit, timestamp) in enumerate(commits):
            shas = _offloaded_shas(store, device, commit)
            dplan.referenced |= shas
            unlimited = not keep_versions and not keep_days
            keep = (
                unlimited
                or (keep_versions and index < keep_versions)
                or (cutoff is not None and timestamp >= cutoff)
            )
            if keep:
                dplan.kept_backups += 1
                dplan.kept |= shas
        keep_all |= dplan.kept
        device_plans.append(dplan)

    blobs = blobstore.all_blobs()
    deletable = {
        sha: size for sha, size in blobs.items() if sha not in keep_all
    }
    return PrunePlan(
        devices=device_plans,
        deletable=deletable,
        blob_count=len(blobs),
        blob_bytes=sum(blobs.values()),
    )


def apply(prune_plan: PrunePlan, blobstore: BlobStore) -> int:
    """Delete every blob in the plan; returns bytes freed."""
    freed = 0
    for sha in prune_plan.deletable:
        freed += blobstore.delete(sha)
    return freed
