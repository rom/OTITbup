from datetime import datetime

import pytest

from otitbup import drivers
from otitbup.config import load_config
from otitbup.drivers.base import Artifact, Driver
from otitbup.gitstore import GitStore
from otitbup.runner import Runner


class FakeDriver(Driver):
    name = "fake"
    payload = b"v1"

    def collect(self, device, secrets):
        return [Artifact(name="config.txt", data=type(self).payload)]


class RecordingAlerts:
    def __init__(self):
        self.notifications = []

    def notify(self, subject, body):
        self.notifications.append((subject, body))


@pytest.fixture(autouse=True)
def register_fake_driver():
    drivers.register("fake", FakeDriver)
    FakeDriver.payload = b"v1"


def _runner(config, force=True):
    alerts = RecordingAlerts()
    runner = Runner(
        config, GitStore(config.data_dir), alerts=alerts, force=force
    )
    return runner, alerts


def test_backup_commits_and_alerts_on_change(config_file):
    config = load_config(config_file)
    runner, alerts = _runner(config)

    results = runner.backup_devices(config.all_devices())
    assert all(r.ok for r in results)
    assert all(r.changed for r in results)
    assert len(alerts.notifications) == 1
    assert "changes detected" in alerts.notifications[0][0]

    # Second run, nothing changed: no commit, no alert.
    results = runner.backup_devices(config.all_devices())
    assert all(r.ok and not r.changed for r in results)
    assert len(alerts.notifications) == 1

    # Content changes: commit + alert again.
    FakeDriver.payload = b"v2"
    results = runner.backup_devices(config.all_devices())
    assert all(r.changed for r in results)
    assert len(alerts.notifications) == 2


def test_driver_failure_is_reported_not_raised(config_file):
    class BrokenDriver(Driver):
        name = "fake"

        def collect(self, device, secrets):
            raise RuntimeError("boom")

    drivers.register("fake", BrokenDriver)
    config = load_config(config_file)
    runner, alerts = _runner(config)
    results = runner.backup_devices(config.all_devices())
    assert all(not r.ok for r in results)
    assert any("failed" in s for s, _ in alerts.notifications)


def test_maintenance_window_skips_outside(config_file, monkeypatch):
    config = load_config(config_file)
    runner, _ = _runner(config, force=False)

    import otitbup.windows as windows
    real_in_window = windows.in_window
    noon = datetime(2026, 7, 7, 12, 0)
    monkeypatch.setattr(
        windows, "in_window", lambda spec, now=None: real_in_window(spec, noon)
    )

    results = runner.backup_devices(config.all_devices())
    by_device = {r.device: r for r in results}
    # cell-1 window is 22:00-06:00 -> skipped at noon; network has no window.
    assert "outside maintenance window" in by_device["plant-a/cell-1/plc-01"].message
    assert by_device["plant-a/network/sw-01"].changed
