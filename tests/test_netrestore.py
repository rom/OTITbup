import sys
import types

import pytest

from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.models import Device
from otitbup.netrestore import NetRestoreError, restore_network_config


class FakeConnection:
    running = "hostname old\n!\ninterface Gi0/1\n"
    config_sets = []

    def __init__(self, **params):
        type(self).last_params = params

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def enable(self):
        pass

    def send_command(self, command):
        return type(self).running

    def send_config_set(self, lines):
        type(self).config_sets.append(lines)
        # Simulate the device adopting the pushed config.
        type(self).running = "\n".join(lines) + "\n"

    def save_config(self):
        pass


@pytest.fixture
def fake_netmiko(monkeypatch):
    mod = types.ModuleType("netmiko")
    mod.ConnectHandler = FakeConnection
    monkeypatch.setitem(sys.modules, "netmiko", mod)
    FakeConnection.running = "hostname old\n!\ninterface Gi0/1\n"
    FakeConnection.config_sets = []
    return mod


@pytest.fixture
def store_with_config(tmp_path):
    store = GitStore(tmp_path / "data")
    store.ensure_repo()
    device = Device(name="sw-01", driver="cisco_ios", site="s", zone="net",
                    address="10.0.0.1", credentials="sw-01")
    store.write_and_commit(device, [Artifact(
        name="show_running_config.txt",
        data=b"hostname new\n!\ninterface Gi0/1\n description uplink\n",
    )])
    return store, device


_SECRET = {"username": "u", "password": "p"}


def test_dry_run_pushes_nothing(fake_netmiko, store_with_config):
    store, device = store_with_config
    result = restore_network_config(store, device, _SECRET, apply=False)
    assert result.applied is False
    assert "hostname new" in result.candidate_config
    assert "hostname old" in result.pre_config
    assert FakeConnection.config_sets == []   # nothing pushed


def test_apply_pushes_and_verifies(fake_netmiko, store_with_config):
    store, device = store_with_config
    result = restore_network_config(store, device, _SECRET, apply=True)
    assert result.applied is True
    assert FakeConnection.config_sets       # config was pushed
    # Banner/comment lines are filtered out of the push.
    pushed = FakeConnection.config_sets[0]
    assert "hostname new" in pushed
    assert not any(line.startswith("!") for line in pushed)
    assert result.verified is True


def test_non_network_driver_refused(fake_netmiko, tmp_path):
    store = GitStore(tmp_path / "data")
    store.ensure_repo()
    device = Device(name="plc", driver="siemens_s7", site="s", zone="z",
                    address="10.0.0.2")
    with pytest.raises(NetRestoreError, match="not.*eligible"):
        restore_network_config(store, device, _SECRET)


def test_no_backup_refused(fake_netmiko, tmp_path):
    store = GitStore(tmp_path / "data")
    store.ensure_repo()
    device = Device(name="sw", driver="cisco_ios", site="s", zone="net",
                    address="10.0.0.3")
    with pytest.raises(NetRestoreError, match="no backups"):
        restore_network_config(store, device, _SECRET)
