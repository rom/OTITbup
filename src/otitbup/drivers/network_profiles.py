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
    # ------------------------------------------------------- SEL (substations)
    "sel_terminal": {
        # SEL RTAC (3530/3505) and SEL protection relays: settings are
        # retrieved with terminal commands. ID = identity/FID, STA =
        # status, SHO = settings. Devices behind an access-level password
        # (ACC) may need a session hook or an account that lands at the
        # right level — verify on your device and override commands.
        "description": "SEL RTAC and protection relays (terminal commands)",
        "device_type": "generic",
        "commands": ["ID", "STA", "SHO"],
        "scrub": [r"Date\s*[=:]", r"Time\s*[=:]", r"[Uu]ptime"],
    },
    # ------------------------------------------------------- Netcontrol
    "netcontrol_rtu": {
        # Netcontrol Netcon 100/500/3000 substation RTUs/gateways expose a
        # Linux-style CLI over SSH. Config lives in files; adjust the
        # commands to your firmware. These devices also speak DNP3,
        # IEC 60870-5-104 and IEC 61850 (use generic_dnp3 / iec61850_mms)
        # and have a web interface (generic_http).
        "description": "Netcontrol Netcon RTUs/gateways (SSH CLI) — also "
                       "reachable via generic_dnp3 / iec61850_mms / "
                       "generic_http",
        "device_type": "linux",
        "commands": [
            "cat /etc/netcon/version 2>/dev/null || cat /etc/version",
            "cat /etc/netcon/*.conf 2>/dev/null",
        ],
        "scrub": [r"[Uu]ptime", r"[Tt]imestamp"],
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
    # ============================ Enterprise / carrier / DC network gear ==
    "juniper_junos": {
        "description": "Juniper Junos switches/routers/SRX firewalls",
        "device_type": "juniper_junos",
        "commands": ["show configuration | display set", "show version"],
        "scrub": [r"^## Last commit", r"[Uu]ptime"],
    },
    "arista_eos": {
        "description": "Arista EOS switches",
        "device_type": "arista_eos",
        "commands": ["show running-config", "show version"],
        "scrub": _IOS_SCRUB + [r"[Uu]ptime"],
    },
    "cisco_nxos": {
        "description": "Cisco Nexus (NX-OS) data-centre switches",
        "device_type": "cisco_nxos",
        "commands": ["show running-config", "show version"],
        "scrub": _IOS_SCRUB + [r"!Time:", r"[Uu]ptime"],
    },
    "cisco_sg": {
        "description": "Cisco Small Business (SG/CBS) switches",
        "device_type": "cisco_s300",
        "commands": ["show running-config"],
        "scrub": _IOS_SCRUB,
    },
    "hpe_comware": {
        "description": "HPE/H3C Comware switches",
        "device_type": "hp_comware",
        "commands": ["display current-configuration", "display version"],
        "scrub": [r"[Uu]ptime"],
    },
    "hpe_procurve": {
        "description": "HPE ProCurve/Aruba-OS switches",
        "device_type": "hp_procurve",
        "commands": ["show running-config", "show version"],
        "scrub": _IOS_SCRUB + [r"[Uu]ptime"],
    },
    "aruba_cx": {
        "description": "Aruba CX switches (AOS-CX)",
        "device_type": "aruba_aoscx",
        "commands": ["show running-config", "show version"],
        "scrub": [r"[Uu]ptime"],
    },
    "huawei_vrp": {
        "description": "Huawei VRP switches/routers",
        "device_type": "huawei",
        "commands": ["display current-configuration", "display version"],
        "scrub": [r"[Uu]ptime"],
    },
    "mikrotik_routeros": {
        "description": "MikroTik RouterOS routers/switches",
        "device_type": "mikrotik_routeros",
        "commands": ["/export"],
        "scrub": [r"^# .* by RouterOS", r"[Uu]ptime"],
    },
    "extreme_exos": {
        "description": "Extreme Networks EXOS switches",
        "device_type": "extreme_exos",
        "commands": ["show configuration", "show version"],
        "scrub": [r"[Uu]ptime"],
    },
    "dell_os10": {
        "description": "Dell EMC OS10 switches",
        "device_type": "dell_os10",
        "commands": ["show running-configuration", "show version"],
        "scrub": [r"[Uu]ptime"],
    },
    "dell_powerconnect": {
        "description": "Dell PowerConnect/N-series switches",
        "device_type": "dell_powerconnect",
        "commands": ["show running-config"],
        "scrub": _IOS_SCRUB,
    },
    "vyos": {
        "description": "VyOS routers/firewalls",
        "device_type": "vyos",
        "commands": ["show configuration commands"],
        "scrub": [r"[Uu]ptime"],
    },
    # ---------------------------------------------------------- Firewalls
    "fortinet_fortigate": {
        "description": "Fortinet FortiGate firewalls",
        "device_type": "fortinet",
        "commands": ["show full-configuration"],
        "scrub": [r"conf_file_ver", r"[Uu]ptime", r"^#conf_file"],
    },
    "paloalto_panos": {
        "description": "Palo Alto Networks PAN-OS firewalls",
        "device_type": "paloalto_panos",
        "commands": ["show config running", "show system info"],
        "scrub": [r"[Uu]ptime"],
    },
    "checkpoint_gaia": {
        "description": "Check Point Gaia firewalls",
        "device_type": "checkpoint_gaia",
        "commands": ["show configuration"],
        "scrub": [r"[Uu]ptime"],
    },
    "juniper_srx": {
        "description": "Juniper SRX firewalls (Junos)",
        "device_type": "juniper_junos",
        "commands": ["show configuration | display set"],
        "scrub": [r"^## Last commit", r"[Uu]ptime"],
    },
    "sophos_xg": {
        "description": "Sophos XG/XGS firewalls (CLI)",
        "device_type": "generic",
        "commands": ["system diagnostics show config"],
        "scrub": [r"[Uu]ptime"],
    },
    # ---------------------------------------------- More industrial switches
    "phoenix_fl_switch": {
        "description": "Phoenix Contact FL SWITCH managed switches",
        "device_type": "generic",
        "commands": ["show running-config", "show version"],
        "scrub": _IOS_SCRUB + _UPTIME_SCRUB,
    },
    "redlion_nt": {
        "description": "Red Lion N-Tron/NT managed industrial switches",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": _UPTIME_SCRUB,
    },
    "antaira": {
        "description": "Antaira industrial switches",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": _UPTIME_SCRUB,
    },
    "korenix": {
        "description": "Korenix JetNet industrial switches",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": _UPTIME_SCRUB,
    },
    "planet_switch": {
        "description": "Planet industrial/enterprise switches",
        "device_type": "generic",
        "commands": ["show running-config"],
        "scrub": _IOS_SCRUB,
    },
    "zyxel": {
        "description": "Zyxel managed switches",
        "device_type": "zyxel_os",
        "commands": ["show running-config"],
        "scrub": _IOS_SCRUB,
    },
    "teltonika": {
        "description": "Teltonika cellular routers/gateways (RutOS CLI)",
        "device_type": "generic",
        "commands": ["uci show", "cat /etc/config/network"],
        "scrub": [r"[Uu]ptime"],
    },
    # ---------------------------------------------------- More RTUs (CLI)
    "ge_d20": {
        "description": "GE/Emerson D20/D25 RTUs (CLI; also DNP3/IEC-104)",
        "device_type": "generic",
        "commands": ["show config", "show version"],
        "scrub": _UPTIME_SCRUB + [r"[Dd]ate", r"[Tt]ime"],
    },
    "novatech_orion": {
        "description": "NovaTech Orion/OrionLX RTUs (Linux CLI; also DNP3/"
                       "IEC-61850)",
        "device_type": "linux",
        "commands": ["cat /etc/orion/version 2>/dev/null || uname -a"],
        "scrub": _UPTIME_SCRUB,
    },
    "smp_gateway": {
        "description": "Eaton/Cooper SMP gateway RTUs (also DNP3/IEC-61850)",
        "device_type": "generic",
        "commands": ["show configuration"],
        "scrub": _UPTIME_SCRUB,
    },
    "survalent_rtu": {
        "description": "Survalent SmartVU/RTU (CLI; also DNP3)",
        "device_type": "generic",
        "commands": ["show config"],
        "scrub": _UPTIME_SCRUB,
    },
    # -------------------------------- Serial-to-ethernet gateways (CLI)
    "lantronix": {
        "description": "Lantronix serial device servers (CLI over SSH)",
        "device_type": "generic",
        "commands": ["show", "show config"],
        "scrub": _UPTIME_SCRUB,
    },
    "digi_connect": {
        "description": "Digi Connect/One serial servers (CLI over SSH)",
        "device_type": "generic",
        "commands": ["show config", "display device"],
        "scrub": _UPTIME_SCRUB,
    },
    "perle_iolan": {
        "description": "Perle IOLAN serial device servers (CLI)",
        "device_type": "generic",
        "commands": ["show configuration"],
        "scrub": _UPTIME_SCRUB,
    },
    "sena_serial": {
        "description": "Sena/Digi serial device servers (CLI)",
        "device_type": "generic",
        "commands": ["show config"],
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
