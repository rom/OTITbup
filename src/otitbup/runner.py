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
from .events import (ANOMALY, BACKUP_ERROR, BACKUP_START, BACKUP_STOP,
                     CHANGE_UNEXPECTED, EventBus, NullEventBus)
from .gitstore import GitStore
from .models import AppConfig, Device
from .runstore import RunRecord, RunStore, default_runstore
from .secrets import SecretsBackend


def default_blobstore(config: AppConfig) -> BlobStore:
    """The blob store lives next to (not inside) the backup repo, and is
    encrypted at rest when encryption.blob_key(_file) or OTITBUP_BLOB_KEY
    is configured."""
    import os
    enc = config.encryption or {}
    key = os.environ.get("OTITBUP_BLOB_KEY") or enc.get("blob_key")
    if not key and enc.get("blob_key_file"):
        key = Path(enc["blob_key_file"]).read_text().strip()
    return BlobStore(Path(config.data_dir).parent / "blobs", key=key)


def default_gitstore(config: AppConfig) -> GitStore:
    """A GitStore configured with the commit-signing key, if set."""
    sign_key = (config.git.get("sign") or {}).get("key_file")
    return GitStore(config.data_dir, sign_key=sign_key)

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
        dry_run: bool = False,
    ):
        self.config = config
        self.store = store
        self.secrets = secrets
        self.dry_run = dry_run
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
        if not self.force and not in_window(
            zone.maintenance_window, tz=zone.timezone
        ):
            return BackupResult(
                device=device.qualified_name,
                ok=True,
                message=f"skipped: outside maintenance window "
                        f"{zone.maintenance_window}",
            )
        # Was this device failing before this run? (for recovery alerts)
        was_failing = (
            self.runstore.status(device.qualified_name).consecutive_failures
            > 0
        )
        self.events.emit(
            BACKUP_START, f"backup started: {device.qualified_name}",
            detail=device.qualified_name,
        )
        self._run_hook(device, "pre")
        with limit:
            result = self._collect_with_retry(device, started)
        self._run_hook(device, "post", ok=result.ok)
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
            if was_failing:
                self.events.emit(
                    BACKUP_STOP,
                    f"backup RECOVERED: {device.qualified_name}",
                    detail=device.qualified_name,
                )
                self.alerts.notify(
                    f"otitbup: {device.qualified_name} recovered",
                    f"{device.qualified_name} backed up successfully after "
                    "prior failures.",
                )
        if not self.dry_run:
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
            self._check_anomalies(device)
        return result

    def _check_anomalies(self, device: Device) -> None:
        """Look for statistical anomalies in this device's fresh run history
        (slow-backup and change-storm signals) and emit an ANOMALY event for
        each — that fans out to alerts, syslog, SNMP, and tickets."""
        from . import anomaly
        cfg = self.config.anomaly
        if cfg.get("enabled") is False:
            return
        runs = self.runstore.recent_runs(
            device.qualified_name, limit=int(cfg.get("history", 50)))
        for finding in anomaly.analyze(device.qualified_name, runs, cfg):
            self.events.emit(
                ANOMALY,
                f"anomaly on {finding.device}: {finding.message}",
                severity="warning", detail=finding.device,
            )
            self.alerts.notify(
                f"otitbup: anomaly on {finding.device}",
                f"{finding.kind}: {finding.message}",
            )

    def _run_hook(self, device: Device, phase: str, ok: bool | None = None):
        """Run a configured pre/post hook (shell command). Hook config
        resolves device.hooks over the global config.hooks. Failures are
        logged, never fatal; env carries device context."""
        import os
        import subprocess
        spec = {**self.config.hooks, **device.hooks}
        cmd = spec.get(phase)
        if not cmd:
            return
        env = {
            **os.environ,
            "OTITBUP_DEVICE": device.qualified_name,
            "OTITBUP_SITE": device.site,
            "OTITBUP_ZONE": device.zone,
            "OTITBUP_DRIVER": device.driver,
            "OTITBUP_ADDRESS": device.address or "",
            "OTITBUP_PHASE": phase,
        }
        if ok is not None:
            env["OTITBUP_OK"] = "1" if ok else "0"
        try:
            subprocess.run(cmd, shell=True, env=env, timeout=120,
                           capture_output=True)
        except Exception as exc:
            log.warning("%s %s-hook failed: %s", device.qualified_name,
                        phase, exc)

    def _collect_with_retry(
        self, device: Device, started: float
    ) -> BackupResult:
        """Collect+store with retry/backoff on transient failure. Retries
        are config-driven (retry.attempts, retry.backoff seconds)."""
        attempts = max(1, int(self.config.retry.get("attempts", 1)))
        backoff = float(self.config.retry.get("backoff", 2.0))
        result = None
        for attempt in range(1, attempts + 1):
            result = self._collect_and_store(device, started)
            if result.ok:
                return result
            if attempt < attempts:
                delay = backoff * (2 ** (attempt - 1))
                log.info("%s: attempt %d/%d failed, retrying in %.0fs",
                         device.qualified_name, attempt, attempts, delay)
                time.sleep(delay)
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
            if self.dry_run:
                return BackupResult(
                    device=device.qualified_name, ok=True, changed=False,
                    message=f"dry run OK: {len(artifacts)} artifact(s) "
                            "collected (not committed)",
                )
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
