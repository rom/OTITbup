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

Registered driver names — switches: cisco_ios, siemens_scalance,
ruggedcom_ros, hirschmann_hios, hirschmann_classic, belden_switch,
moxa_switch, westermo_weos, advantech_switch, netgear_switch,
omron_switch. Routers/firewalls: cisco_asa, ruggedcom_rox, moxa_edr,
westermo_merlin, advantech_router, hirschmann_eagle (SCALANCE M-series
routers and S/SC firewalls share the siemens_scalance profile).
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
_UPTIME_SCRUB = [r"[Uu]p ?[Tt]ime"]

# Hirschmann HiOS/Classic CLIs are shared by Hirschmann- and Belden-branded
# industrial gear (Belden owns Hirschmann).
_HIRSCHMANN_COMMANDS = ["show running-config", "show system info"]
_HIRSCHMANN_SCRUB = _IOS_SCRUB + _UPTIME_SCRUB

PROFILES: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------- Cisco
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
    "cisco_asa": {
        # ASA firewalls; `show running-config` needs enable — configure
        # enable_secret in the device's credentials.
        "description": "Cisco ASA firewalls",
        "device_type": "cisco_asa",
        "commands": [
            "show running-config",
            "show version",
        ],
        "scrub": _IOS_SCRUB + [
            r"^: Written by ",
            r"^: Saved",
            r"^Cryptochecksum",
        ],
    },
    # ----------------------------------------------------------- Siemens
    "siemens_scalance": {
        # SCALANCE X/XB/XC/XR switches, M-series (4G/5G) routers and
        # S/SC-series industrial firewalls share the IOS-like SCALANCE CLI.
        "description": "Siemens SCALANCE switches, M-series routers, "
                       "S/SC firewalls",
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
        # RUGGEDCOM ROX II (RX1400/RX1500/RX5000): layer-3 switches,
        # routers and firewalls with a confd-style CLI.
        "description": "Siemens RUGGEDCOM ROX II routers/firewalls "
                       "(RX1400, RX1500, RX5000, ...)",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": [],
    },
    # ----------------------------------------- Hirschmann / Belden family
    "hirschmann_hios": {
        "description": "Hirschmann/Belden HiOS switches "
                       "(BOBCAT, GREYHOUND, RSP, MSP, ...)",
        "device_type": "generic",
        "commands": _HIRSCHMANN_COMMANDS,
        "scrub": _HIRSCHMANN_SCRUB,
    },
    "hirschmann_classic": {
        "description": "Hirschmann Classic-OS switches "
                       "(RS20/30/40, MICE MS20/30, MACH, ...)",
        "device_type": "generic",
        "commands": _HIRSCHMANN_COMMANDS,
        "scrub": _HIRSCHMANN_SCRUB,
    },
    "hirschmann_eagle": {
        "description": "Hirschmann/Belden EAGLE industrial firewalls "
                       "(EAGLE20/30/40, EAGLE One)",
        "device_type": "generic",
        "commands": _HIRSCHMANN_COMMANDS,
        "scrub": _HIRSCHMANN_SCRUB,
    },
    "belden_switch": {
        # Belden-branded switches are Hirschmann family. For legacy
        # GarrettCom Magnum devices override commands (e.g. "show config").
        "description": "Belden-branded industrial switches "
                       "(Hirschmann family)",
        "device_type": "generic",
        "commands": _HIRSCHMANN_COMMANDS,
        "scrub": _HIRSCHMANN_SCRUB,
    },
    # -------------------------------------------------------------- Moxa
    "moxa_switch": {
        # Moxa EDS managed switches: IOS-like CLI over SSH.
        "description": "Moxa EDS managed switches",
        "device_type": "generic",
        "commands": [
            "show running-config",
            "show version",
        ],
        "scrub": _IOS_SCRUB,
    },
    "moxa_edr": {
        # Moxa EDR secure routers / industrial firewalls (EDR-810,
        # EDR-G902/G903, EDR-G9010): same CLI family as EDS.
        "description": "Moxa EDR secure routers/firewalls",
        "device_type": "generic",
        "commands": [
            "show running-config",
            "show version",
        ],
        "scrub": _IOS_SCRUB,
    },
    # ---------------------------------------------------------- Westermo
    "westermo_weos": {
        # WeOS devices: Lynx/Viper/RedFox switches — RedFox also does
        # routing/firewalling on the same CLI.
        "description": "Westermo WeOS switches/routers "
                       "(Lynx, Viper, RedFox, ...)",
        "device_type": "generic",
        "commands": [
            "show running-config",
            "show version",
        ],
        "scrub": _UPTIME_SCRUB,
    },
    "westermo_merlin": {
        # Westermo Merlin / GW-series cellular (4G/5G) routers.
        "description": "Westermo Merlin 4G/5G cellular routers",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": _UPTIME_SCRUB,
    },
    # --------------------------------------------------------- Advantech
    "advantech_switch": {
        # Advantech EKI managed switches: IOS-like CLI over SSH.
        "description": "Advantech EKI managed switches",
        "device_type": "generic",
        "commands": [
            "show running-config",
            "show version",
        ],
        "scrub": _IOS_SCRUB,
    },
    "advantech_router": {
        # Advantech ICR cellular routers (ex-Conel) expose a Linux shell
        # over SSH; configuration lives in /etc/settings files. Command
        # sets vary by firmware generation — verify on your model and
        # override if needed.
        "description": "Advantech ICR cellular routers (ex-Conel) — "
                       "verify the export command for your firmware",
        "device_type": "linux",
        "commands": [
            "cat /etc/version",
            "cat /etc/settings.*",
        ],
        "scrub": [],
    },
    # ----------------------------------------------------------- Netgear
    "netgear_switch": {
        # Netgear M4300/M4250/ProSAFE managed switches. Netgear
        # routers/firewalls are web-managed — use generic_http for those.
        "description": "Netgear M4300/M4250/ProSAFE managed switches",
        "device_type": "netgear_prosafe",
        "commands": ["show running-config"],
        "scrub": _IOS_SCRUB + [
            r"^!Current Configuration:",
            r"^!System Up Time",
        ],
    },
    # ------------------------------------------------------------- Omron
    "omron_switch": {
        # Omron industrial ethernet switches; many models are web-managed
        # only — use generic_http for those. Omron has no router/firewall
        # product line.
        "description": "Omron industrial ethernet switches",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": _UPTIME_SCRUB,
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
