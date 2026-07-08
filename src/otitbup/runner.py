"""Backup orchestration.

Respects OT safety constraints (docs/REQUIREMENTS.md section 10): per-zone
maintenance windows and per-zone concurrency caps so backup traffic cannot
disturb control traffic.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pathlib import Path

from .alerts import AlertManager
from .blobstore import BlobStore
from .drivers import get_driver
from .gitstore import GitStore
from .models import AppConfig, Device
from .secrets import SecretsBackend


def default_blobstore(config: AppConfig) -> BlobStore:
    """The blob store lives next to (not inside) the backup repo."""
    return BlobStore(Path(config.data_dir).parent / "blobs")

log = logging.getLogger("otitbup.runner")


@dataclass
class BackupResult:
    device: str
    ok: bool
    changed: bool = False
    commit: str | None = None
    message: str = ""


class Runner:
    def __init__(
        self,
        config: AppConfig,
        store: GitStore,
        secrets: SecretsBackend | None = None,
        alerts: AlertManager | None = None,
        force: bool = False,
    ):
        self.config = config
        self.store = store
        self.secrets = secrets
        self.alerts = alerts or AlertManager(config.alerts)
        self.force = force
        self.blobstore = default_blobstore(config)

    def backup_devices(self, devices: list[Device]) -> list[BackupResult]:
        self.store.ensure_repo()
        zone_limits: dict[tuple[str, str], threading.Semaphore] = {}
        for device in devices:
            key = (device.site, device.zone)
            if key not in zone_limits:
                zone = self.config.find_zone(device)
                zone_limits[key] = threading.Semaphore(zone.max_concurrent)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(self._backup_one, d, zone_limits[(d.site, d.zone)])
                for d in devices
            ]
            results = [f.result() for f in futures]

        changed = [r for r in results if r.changed]
        failed = [r for r in results if not r.ok]
        if changed:
            names = ", ".join(r.device for r in changed)
            body = "\n".join(
                f"{r.device}: commit {r.commit}" for r in changed
            )
            self.alerts.notify(f"otitbup: changes detected on {names}", body)
        if failed:
            body = "\n".join(f"{r.device}: {r.message}" for r in failed)
            self.alerts.notify(
                f"otitbup: {len(failed)} backup(s) failed", body
            )

        if self.config.git.get("push") and self.config.git.get("remote"):
            try:
                self.store.push(self.config.git["remote"])
            except Exception as exc:
                log.warning("push to remote failed: %s", exc)

        return results

    def _backup_one(
        self, device: Device, limit: threading.Semaphore
    ) -> BackupResult:
        from .windows import in_window

        zone = self.config.find_zone(device)
        if not self.force and not in_window(zone.maintenance_window):
            return BackupResult(
                device=device.qualified_name,
                ok=True,
                message=f"skipped: outside maintenance window "
                        f"{zone.maintenance_window}",
            )
        with limit:
            try:
                driver = get_driver(device.driver)
                secret = None
                if device.credentials:
                    if not self.secrets:
                        raise RuntimeError(
                            "device references credentials but no secrets "
                            "backend is configured"
                        )
                    secret = self.secrets.get(device.credentials)
                artifacts = driver.collect(device, secret)
                threshold = self.config.retention_for(device).get(
                    "large_file_threshold", 0
                )
                commit = self.store.write_and_commit(
                    device, artifacts,
                    blobstore=self.blobstore, threshold=threshold,
                )
                if commit:
                    log.info("%s: changed, commit %s", device.qualified_name, commit[:10])
                else:
                    log.info("%s: no change", device.qualified_name)
                return BackupResult(
                    device=device.qualified_name,
                    ok=True,
                    changed=commit is not None,
                    commit=commit,
                    message="changed" if commit else "no change",
                )
            except Exception as exc:
                log.error("%s: %s", device.qualified_name, exc)
                return BackupResult(
                    device=device.qualified_name, ok=False, message=str(exc)
                )
