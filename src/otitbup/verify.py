"""Backup verification job.

A backup you never verify is a hope, not a backup. This re-hashes stored
artifacts against the manifest recorded at capture time and checks that
every offloaded blob is still present and intact. Run it from cron; it
alerts on any corruption.

By default it verifies each device's latest backup (fast); `--all-commits`
walks the entire history (thorough, for periodic deep checks).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .gitstore import GitStore
from .models import AppConfig, Device


@dataclass
class VerifyReport:
    checked_commits: int = 0
    checked_devices: int = 0
    problems: dict[str, list[str]] = field(default_factory=dict)
    orphan_blobs: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def verify(
    config: AppConfig,
    store: GitStore,
    blobstore=None,
    all_commits: bool = False,
) -> VerifyReport:
    report = VerifyReport()
    referenced: set[str] = set()
    for device in config.all_devices():
        commits = store.device_commits(device)
        if not commits:
            continue
        report.checked_devices += 1
        targets = commits if all_commits else commits[:1]
        for commit, _ts in targets:
            report.checked_commits += 1
            referenced |= _referenced_blobs(store, device, commit)
            problems = store.verify_commit(device, commit, blobstore=blobstore)
            if problems:
                key = f"{device.qualified_name}@{commit[:8]}"
                report.problems[key] = problems

    # Orphan blobs: present in the store but referenced by nothing we saw.
    # Only meaningful on a full (all_commits) sweep.
    if blobstore is not None and all_commits:
        stored = set(blobstore.all_blobs())
        report.orphan_blobs = len(stored - referenced)
    return report


def _referenced_blobs(store: GitStore, device: Device, commit: str) -> set[str]:

    from .blobstore import parse_pointer
    shas: set[str] = set()
    try:
        files = store.list_files_at(commit, device.path)
    except Exception:
        return shas
    for repo_path in files:
        try:
            pointer = parse_pointer(store.read_file_at(commit, repo_path))
        except Exception:
            continue
        if pointer:
            shas.add(pointer[0])
    return shas
