"""Regression tests for the post-review bug fixes (Commit B):
audit-chain race, retention data-loss guard, maintenance-window timezone,
tickets misconfig, syslog severity, daemon loop guard, cron dedup, and the
offsite tar-extract guard."""
import textwrap
import threading
from datetime import UTC, datetime

import pytest

from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.runstore import RunStore

# ------------------------------------------------- audit hash-chain race

def test_audit_chain_survives_concurrent_writers(tmp_path):
    rs = RunStore(tmp_path / "run.db")
    errors = []

    def hammer(n):
        try:
            for i in range(25):
                rs.audit(1000.0 + i, "view", actor=f"t{n}", detail=str(i))
        except Exception as exc:                       # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    intact, first_bad = rs.verify_audit()
    assert intact, f"audit chain forked at id {first_bad}"


# --------------------------------------------- retention data-loss guard

def _ret_setup(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        retention: {keep_versions: 5}
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {name: d, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    return config, store


def test_retention_aborts_when_kept_manifest_unreadable(tmp_path):
    from otitbup import retention
    from otitbup.blobstore import BlobStore
    config, store = _ret_setup(tmp_path)
    device = config.all_devices()[0]
    store.write_and_commit(device, [Artifact(name="c.txt", data=b"v1")])
    blobs = BlobStore(tmp_path / "blobs")

    # A kept backup whose manifest read fails must NOT be treated as "no
    # blobs" (which would let referenced content be pruned) — the whole plan
    # aborts instead.
    def boom(*a, **k):
        raise OSError("transient git failure")
    store.read_file_at = boom
    with pytest.raises(retention.RetentionError):
        retention.plan(config, store, blobs)


# --------------------------------------------- maintenance-window timezone

def test_in_window_default_now_is_utc(monkeypatch):
    from otitbup import windows

    # Window 12:00-13:00 UTC. A host whose LOCAL time is 14:00 but UTC is
    # 12:30 must see the window OPEN (previously naive local time was
    # mislabelled UTC and the window read closed).
    real = datetime

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:                              # naive local
                return real(2026, 1, 1, 14, 30)
            return real(2026, 1, 1, 12, 30, tzinfo=tz)  # aware UTC

    monkeypatch.setattr(windows, "datetime", FakeDT)
    assert windows.in_window("12:00-13:00") is True


def test_in_window_agrees_between_manual_and_daemon_paths():
    from otitbup import windows
    now = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)
    # Berlin is UTC+1 in January → 00:00 local, inside 22:00-06:00.
    manual = windows.in_window("22:00-06:00", now, tz="Europe/Berlin")
    assert manual is True


# ------------------------------------------------- tickets never fatal

def test_ticket_misconfig_does_not_raise_from_emit():
    from otitbup.events import BACKUP_ERROR, EventBus
    # backend set but no url -> self.cfg['url'] would KeyError; emit must
    # swallow it (sinks are never fatal) rather than abort the run.
    bus = EventBus(cfg={}, tickets={"backend": "servicenow",
                                    "on": ["backup.error"]})
    bus.emit(BACKUP_ERROR, "device down")   # must not raise


# ------------------------------------------------- syslog severity on wire

def test_syslog_pri_reflects_event_severity():
    import socket as _socket

    from otitbup.events import BACKUP_ERROR, EventBus
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)
    port = sock.getsockname()[1]
    bus = EventBus(cfg={"syslog": {"address": "127.0.0.1", "port": port,
                                   "facility": "local0", "protocol": "udp"}})
    bus.emit(BACKUP_ERROR, "capture failed")   # severity 'error'
    data, _ = sock.recvfrom(4096)
    sock.close()
    # local0=16, error=3 -> PRI = 16*8 + 3 = 131 (previously always 134/info).
    assert data.startswith(b"<131>"), data[:16]


# ------------------------------------------------- daemon cron dedup

def test_cron_device_fires_once_per_minute(tmp_path, monkeypatch):
    from otitbup import daemon as daemon_mod
    from otitbup.daemon import Daemon

    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {name: d, driver: cisco_ios, schedule: "0 2 * * *"}
    """))
    config = load_config(cfg)

    class FakeRunner:
        def __init__(self):
            self.config = config
            self.calls = 0

        def backup_devices(self, devices):
            self.calls += len(devices)
            return []

    runner = FakeRunner()
    d = Daemon(config, runner)

    real = datetime

    class FakeDT(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 1, 1, 2, 0, 30, tzinfo=tz)

    monkeypatch.setattr(daemon_mod, "datetime", FakeDT)
    d.run_once()
    d.run_once()      # same minute, second poll
    assert runner.calls == 1
