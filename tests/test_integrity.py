import glob
import textwrap
import time

from otitbup import integrity
from otitbup.config import load_config
from otitbup.drivers import register
from otitbup.drivers.base import Artifact, Driver
from otitbup.gitstore import GitStore
from otitbup.runner import Runner
from otitbup.runstore import default_runstore


class _D(Driver):
    name = "integ_fake"

    def collect(self, device, secrets):
        return [Artifact(name="c.txt", data=b"hostname r1\ninterface x\n")]


def _setup(tmp_path):
    register("integ_fake", _D)
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: s
            zones: [{name: z, devices: [{name: d1, driver: integ_fake}]}]
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    Runner(config, store, force=True).backup_devices(config.all_devices())
    return config, store


def test_fsck_clean_then_detects_corruption(tmp_path):
    config, store = _setup(tmp_path)
    assert store.fsck() == []
    objs = glob.glob(str(config.data_dir) + "/.git/objects/??/*")
    with open(objs[0], "r+b") as f:
        f.truncate(1)
    assert store.fsck()          # corruption detected


def test_integrity_check_ok(tmp_path):
    config, store = _setup(tmp_path)
    result = integrity.check(config, store)
    assert result.ok
    assert result.checked_devices == 1
    assert "OK" in result.summary()


def test_integrity_detects_repo_corruption(tmp_path):
    config, store = _setup(tmp_path)
    objs = glob.glob(str(config.data_dir) + "/.git/objects/??/*")
    with open(objs[0], "r+b") as f:
        f.truncate(1)
    result = integrity.check(config, store)
    assert not result.ok
    assert result.repo_problems
    assert "FAILED" in result.summary()


def test_run_scheduled_persists_and_alerts(tmp_path):
    config, store = _setup(tmp_path)
    runstore = default_runstore(config)
    events, alerts = [], []

    class _E:
        def emit(self, t, m, **k):
            events.append(t)

    class _A:
        def notify(self, s, b):
            alerts.append(s)

    # Healthy -> integrity.ok event, no alert, meta persisted.
    res = integrity.run_scheduled(config, store, None, runstore, _E(), _A(),
                                  time.time())
    assert res.ok
    assert "integrity.ok" in events
    assert not alerts
    import json
    meta = json.loads(runstore.get_meta("integrity")["value"])
    assert meta["ok"] is True

    # Corrupt the repo -> integrity.error event + alert.
    objs = glob.glob(str(config.data_dir) + "/.git/objects/??/*")
    with open(objs[0], "r+b") as f:
        f.truncate(1)
    events.clear()
    res = integrity.run_scheduled(config, store, None, runstore, _E(), _A(),
                                  time.time())
    assert not res.ok
    assert "integrity.error" in events
    assert alerts
