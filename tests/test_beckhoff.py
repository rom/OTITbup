"""Tests the Beckhoff ADS driver against a fake pyads module."""
import sys
import types

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device


class FakeAdsVersion:
    version, revision, build = 3, 1, 4024


class FakeConnection:
    open_error = None
    last_args = None

    def __init__(self, ams_net_id, ams_port, ip_address=None):
        type(self).last_args = (ams_net_id, ams_port, ip_address)

    def open(self):
        if type(self).open_error:
            raise type(self).open_error

    def close(self):
        pass

    def read_device_info(self):
        return "TwinCAT PLC", FakeAdsVersion()

    def read_state(self):
        return 5, 0  # ADSSTATE_RUN


@pytest.fixture
def fake_pyads(monkeypatch):
    mod = types.ModuleType("pyads")
    mod.Connection = FakeConnection
    monkeypatch.setitem(sys.modules, "pyads", mod)
    FakeConnection.open_error = None
    return mod


def _device(options=None):
    return Device(
        name="plc-bk", driver="beckhoff_ads", site="plant-a", zone="cell-6",
        address="10.0.0.60", options=options or {},
    )


def test_reads_device_info_and_state(fake_pyads):
    artifacts = get_driver("beckhoff_ads").collect(_device(), None)
    by_name = {a.name: a for a in artifacts}
    info = yaml.safe_load(by_name["device_info.yml"].data)
    assert info["device_name"] == "TwinCAT PLC"
    assert info["ads_version"] == "3.1.4024"
    assert info["ads_state"] == "Run"
    assert info["ams_net_id"] == "10.0.0.60.1.1"  # derived from address
    assert yaml.safe_load(by_name["fingerprint.yml"].data)["device_sha256"]


def test_ams_options_override(fake_pyads):
    device = _device({"ams_net_id": "5.1.2.3.1.1", "ams_port": 10000})
    get_driver("beckhoff_ads").collect(device, None)
    assert FakeConnection.last_args == ("5.1.2.3.1.1", 10000, "10.0.0.60")


def test_no_route_is_clear_error(fake_pyads):
    FakeConnection.open_error = ConnectionRefusedError("target refused")
    with pytest.raises(DriverError, match="ADS route"):
        get_driver("beckhoff_ads").collect(_device(), None)
