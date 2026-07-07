"""Tests vendor SSH profiles against a fake netmiko module."""
import sys
import types

import pytest

from otitbup.drivers import available_drivers, get_driver
from otitbup.drivers.network_profiles import PROFILES
from otitbup.models import Device

_CISCO_RUNNING_CONFIG = """\
Building configuration...

Current configuration : 4096 bytes
!
! Last configuration change at 11:22:33 UTC Mon Jul 6 2026 by admin
! NVRAM config last updated at 11:22:40 UTC Mon Jul 6 2026
!
hostname core-sw-01
ntp clock-period 17208078
interface GigabitEthernet0/1
 description uplink
end
: Written by admin at 12:00:00.000 UTC Mon Jul 6 2026
: Saved
Cryptochecksum: 8a1f00cafe44beef
"""


class FakeConnection:
    last_params = None
    sent_commands = []

    def __init__(self, **params):
        type(self).last_params = params
        type(self).sent_commands = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def enable(self):
        pass

    def send_command(self, command):
        type(self).sent_commands.append(command)
        if command == "show running-config":
            return _CISCO_RUNNING_CONFIG
        if "version" in command:
            return "SW version 7.1.2, uptime is 4 weeks, 2 days\nSerial: X1\n"
        return f"output of {command}\n"


@pytest.fixture
def fake_netmiko(monkeypatch):
    mod = types.ModuleType("netmiko")
    mod.ConnectHandler = FakeConnection
    monkeypatch.setitem(sys.modules, "netmiko", mod)
    return mod


def _device(driver, options=None):
    return Device(
        name="sw", driver=driver, site="plant-a", zone="network",
        address="10.10.0.2", options=options or {},
    )


_SECRETS = {"username": "backup", "password": "pw"}


def test_all_profiles_are_registered():
    assert set(PROFILES) <= set(available_drivers())


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_profile_uses_its_device_type_and_commands(fake_netmiko, profile):
    artifacts = get_driver(profile).collect(_device(profile), _SECRETS)
    expected = PROFILES[profile]
    assert FakeConnection.last_params["device_type"] == expected["device_type"]
    assert FakeConnection.sent_commands == expected["commands"]
    assert len(artifacts) == len(expected["commands"])
    assert all(a.kind == "config" for a in artifacts)


def test_volatile_lines_are_scrubbed(fake_netmiko):
    artifacts = get_driver("cisco_ios").collect(_device("cisco_ios"), _SECRETS)
    config = {a.name: a.data.decode() for a in artifacts}["show_running_config.txt"]
    assert "hostname core-sw-01" in config
    assert "interface GigabitEthernet0/1" in config
    # Volatile lines must not reach the repo — they'd churn every diff.
    assert "Last configuration change" not in config
    assert "ntp clock-period" not in config
    assert "Building configuration" not in config

    version = {a.name: a.data.decode() for a in artifacts}["show_version.txt"]
    assert "uptime is" not in version
    assert "Serial: X1" in version


def test_cisco_asa_scrubs_firewall_stamps(fake_netmiko):
    artifacts = get_driver("cisco_asa").collect(_device("cisco_asa"), _SECRETS)
    config = {a.name: a.data.decode() for a in artifacts}["show_running_config.txt"]
    assert "hostname core-sw-01" in config
    assert "Cryptochecksum" not in config
    assert ": Written by" not in config
    assert ": Saved" not in config


def test_hirschmann_family_shares_defaults(fake_netmiko):
    for profile in ("hirschmann_hios", "hirschmann_classic",
                    "hirschmann_eagle", "belden_switch"):
        get_driver(profile).collect(_device(profile), _SECRETS)
        assert FakeConnection.sent_commands == [
            "show running-config", "show system info",
        ]


def test_netgear_uses_prosafe_device_type(fake_netmiko):
    get_driver("netgear_switch").collect(_device("netgear_switch"), _SECRETS)
    assert FakeConnection.last_params["device_type"] == "netgear_prosafe"


def test_ruggedcom_ros_reads_config_csv(fake_netmiko):
    get_driver("ruggedcom_ros").collect(_device("ruggedcom_ros"), _SECRETS)
    assert FakeConnection.sent_commands[0] == "type config.csv"


def test_options_override_profile_defaults(fake_netmiko):
    options = {
        "commands": ["show running-config all"],
        "device_type": "cisco_xe",
        "scrub": [],
    }
    artifacts = get_driver("siemens_scalance").collect(
        _device("siemens_scalance", options), _SECRETS
    )
    assert FakeConnection.last_params["device_type"] == "cisco_xe"
    assert FakeConnection.sent_commands == ["show running-config all"]
    assert len(artifacts) == 1


def test_generic_ssh_still_unscrubbed_by_default(fake_netmiko):
    artifacts = get_driver("generic_ssh").collect(
        _device("generic_ssh"), _SECRETS
    )
    config = artifacts[0].data.decode()
    assert "ntp clock-period" in config  # untouched without explicit scrub
