import textwrap
import time

from otitbup.config import load_config
from otitbup.daemon import Daemon


class _FakeRunner:
    def __init__(self, config):
        self.config = config
        self.store = None

    def backup_devices(self, devices):
        return []


def _write(path, device_names):
    devices = "\n".join(
        f"                  - {{name: {n}, driver: cisco_ios}}"
        for n in device_names
    )
    path.write_text(textwrap.dedent("""\
        data_dir: ./data
        sites:
          - name: s
            zones:
              - name: z
                devices:
""") + devices + "\n")


def test_daemon_reloads_on_config_change(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    _write(cfg, ["a"])
    config = load_config(cfg)
    runner = _FakeRunner(config)
    daemon = Daemon(config, runner, config_path=str(cfg))

    assert len(daemon.config.all_devices()) == 1
    assert daemon.reload() is False        # unchanged

    time.sleep(0.01)
    _write(cfg, ["a", "b", "c"])
    assert daemon.reload() is True         # mtime changed -> reloaded
    assert len(daemon.config.all_devices()) == 3
    assert len(runner.config.all_devices()) == 3   # runner updated too


def test_daemon_keeps_old_config_on_broken_reload(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    _write(cfg, ["a"])
    config = load_config(cfg)
    daemon = Daemon(config, _FakeRunner(config), config_path=str(cfg))

    time.sleep(0.01)
    cfg.write_text("data_dir: ./data\nsites: [{name: s, zones: [{}]}]\n")
    assert daemon.reload() is False        # invalid -> not applied
    assert len(daemon.config.all_devices()) == 1   # old config retained


class _GcRunner(_FakeRunner):
    def __init__(self, config):
        super().__init__(config)
        self.gc_calls = 0

        class _Store:
            def repo_size_bytes(_self):
                return 1000

            def gc(_self, aggressive=False):
                self.gc_calls += 1
                return ""

        self.store = _Store()

        class _Alerts:
            def notify(self, *a, **k):
                pass

        self.alerts = _Alerts()


def test_daemon_periodic_gc(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""\
        data_dir: ./data
        housekeeping:
          gc_interval_days: 7
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {name: a, driver: cisco_ios}
    """))
    config = load_config(cfg)
    runner = _GcRunner(config)
    daemon = Daemon(config, runner, config_path=str(cfg))

    daemon._maybe_gc()
    assert runner.gc_calls == 1          # first run: due
    daemon._maybe_gc()
    assert runner.gc_calls == 1          # within interval: skipped


def test_daemon_gc_disabled_by_default(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    _write(cfg, ["a"])
    config = load_config(cfg)
    runner = _GcRunner(config)
    Daemon(config, runner, config_path=str(cfg))._maybe_gc()
    assert runner.gc_calls == 0
