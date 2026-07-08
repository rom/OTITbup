import textwrap
import time

from otitbup.config import load_config
from otitbup.drivers import register
from otitbup.drivers.base import Artifact, Driver
from otitbup.gitstore import GitStore
from otitbup.retention import plan
from otitbup.runner import default_blobstore
from otitbup.runstore import default_runstore


class _BigDriver(Driver):
    name = "hold_fake"
    _n = 0

    def collect(self, device, secrets):
        _BigDriver._n += 1
        return [Artifact(name="p.bin", data=b"X" * 2000 + bytes([_BigDriver._n]))]


def _setup(tmp_path):
    register("hold_fake", _BigDriver)
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        retention: {keep_versions: 1, large_file_threshold: 100}
        sites:
          - name: s
            zones: [{name: z, devices: [{name: d1, driver: hold_fake}]}]
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    from otitbup.runner import Runner
    runner = Runner(config, store, force=True)
    # Three distinct backups -> three offloaded blobs; keep_versions=1 makes
    # the two older ones deletable.
    for _ in range(3):
        runner.backup_devices(config.all_devices())
    return config, store


def test_legal_hold_protects_all_blobs(tmp_path):
    config, store = _setup(tmp_path)
    blobstore = default_blobstore(config)
    runstore = default_runstore(config)

    # Without a hold, older versions are deletable.
    p = plan(config, store, blobstore, runstore=runstore)
    assert p.deletable, "expected some prunable blobs"

    # With a hold on the device, nothing is deletable.
    runstore.set_hold("s/z/d1", time.time(), reason="incident")
    p2 = plan(config, store, blobstore, runstore=runstore)
    assert p2.deletable == {}
    assert p2.devices[0].held is True

    runstore.clear_hold("s/z/d1")
    assert plan(config, store, blobstore, runstore=runstore).deletable


def test_site_glob_hold(tmp_path):
    config, store = _setup(tmp_path)
    runstore = default_runstore(config)
    runstore.set_hold("s/*", time.time())
    assert runstore.is_held("s/z/d1")
    p = plan(config, store, default_blobstore(config), runstore=runstore)
    assert p.deletable == {}


def test_retention_lock_days_keeps_recent(tmp_path):
    config, store = _setup(tmp_path)
    # Re-load with a lock window covering all just-made backups.
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(cfg.read_text().replace(
        "retention: {keep_versions: 1, large_file_threshold: 100}",
        "retention: {keep_versions: 1, large_file_threshold: 100, "
        "lock_days: 3650}"))
    config = load_config(cfg)
    p = plan(config, store, default_blobstore(config))
    assert p.deletable == {}          # everything within the lock window
