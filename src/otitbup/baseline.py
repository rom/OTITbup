"""Baseline (golden-config) drift.

Change detection alone tells you a config differs from *last time*. What
operators actually certify against is an *approved* baseline — the config
signed off after the last authorized change. This measures every device's
latest backup against its approved baseline commit and reports drift.

Baselines are set explicitly (`otitbup baseline set <device>`), stored in
the run store, and shown in the web UI. Drift is the diff between the
baseline commit and the device's latest commit.
"""
from __future__ import annotations

from dataclasses import dataclass

from .gitstore import GitStore
from .models import AppConfig, Device
from .runstore import RunStore


@dataclass
class Drift:
    device: str
    baseline_commit: str | None
    latest_commit: str | None
    has_baseline: bool
    drifted: bool
    note: str = ""


def device_drift(
    store: GitStore, runstore: RunStore, device: Device
) -> Drift:
    baseline = runstore.get_baseline(device.qualified_name)
    latest = store.last_commit_hash(device)
    if not baseline:
        return Drift(
            device=device.qualified_name,
            baseline_commit=None, latest_commit=latest,
            has_baseline=False, drifted=False,
        )
    base_commit = baseline["commit_hash"]
    drifted = bool(latest) and latest != base_commit
    return Drift(
        device=device.qualified_name,
        baseline_commit=base_commit,
        latest_commit=latest,
        has_baseline=True,
        drifted=drifted,
        note=baseline.get("note") or "",
    )


def drift_diff(
    store: GitStore, runstore: RunStore, device: Device
) -> str:
    drift = device_drift(store, runstore, device)
    if not drift.has_baseline or not drift.drifted or not drift.latest_commit:
        return ""
    return store.diff_between(
        device, drift.baseline_commit, drift.latest_commit
    )


def all_drift(
    config: AppConfig, store: GitStore, runstore: RunStore
) -> list[Drift]:
    return [
        device_drift(store, runstore, device)
        for device in config.all_devices()
    ]
