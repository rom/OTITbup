"""Tests the S7 driver against a fake snap7 module — no PLC or native
snap7 library needed."""
import sys
import types

import pytest
import yaml

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device


class FakeCpuInfo:
    ModuleTypeName = b"CPU 315-2 PN/DP"
    SerialNumber = b"S C-X4U421302013"
    ASName = b"S7-300 station"
    ModuleName = b"CPU 315"


class FakeOrderCode:
    OrderCode = b"6ES7 315-2EH14-0AB0"
    V1, V2, V3 = 3, 2, 12


class FakeBlockEnum:
    OB, FB, FC, DB, SDB = "OB", "FB", "FC", "DB", "SDB"


class FakeClient:
    blocks = {"OB": [1], "FB": [], "FC": [2], "DB": [1, 2], "SDB": []}
    refuse_upload = False

    def connect(self, address, rack, slot, port):
        self.address = address

    def disconnect(self):
        pass

    def get_cpu_info(self):
        return FakeCpuInfo()

    def get_order_code(self):
        return FakeOrderCode()

    def get_cpu_state(self):
        return "S7CpuStatusRun"

    def list_blocks_of_type(self, block_type, size):
        if self.refuse_upload:
            raise RuntimeError("function refused (protection level)")
        return list(self.blocks[block_type])

    def full_upload(self, block_type, number):
        if self.refuse_upload:
            raise RuntimeError("function refused")
        return (bytearray(f"MC7:{block_type}{number}".encode()) + b"\x00" * 4,
                len(f"MC7:{block_type}{number}"))


@pytest.fixture
def fake_snap7(monkeypatch):
    mod = types.ModuleType("snap7")
    mod.client = types.SimpleNamespace(Client=FakeClient)
    mod.type = types.SimpleNamespace(Block=FakeBlockEnum)
    monkeypatch.setitem(sys.modules, "snap7", mod)
    FakeClient.refuse_upload = False
    return mod


@pytest.fixture
def device():
    return Device(
        name="plc-01", driver="siemens_s7", site="plant-a", zone="cell-1",
        address="10.0.0.5",
    )


def test_collects_blocks_info_and_fingerprint(fake_snap7, device):
    artifacts = get_driver("siemens_s7").collect(device, None)
    names = {a.name for a in artifacts}
    assert "cpu_info.yml" in names
    assert "fingerprint.yml" in names
    assert {"blocks/OB_1.mc7", "blocks/FC_2.mc7",
            "blocks/DB_1.mc7", "blocks/DB_2.mc7"} <= names

    by_name = {a.name: a for a in artifacts}
    # (buffer, size) tuples are truncated to the real size.
    assert by_name["blocks/OB_1.mc7"].data == b"MC7:OB1"

    info = yaml.safe_load(by_name["cpu_info.yml"].data)
    assert info["ModuleTypeName"] == "CPU 315-2 PN/DP"
    assert info["FirmwareVersion"] == "3.2.12"
    assert info["CpuState"] == "S7CpuStatusRun"

    fp = yaml.safe_load(by_name["fingerprint.yml"].data)
    assert fp["block_count"] == 4
    assert fp["program_sha256"]
    assert fp["upload_complete"] is True


def test_protected_cpu_falls_back_to_metadata(fake_snap7, device):
    FakeClient.refuse_upload = True
    artifacts = get_driver("siemens_s7").collect(device, None)
    names = {a.name for a in artifacts}
    assert names == {"cpu_info.yml", "fingerprint.yml"}

    by_name = {a.name: a for a in artifacts}
    fp = yaml.safe_load(by_name["fingerprint.yml"].data)
    assert fp["block_count"] == 0
    assert fp["upload_complete"] is False
    assert any("refused" in note for note in fp["collection_notes"])


def test_fingerprint_changes_with_program(fake_snap7, device):
    driver = get_driver("siemens_s7")
    first = {a.name: a.data for a in driver.collect(device, None)}
    FakeClient.blocks = {**FakeClient.blocks, "DB": [1, 2, 3]}
    try:
        second = {a.name: a.data for a in driver.collect(device, None)}
    finally:
        FakeClient.blocks = {**FakeClient.blocks, "DB": [1, 2]}
    assert first["fingerprint.yml"] != second["fingerprint.yml"]


def test_address_required(fake_snap7):
    device = Device(
        name="plc", driver="siemens_s7", site="a", zone="z", address=None
    )
    with pytest.raises(DriverError, match="address"):
        get_driver("siemens_s7").collect(device, None)
