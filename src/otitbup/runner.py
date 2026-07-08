"""Backup orchestration.

Respects OT safety constraints (docs/REQUIREMENTS.md section 10): per-zone
maintenance windows and per-zone concurrency caps so backup traffic cannot
disturb control traffic.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pathlib import Path

from .alerts import AlertManager
from .blobstore import BlobStore
from .drivers import get_driver
from .events import (BACKUP_ERROR, BACKUP_START, BACKUP_STOP,
                     CHANGE_UNEXPECTED, EventBus, NullEventBus)
from .gitstore import GitStore
from .models import AppConfig, Device
from .runstore import RunRecord, RunStore, default_runstore
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
    expected: bool | None = None   # for a change: was it during maintenance?


class Runner:
    def __init__(
        self,
        config: AppConfig,
        store: GitStore,
        secrets: SecretsBackend | None = None,
        alerts: AlertManager | None = None,
        force: bool = False,
        runstore: RunStore | None = None,
        events: EventBus | None = None,
    ):
        self.config = config
        self.store = store
        self.secrets = secrets
        self.alerts = alerts or AlertManager(
            config.alerts,
            state_path=Path(config.data_dir).parent / "alert-state.json",
        )
        self.force = force
        self.blobstore = default_blobstore(config)
        self.runstore = runstore or default_runstore(config)
        self.events = events or NullEventBus()

    def backup_devices(self, devices: list[Device]) -> list[BackupResult]:
        from .filelock import FileLock, LockBusy
        lock = FileLock(Path(self.config.data_dir).parent / "otitbup.lock")
        try:
            lock.acquire(blocking=False)
        except LockBusy as exc:
            log.warning("backup skipped: %s", exc)
            return [
                BackupResult(
                    device=d.qualified_name, ok=True,
                    message="skipped: another backup is in progress",
                )
                for d in devices
            ]
        try:
            return self._backup_devices_locked(devices)
        finally:
            lock.release()

    def _backup_devices_locked(
        self, devices: list[Device]
    ) -> list[BackupResult]:
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

        # Unexpected changes (detected outside a maintenance window) are
        # called out separately — that is the unauthorized-change signal.
        unexpected = [r for r in results if r.changed and r.expected is False]
        expected = [r for r in results if r.changed and r.expected]
        failed = [r for r in results if not r.ok]
        if unexpected:
            names = ", ".join(r.device for r in unexpected)
            body = "\n".join(
                f"{r.device}: commit {r.commit} (NOT in maintenance)"
                for r in unexpected
            )
            self.alerts.notify(
                f"otitbup: UNEXPECTED changes on {names}", body
            )
            for r in unexpected:
                # Distinct event so ticketing/SNMP can target it.
                self.events.emit(
                    CHANGE_UNEXPECTED,
                    f"unexpected change on {r.device} (commit {r.commit})",
                    severity="warning", detail=r.device,
                )
        if expected:
            names = ", ".join(r.device for r in expected)
            body = "\n".join(
                f"{r.device}: commit {r.commit} (maintenance)"
                for r in expected
            )
            self.alerts.notify(
                f"otitbup: changes on {names} (expected, maintenance)", body
            )
        if failed:
            body = "\n".join(f"{r.device}: {r.message}" for r in failed)
            self.alerts.notify(
                f"otitbup: {len(failed)} backup(s) failed", body
            )
        self._alert_stale(devices)

        if self.config.git.get("push") and self.config.git.get("remote"):
            try:
                self.store.push(self.config.git["remote"])
            except Exception as exc:
                log.warning("push to remote failed: %s", exc)

        return results

    def _alert_stale(self, devices: list[Device]) -> None:
        """Alert on devices whose last SUCCESSFUL backup is older than
        alerts.stale_days (default 7; 0 disables). Silent failure is the
        classic way backups rot — this is the catch-all."""
        stale_days = int(self.config.alerts.get("stale_days", 7))
        if stale_days <= 0:
            return
        cutoff = time.time() - stale_days * 86400
        stale = []
        for device in devices:
            status = self.runstore.status(device.qualified_name)
            if status.last_success is None or status.last_success < cutoff:
                stale.append(device.qualified_name)
        if stale:
            self.alerts.notify(
                f"otitbup: {len(stale)} device(s) with no successful backup "
                f"in {stale_days}d",
                "\n".join(stale),
            )

    def _backup_one(
        self, device: Device, limit: threading.Semaphore
    ) -> BackupResult:
        from .windows import in_window

        started = time.time()
        zone = self.config.find_zone(device)
        if not self.force and not in_window(zone.maintenance_window):
            return BackupResult(
                device=device.qualified_name,
                ok=True,
                message=f"skipped: outside maintenance window "
                        f"{zone.maintenance_window}",
            )
        self.events.emit(
            BACKUP_START, f"backup started: {device.qualified_name}",
            detail=device.qualified_name,
        )
        with limit:
            result = self._collect_and_store(device, started)
        if not result.ok:
            self.events.emit(
                BACKUP_ERROR,
                f"backup failed: {device.qualified_name}: {result.message}",
                severity="error", detail=device.qualified_name,
            )
        else:
            self.events.emit(
                BACKUP_STOP,
                f"backup finished: {device.qualified_name} ({result.message})",
                detail=device.qualified_name,
            )
        self.runstore.record_run(RunRecord(
            device=result.device,
            started_at=started,
            finished_at=time.time(),
            ok=result.ok,
            changed=result.changed,
            commit_hash=result.commit,
            message=result.message,
            expected=result.expected,
        ))
        return result

    def _collect_and_store(
        self, device: Device, started: float
    ) -> BackupResult:
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
            expected = None
            if commit:
                expected = self.runstore.in_maintenance(
                    device.qualified_name, started
                )
                log.info(
                    "%s: changed, commit %s%s", device.qualified_name,
                    commit[:10], "" if expected else " (UNEXPECTED)",
                )
            else:
                log.info("%s: no change", device.qualified_name)
            return BackupResult(
                device=device.qualified_name,
                ok=True,
                changed=commit is not None,
                commit=commit,
                message="changed" if commit else "no change",
                expected=expected,
            )
        except Exception as exc:
            log.error("%s: %s", device.qualified_name, exc)
            return BackupResult(
                device=device.qualified_name, ok=False, message=str(exc)
            )
