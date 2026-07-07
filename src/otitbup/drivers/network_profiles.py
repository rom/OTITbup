"""Vendor profiles for SSH-managed network equipment.

Each profile is a preset over the generic SSH core (generic_ssh.collect_ssh):
netmiko device_type, the commands to capture, and `scrub` regexes that drop
volatile output lines (uptime, "last change" stamps) so backup diffs only
show real configuration changes.

Profiles are DEFAULTS — every field can be overridden per device via
options (device_type, commands, port, scrub), because industrial firmware
lines vary. Example:

    - name: sw-cell1
      driver: siemens_scalance
      address: 10.10.0.2
      credentials: sw-cell1
      options:
        commands: ["show running-config all"]   # override just this

Registered driver names: cisco_ios, siemens_scalance, ruggedcom_ros,
ruggedcom_rox, moxa_switch, westermo_weos, westermo_merlin.
"""
from __future__ import annotations

from typing import Any

from ..models import Device
from .base import Artifact, Driver
from .generic_ssh import collect_ssh

# Volatile-line patterns shared by IOS-like CLIs.
_IOS_SCRUB = [
    r"^! Last configuration change",
    r"^! NVRAM config last updated",
    r"^ntp clock-period ",
    r"uptime is ",
    r"^Building configuration",
    r"^Current configuration",
]

PROFILES: dict[str, dict[str, Any]] = {
    "cisco_ios": {
        "description": "Cisco IOS / IOS-XE switches and routers",
        "device_type": "cisco_ios",
        "commands": [
            "show running-config",
            "show version",
            "show inventory",
        ],
        "scrub": _IOS_SCRUB,
    },
    "siemens_scalance": {
        # SCALANCE X/XB/XC/XR industrial switches: IOS-like CLI over SSH.
        "description": "Siemens SCALANCE industrial switches",
        "device_type": "cisco_ios",
        "commands": [
            "show running-config",
            "show versions",
        ],
        "scrub": _IOS_SCRUB + [r"^System Up Time"],
    },
    "ruggedcom_ros": {
        # RUGGEDCOM ROS keeps its whole configuration in config.csv;
        # `type config.csv` prints it from the CLI shell.
        "description": "Siemens RUGGEDCOM switches running ROS",
        "device_type": "generic",
        "commands": [
            "type config.csv",
            "version",
        ],
        "scrub": [],
    },
    "ruggedcom_rox": {
        # RUGGEDCOM ROX II (RX1500 etc.): confd-style CLI.
        "description": "Siemens RUGGEDCOM layer-3 devices running ROX II",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": [],
    },
    "moxa_switch": {
        # Moxa EDS/EDR managed switches: IOS-like CLI over SSH.
        "description": "Moxa EDS/EDR managed switches",
        "device_type": "generic",
        "commands": [
            "show running-config",
            "show version",
        ],
        "scrub": _IOS_SCRUB,
    },
    "westermo_weos": {
        # Westermo Lynx/Viper/RedFox switches running WeOS.
        "description": "Westermo WeOS switches (Lynx, Viper, RedFox, ...)",
        "device_type": "generic",
        "commands": [
            "show running-config",
            "show version",
        ],
        "scrub": [r"[Uu]ptime"],
    },
    "westermo_merlin": {
        # Westermo Merlin / GW-series cellular (4G/5G) routers.
        "description": "Westermo Merlin 4G/5G cellular routers",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": [r"[Uu]ptime"],
    },
}


class ProfiledSSHDriver(Driver):
    """Base for vendor-preset SSH drivers; `name` doubles as profile key."""

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        profile = PROFILES[self.name]
        options = device.options
        return collect_ssh(
            device, secrets,
            device_type=options.get("device_type", profile["device_type"]),
            commands=options.get("commands") or profile["commands"],
            port=int(options.get("port", 22)),
            scrub=(
                options["scrub"] if "scrub" in options else profile["scrub"]
            ),
        )


def _class_name(profile: str) -> str:
    return "".join(part.capitalize() for part in profile.split("_")) + "Driver"


# Generate one driver class per profile (CiscoIosDriver, ...) so the lazy
# registry can import them by "module:ClassName" like any other driver.
for _profile in PROFILES:
    globals()[_class_name(_profile)] = type(
        _class_name(_profile), (ProfiledSSHDriver,), {"name": _profile}
    )
