"""Scheduler daemon: per-device interval schedules with maintenance-window
awareness (REQUIREMENTS.md section 9). Last-run state persists in
<data_dir>/../state.json (kept next to, not inside, the backup repo so it
doesn't pollute history).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import AppConfig
from .runner import Runner
from .windows import in_window, parse_interval

log = logging.getLogger("otitbup.daemon")

_POLL_SECONDS = 30


class Daemon:
    def __init__(self, config: AppConfig, runner: Runner):
        self.config = config
        self.runner = runner
        self.state_path = Path(config.data_dir).parent / "state.json"
        self.state: dict[str, str] = self._load_state()

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
        due = []
        for device in self.config.all_devices():
            zone = self.config.find_zone(device)
            if not in_window(zone.maintenance_window, now):
                continue
            last = self.state.get(device.qualified_name)
            if last is None:
                due.append(device)
                continue
            next_run = datetime.fromisoformat(last) + parse_interval(device.schedule)
            if now >= next_run:
                due.append(device)
        return due

    def run_forever(self) -> None:
        log.info(
            "daemon started: %d device(s), polling every %ds",
            len(self.config.all_devices()),
            _POLL_SECONDS,
        )
        while True:
            self.run_once()
            time.sleep(_POLL_SECONDS)

    def run_once(self) -> int:
        """One scheduler tick. Returns the number of devices backed up."""
        now = datetime.now(timezone.utc)
        due = self._due(now)
        if due:
            log.info("due: %s", ", ".join(d.qualified_name for d in due))
            self.runner.backup_devices(due)
            for device in due:
                self.state[device.qualified_name] = now.isoformat()
            self._save_state()
        return len(due)
