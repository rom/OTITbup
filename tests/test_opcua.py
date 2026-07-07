"""Tests the OPC UA driver against a fake asyncua module."""
import sys
import types

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device

_NODE_VALUES = {
    "i=2261": "S7-1500 OPC UA Server",
    "i=2262": "urn:siemens:plc",
    "i=2263": "Siemens AG",
    "i=2264": "V3.1.4",
    "i=2265": "42",
    "i=2266": "2026-01-15",
    "ns=3;s=SerialNumber": "S C-ABC123",
}


class FakeNode:
    def __init__(self, node_id):
        self.node_id = node_id

    def read_value(self):
        if self.node_id not in _NODE_VALUES:
            raise RuntimeError("BadNodeIdUnknown")
        return _NODE_VALUES[self.node_id]


class FakeClient:
    connect_error = None
    last_endpoint = None
    last_user = None

    def __init__(self, endpoint, timeout=None):
        type(self).last_endpoint = endpoint

    def set_user(self, user):
        type(self).last_user = user

    def set_password(self, password):
        pass

    def connect(self):
        if type(self).connect_error:
            raise type(self).connect_error

    def disconnect(self):
        pass

    def get_node(self, node_id):
        return FakeNode(node_id)

    def get_namespace_array(self):
        return ["http://opcfoundation.org/UA/", "urn:siemens:plc"]


@pytest.fixture
def fake_asyncua(monkeypatch):
    root = types.ModuleType("asyncua")
    sync = types.ModuleType("asyncua.sync")
    sync.Client = FakeClient
    root.sync = sync
    monkeypatch.setitem(sys.modules, "asyncua", root)
    monkeypatch.setitem(sys.modules, "asyncua.sync", sync)
    FakeClient.connect_error = None
    return sync


def _device(options=None):
    return Device(
        name="plc-ua", driver="generic_opcua", site="plant-a", zone="cell-1",
        address="10.0.0.50", options=options or {},
    )


def test_collects_build_info_and_fingerprint(fake_asyncua):
    artifacts = get_driver("generic_opcua").collect(_device(), None)
    by_name = {a.name: a for a in artifacts}
    assert set(by_name) == {"server_info.yml", "fingerprint.yml"}
    assert FakeClient.last_endpoint == "opc.tcp://10.0.0.50:4840"

    info = yaml.safe_load(by_name["server_info.yml"].data)
    assert info["ProductName"] == "S7-1500 OPC UA Server"
    assert info["ManufacturerName"] == "Siemens AG"
    assert info["SoftwareVersion"] == "V3.1.4"
    assert "urn:siemens:plc" in info["NamespaceArray"]
    fp = yaml.safe_load(by_name["fingerprint.yml"].data)
    assert fp["server_sha256"]


def test_extra_nodes_and_credentials(fake_asyncua):
    device = _device({"nodes": {"serial": "ns=3;s=SerialNumber"}})
    artifacts = get_driver("generic_opcua").collect(
        device, {"username": "backup", "password": "pw"}
    )
    by_name = {a.name: a for a in artifacts}
    values = yaml.safe_load(by_name["values.yml"].data)
    assert values["serial"] == "S C-ABC123"
    assert FakeClient.last_user == "backup"


def test_fingerprint_changes_with_firmware(fake_asyncua):
    driver = get_driver("generic_opcua")
    first = {a.name: a.data for a in driver.collect(_device(), None)}
    _NODE_VALUES["i=2264"] = "V3.2.0"
    try:
        second = {a.name: a.data for a in driver.collect(_device(), None)}
    finally:
        _NODE_VALUES["i=2264"] = "V3.1.4"
    assert first["fingerprint.yml"] != second["fingerprint.yml"]


def test_connect_failure_is_driver_error(fake_asyncua):
    FakeClient.connect_error = ConnectionRefusedError("refused")
    with pytest.raises(DriverError, match="connect"):
        get_driver("generic_opcua").collect(_device(), None)
