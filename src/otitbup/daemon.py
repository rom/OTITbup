"""Scheduler daemon: per-device interval schedules with maintenance-window
awareness (docs/REQUIREMENTS.md section 9). Last-run state persists in
<data_dir>/../state.json (kept next to, not inside, the backup repo so it
doesn't pollute history).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import AppConfig
from .runner import Runner
from .windows import in_window, parse_interval

log = logging.getLogger("otitbup.daemon")

_POLL_SECONDS = 30


class Daemon:
    def __init__(
        self, config: AppConfig, runner: Runner,
        config_path: str | None = None, events=None,
    ):
        self.config = config
        self.runner = runner
        self.config_path = config_path
        self.events = events
        self._config_mtime = self._mtime()
        self.state_path = Path(config.data_dir).parent / "state.json"
        self.state: dict[str, str] = self._load_state()

    def _mtime(self) -> float:
        if not self.config_path:
            return 0.0
        try:
            return Path(self.config_path).stat().st_mtime
        except OSError:
            return 0.0

    def reload(self) -> bool:
        """Re-read the config file if it changed. Returns True on reload.
        The inventory, schedules, retention, alerts and policy all take
        effect on the next tick without a restart."""
        if not self.config_path:
            return False
        current = self._mtime()
        if current == self._config_mtime:
            return False
        from .config import ConfigError, load_config
        try:
            new_config = load_config(self.config_path)
        except ConfigError as exc:
            log.warning("config reload failed, keeping old config: %s", exc)
            self._config_mtime = current  # don't retry the same broken file
            return False
        self.config = new_config
        self.runner.config = new_config
        self._config_mtime = current
        log.info(
            "config reloaded: %d device(s)", len(new_config.all_devices())
        )
        if self.events is not None:
            from .events import CONFIG_RELOAD
            self.events.emit(
                CONFIG_RELOAD,
                f"config reloaded: {len(new_config.all_devices())} device(s)",
                detail=self.config_path,
            )
        return True

    def _load_state(self) -> dict[str, str]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except json.JSONDecodeError:
                log.warning("state file corrupt, starting fresh")
        return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def _due(self, now: datetime) -> list:
        from .windows import cron_matches, is_cron
        due = []
        for device in self.config.all_devices():
            zone = self.config.find_zone(device)
            if not in_window(zone.maintenance_window, now, tz=zone.timezone):
                continue
            last = self.state.get(device.qualified_name)
            if is_cron(device.schedule):
                # Cron fires on the matching minute; the poll interval is
                # coarse, so fire at most once per matching minute.
                if not cron_matches(device.schedule, now):
                    continue
                minute_key = now.strftime("%Y%m%d%H%M")
                if last == "cron:" + minute_key:
                    continue
                self.state[device.qualified_name] = "cron:" + minute_key
                due.append(device)
                continue
            if last is None or last.startswith("cron:"):
                due.append(device)
                continue
            next_run = datetime.fromisoformat(last) + parse_interval(
                device.schedule)
            if now >= next_run:
                due.append(device)
        return due

    def run_forever(self) -> None:
        log.info(
            "daemon started: %d device(s), polling every %ds",
            len(self.config.all_devices()),
            _POLL_SECONDS,
        )
        self._install_sighup()
        while True:
            self.reload()  # pick up config edits without a restart
            self.run_once()
            self._maybe_report()
            self._maybe_gc()
            time.sleep(_POLL_SECONDS)

    def _install_sighup(self) -> None:
        """SIGHUP forces an immediate reload on the next tick (edit then
        `kill -HUP <pid>`), in addition to automatic mtime detection."""
        try:
            import signal

            def _handler(signum, frame):
                self._config_mtime = -1.0  # force reload() to fire

            signal.signal(signal.SIGHUP, _handler)
        except (ImportError, ValueError, AttributeError):
            pass  # no SIGHUP (e.g. Windows, or not main thread)

    def run_once(self) -> int:
        """One scheduler tick. Returns the number of devices backed up."""
        now = datetime.now(UTC)
        due = self._due(now)
        if due:
            log.info("due: %s", ", ".join(d.qualified_name for d in due))
            self.runner.backup_devices(due)
            for device in due:
                self.state[device.qualified_name] = now.isoformat()
            self._save_state()
        return len(due)

    def _maybe_report(self) -> None:
        """Emit a scheduled compliance report every reports.interval (e.g.
        '7d'), delivered via the same alert channels (email/webhook)."""
        interval = self.config.reports.get("interval")
        if not interval:
            return
        now = datetime.now(UTC)
        last = self.state.get("__report__")
        if last is not None:
            due_at = datetime.fromisoformat(last) + parse_interval(interval)
            if now < due_at:
                return
        try:
            from .reports import compliance_report
            from .runstore import default_runstore
            html = compliance_report(
                self.config, self.runner.store, default_runstore(self.config),
                period_days=int(self.config.reports.get("period_days", 30)),
            )
            out = self.config.reports.get("out")
            if out:
                Path(out).write_text(html)
            self.runner.alerts.notify(
                "otitbup: scheduled compliance report",
                "Compliance report generated"
                + (f" at {out}" if out else "")
                + f" ({len(self.config.all_devices())} devices).",
            )
            log.info("scheduled compliance report generated")
        except Exception as exc:
            log.warning("scheduled report failed: %s", exc)
        self.state["__report__"] = now.isoformat()
        self._save_state()

    def _maybe_gc(self) -> None:
        """Run `git gc` every housekeeping.gc_interval_days to keep the
        backup repo compact. 0/unset disables."""
        days = int(self.config.housekeeping.get("gc_interval_days", 0))
        if days <= 0:
            return
        now = datetime.now(UTC)
        last = self.state.get("__gc__")
        if last is not None:
            due_at = datetime.fromisoformat(last) + timedelta(days=days)
            if now < due_at:
                return
        try:
            before = self.runner.store.repo_size_bytes()
            self.runner.store.gc(
                aggressive=bool(self.config.housekeeping.get("gc_aggressive")))
            after = self.runner.store.repo_size_bytes()
            log.info("housekeeping git gc: %.1f -> %.1f MiB",
                     before / 1048576, after / 1048576)
        except Exception as exc:
            log.warning("housekeeping git gc failed: %s", exc)
        self.state["__gc__"] = now.isoformat()
        self._save_state()
