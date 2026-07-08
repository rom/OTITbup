"""Prometheus text-format metrics and JSON status — the machine-readable
views SCADA dashboards and monitoring poll so a silently-stopped backup
becomes visible.

metrics_text() renders the Prometheus exposition format; status_json()
returns the same underlying data as a JSON-serialisable dict.
"""
from __future__ import annotations

import time

from .gitstore import GitStore
from .models import AppConfig
from .runstore import RunStore


def _collect(config: AppConfig, store: GitStore, runstore: RunStore,
             blobstore=None, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    devices = config.all_devices()
    per_device = []
    covered = never = stale = failing = 0
    for device in devices:
        status = runstore.status(device.qualified_name)
        if status.last_success is None:
            never += 1
        else:
            covered += 1
            if now - status.last_success > 7 * 86400:
                stale += 1
        if status.consecutive_failures > 0:
            failing += 1
        per_device.append({
            "device": device.qualified_name,
            "site": device.site,
            "zone": device.zone,
            "driver": device.driver,
            "last_success": status.last_success,
            "last_attempt": status.last_attempt,
            "last_change": status.last_change,
            "last_ok": status.last_ok,
            "consecutive_failures": status.consecutive_failures,
            "age_seconds": (
                None if status.last_success is None
                else now - status.last_success
            ),
        })
    return {
        "generated_at": now,
        "totals": {
            "devices": len(devices),
            "covered": covered,
            "never_backed_up": never,
            "stale": stale,
            "failing": failing,
            "blob_bytes": blobstore.total_size() if blobstore else 0,
        },
        "devices": per_device,
    }


def status_json(config, store, runstore, blobstore=None, now=None) -> dict:
    return _collect(config, store, runstore, blobstore, now)


def metrics_text(config, store, runstore, blobstore=None, now=None) -> str:
    data = _collect(config, store, runstore, blobstore, now)
    totals = data["totals"]
    lines = [
        "# HELP otitbup_devices_total Configured devices.",
        "# TYPE otitbup_devices_total gauge",
        f"otitbup_devices_total {totals['devices']}",
        "# HELP otitbup_devices_covered Devices with at least one successful backup.",
        "# TYPE otitbup_devices_covered gauge",
        f"otitbup_devices_covered {totals['covered']}",
        "# HELP otitbup_devices_never_backed_up Devices never backed up successfully.",
        "# TYPE otitbup_devices_never_backed_up gauge",
        f"otitbup_devices_never_backed_up {totals['never_backed_up']}",
        "# HELP otitbup_devices_stale Devices with no success in 7 days.",
        "# TYPE otitbup_devices_stale gauge",
        f"otitbup_devices_stale {totals['stale']}",
        "# HELP otitbup_devices_failing Devices whose last backup failed.",
        "# TYPE otitbup_devices_failing gauge",
        f"otitbup_devices_failing {totals['failing']}",
        "# HELP otitbup_blob_bytes Total bytes in the large-artifact blob store.",
        "# TYPE otitbup_blob_bytes gauge",
        f"otitbup_blob_bytes {totals['blob_bytes']}",
        "# HELP otitbup_device_last_success_timestamp_seconds Last successful backup (unix).",
        "# TYPE otitbup_device_last_success_timestamp_seconds gauge",
        "# HELP otitbup_device_consecutive_failures Consecutive failed backups.",
        "# TYPE otitbup_device_consecutive_failures gauge",
    ]
    for dev in data["devices"]:
        labels = (
            f'device="{_esc(dev["device"])}",site="{_esc(dev["site"])}",'
            f'zone="{_esc(dev["zone"])}",driver="{_esc(dev["driver"])}"'
        )
        if dev["last_success"] is not None:
            lines.append(
                "otitbup_device_last_success_timestamp_seconds"
                f"{{{labels}}} {dev['last_success']:.0f}"
            )
        lines.append(
            f"otitbup_device_consecutive_failures{{{labels}}} "
            f"{dev['consecutive_failures']}"
        )
    return "\n".join(lines) + "\n"


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
