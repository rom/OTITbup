"""Tests the Rockwell driver against a fake pycomm3 module."""
import sys
import types

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device


class FakeLogixDriver:
    tag_list = [
        {"tag_name": "Motor_Speed", "data_type_name": "DINT", "dim": 0},
        {"tag_name": "Recipe", "data_type_name": "RECIPE_UDT", "dim": 1,
         "dimensions": [10], "external_access": "Read/Write"},
    ]

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def info(self):
        return {
            "product_name": "1756-L83E/B",
            "product_code": 166,
            "vendor": "Rockwell Automation/Allen-Bradley",
            "serial": "c00fefe1",
            "revision": {"major": 33, "minor": 11},
            "keyswitch": "REMOTE RUN",
            "name": "Line3_PLC",
            "programs": {"MainProgram": {}, "Safety": {}},
            "tasks": {"MainTask": {}},
        }

    def get_tag_list(self):
        return list(type(self).tag_list)


@pytest.fixture
def fake_pycomm3(monkeypatch):
    mod = types.ModuleType("pycomm3")
    mod.LogixDriver = FakeLogixDriver
    monkeypatch.setitem(sys.modules, "pycomm3", mod)
    return mod


@pytest.fixture
def device():
    return Device(
        name="plc-02", driver="rockwell_enip", site="plant-a", zone="cell-2",
        address="10.0.0.20", options={"slot": 1},
    )


def test_collects_info_tags_and_fingerprint(fake_pycomm3, device):
    artifacts = get_driver("rockwell_enip").collect(device, None)
    by_name = {a.name: a for a in artifacts}
    assert set(by_name) == {"controller_info.yml", "tags.yml", "fingerprint.yml"}

    info = yaml.safe_load(by_name["controller_info.yml"].data)
    assert info["name"] == "Line3_PLC"
    assert info["revision"] == "33.11"
    assert info["programs"] == ["MainProgram", "Safety"]
    assert info["keyswitch"] == "REMOTE RUN"

    tags = yaml.safe_load(by_name["tags.yml"].data)
    assert tags["Motor_Speed"]["data_type"] == "DINT"
    assert tags["Recipe"]["dim"] == [10]

    fp = yaml.safe_load(by_name["fingerprint.yml"].data)
    assert fp["tag_count"] == 2
    assert fp["program_sha256"]


def test_fingerprint_changes_with_tags(fake_pycomm3, device):
    driver = get_driver("rockwell_enip")
    first = {a.name: a.data for a in driver.collect(device, None)}
    original = FakeLogixDriver.tag_list
    FakeLogixDriver.tag_list = original + [
        {"tag_name": "NewTag", "data_type_name": "BOOL", "dim": 0}
    ]
    try:
        second = {a.name: a.data for a in driver.collect(device, None)}
    finally:
        FakeLogixDriver.tag_list = original
    assert first["fingerprint.yml"] != second["fingerprint.yml"]


def test_slot_becomes_connection_path(fake_pycomm3, device, monkeypatch):
    seen = {}
    original_init = FakeLogixDriver.__init__

    def spy(self, path):
        seen["path"] = path
        original_init(self, path)

    monkeypatch.setattr(FakeLogixDriver, "__init__", spy)
    get_driver("rockwell_enip").collect(device, None)
    assert seen["path"] == "10.0.0.20/1"


def test_connection_failure_is_driver_error(fake_pycomm3, device, monkeypatch):
    def boom(self):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(FakeLogixDriver, "__enter__", boom)
    with pytest.raises(DriverError, match="collection failed"):
        get_driver("rockwell_enip").collect(device, None)
