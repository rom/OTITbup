import textwrap
import time

import pytest

from otitbup.baseline import all_drift, device_drift, drift_diff
from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.runstore import RunStore


@pytest.fixture
def env(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: s
            zones:
              - name: net
                devices:
                  - {name: sw-01, driver: cisco_ios}
                  - {name: sw-02, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    runstore = RunStore(tmp_path / "runstore.db")
    return config, store, runstore


def test_baseline_drift_lifecycle(env):
    config, store, runstore = env
    sw01 = config.all_devices()[0]

    store.write_and_commit(sw01, [Artifact(
        name="show_running_config.txt", data=b"hostname sw-01\nvlan 10\n")])
    base_commit = store.last_commit_hash(sw01)

    # No baseline yet.
    assert not device_drift(store, runstore, sw01).has_baseline

    runstore.set_baseline(sw01.qualified_name, base_commit, time.time())
    drift = device_drift(store, runstore, sw01)
    assert drift.has_baseline and not drift.drifted

    # A new backup that differs -> drift.
    store.write_and_commit(sw01, [Artifact(
        name="show_running_config.txt", data=b"hostname sw-01\nvlan 20\n")])
    drift = device_drift(store, runstore, sw01)
    assert drift.drifted
    assert "vlan 20" in drift_diff(store, runstore, sw01)

    # Re-approve the new state -> no drift.
    runstore.set_baseline(
        sw01.qualified_name, store.last_commit_hash(sw01), time.time())
    assert not device_drift(store, runstore, sw01).drifted


def test_all_drift_counts(env):
    config, store, runstore = env
    for device in config.all_devices():
        store.write_and_commit(device, [Artifact(name="c.txt", data=b"x")])
    sw01 = config.all_devices()[0]
    runstore.set_baseline(sw01.qualified_name, "0" * 40, time.time())  # stale
    drifts = {d.device: d for d in all_drift(config, store, runstore)}
    assert drifts["s/net/sw-01"].drifted is True
    assert drifts["s/net/sw-02"].has_baseline is False


def test_search_across_latest_backups(env):
    config, store, runstore = env
    sw01, sw02 = config.all_devices()
    store.write_and_commit(sw01, [Artifact(
        name="show_running_config.txt", data=b"hostname sw-01\nvlan 30\n")])
    store.write_and_commit(sw02, [Artifact(
        name="show_running_config.txt", data=b"hostname sw-02\nvlan 40\n")])

    hits = store.search("vlan 30")
    assert len(hits) == 1
    assert "sw-01" in hits[0][0]

    hits = store.search("hostname")
    assert len(hits) == 2

    # Case-insensitive by default.
    assert store.search("VLAN 40")
    assert not store.search("VLAN 40", ignore_case=False)


def test_diff_between_commits(env):
    config, store, runstore = env
    sw01 = config.all_devices()[0]
    store.write_and_commit(sw01, [Artifact(name="c.txt", data=b"v1\n")])
    first = store.last_commit_hash(sw01)
    store.write_and_commit(sw01, [Artifact(name="c.txt", data=b"v2\n")])
    second = store.last_commit_hash(sw01)
    diff = store.diff_between(sw01, first, second)
    assert "-v1" in diff and "+v2" in diff
