"""Tests the SFTP file-fetch driver against a fake paramiko module with an
in-memory remote filesystem."""
import stat as stat_module
import sys
import types

import pytest

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device

_TREE = {
    "/home/codesys3/PlcLogic/Application/Application.app": b"BOOTAPP-v7",
    "/home/codesys3/PlcLogic/Application/Application.crc": b"cafe",
    "/home/codesys3/visu/style.css": b"body{}",
    "/opt/plcnext/projects/PCWE/PCWE.acf.config": b"<config/>",
    "/etc/device.settings": b"mode=run",
    "/var/huge.bin": b"\x00" * 4096,
}


class _Attr:
    def __init__(self, filename, mode, size):
        self.filename, self.st_mode, self.st_size = filename, mode, size


class FakeFile:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSFTP:
    def _dirs(self):
        dirs = set()
        for path in _TREE:
            parts = path.strip("/").split("/")
            for depth in range(1, len(parts)):
                dirs.add("/" + "/".join(parts[:depth]))
        dirs.add("/")
        return dirs

    def stat(self, path):
        path = path.rstrip("/") or "/"
        if path in _TREE:
            return _Attr(path, stat_module.S_IFREG, len(_TREE[path]))
        if path in self._dirs():
            return _Attr(path, stat_module.S_IFDIR, 0)
        raise FileNotFoundError(path)

    def listdir_attr(self, directory):
        directory = directory.rstrip("/") or "/"
        seen, entries = set(), []
        for path, data in _TREE.items():
            parent, _, name = path.rpartition("/")
            parent = parent or "/"
            if parent == directory and name not in seen:
                seen.add(name)
                entries.append(_Attr(name, stat_module.S_IFREG, len(data)))
        for sub in self._dirs():
            parent, _, name = sub.rpartition("/")
            parent = parent or "/"
            if sub != "/" and parent == directory and name not in seen:
                seen.add(name)
                entries.append(_Attr(name, stat_module.S_IFDIR, 0))
        return entries

    def open(self, path, mode="rb"):
        return FakeFile(_TREE[path])


class FakeSSHClient:
    connect_kwargs = None

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, address, **kwargs):
        type(self).connect_kwargs = {"address": address, **kwargs}

    def open_sftp(self):
        return FakeSFTP()

    def close(self):
        pass


@pytest.fixture
def fake_paramiko(monkeypatch):
    mod = types.ModuleType("paramiko")
    mod.SSHClient = FakeSSHClient
    mod.AutoAddPolicy = object
    monkeypatch.setitem(sys.modules, "paramiko", mod)
    return mod


_SECRETS = {"username": "admin", "password": "wago"}


def _device(driver, options=None):
    return Device(
        name="plc-lx", driver=driver, site="plant-a", zone="cell-7",
        address="10.0.0.70", options=options or {},
    )


def test_wago_fetches_runtime_dir_and_notes_missing_one(fake_paramiko):
    artifacts = get_driver("wago_pfc").collect(_device("wago_pfc"), _SECRETS)
    by_name = {a.name: a for a in artifacts}
    assert by_name[
        "home/codesys3/PlcLogic/Application/Application.app"
    ].data == b"BOOTAPP-v7"
    assert by_name["home/codesys3/visu/style.css"].kind == "project"
    # /home/codesys (Codesys 2) doesn't exist on this device: noted.
    notes = by_name["_collection_notes.txt"].data.decode()
    assert "path not found: /home/codesys" in notes


def test_plcnext_default_path(fake_paramiko):
    artifacts = get_driver("phoenix_plcnext").collect(
        _device("phoenix_plcnext"), _SECRETS
    )
    names = {a.name for a in artifacts}
    assert "opt/plcnext/projects/PCWE/PCWE.acf.config" in names


def test_glob_pattern_and_single_file(fake_paramiko):
    device = _device("codesys_ssh", {
        "paths": ["/home/codesys3/**/*.app", "/etc/device.settings"],
    })
    artifacts = get_driver("codesys_ssh").collect(device, _SECRETS)
    names = {a.name for a in artifacts}
    assert "home/codesys3/PlcLogic/Application/Application.app" in names
    assert "etc/device.settings" in names
    assert "home/codesys3/visu/style.css" not in names


def test_oversize_files_are_skipped_with_note(fake_paramiko):
    device = _device("generic_sftp", {
        "paths": ["/var/huge.bin", "/etc/device.settings"],
        "max_file_size": 1024,
    })
    artifacts = get_driver("generic_sftp").collect(device, _SECRETS)
    by_name = {a.name: a for a in artifacts}
    assert "var/huge.bin" not in by_name
    assert "skipped oversize" in by_name["_collection_notes.txt"].data.decode()


def test_nothing_fetched_is_driver_error(fake_paramiko):
    device = _device("generic_sftp", {"paths": ["/does/not/exist"]})
    with pytest.raises(DriverError, match="no files fetched"):
        get_driver("generic_sftp").collect(device, _SECRETS)


def test_paths_required_for_generic(fake_paramiko):
    with pytest.raises(DriverError, match="paths"):
        get_driver("codesys_ssh").collect(_device("codesys_ssh"), _SECRETS)


def test_credentials_required(fake_paramiko):
    with pytest.raises(DriverError, match="credentials"):
        get_driver("wago_pfc").collect(_device("wago_pfc"), None)
