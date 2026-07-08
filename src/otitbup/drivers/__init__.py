"""Driver registry with lazy imports so optional vendor dependencies
(netmiko, python-snap7, pycomm3) are only required when the corresponding
driver is actually used."""
from __future__ import annotations

import importlib

from .base import Artifact, Driver, DriverError

_REGISTRY: dict[str, str | type[Driver]] = {
    "generic_file": "otitbup.drivers.generic_file:GenericFileDriver",
    "generic_ssh": "otitbup.drivers.generic_ssh:GenericSSHDriver",
    "siemens_s7": "otitbup.drivers.siemens_s7:SiemensS7Driver",
    "rockwell_enip": "otitbup.drivers.rockwell_enip:RockwellENIPDriver",
    "schneider_modbus": "otitbup.drivers.schneider_modbus:SchneiderModbusDriver",
    "generic_http": "otitbup.drivers.generic_http:GenericHTTPDriver",
    "moxa_nport": "otitbup.drivers.generic_http:MoxaNPortDriver",
    "generic_opcua": "otitbup.drivers.generic_opcua:GenericOPCUADriver",
    "generic_dnp3": "otitbup.drivers.generic_dnp3:GenericDNP3Driver",
    "mitsubishi_mc": "otitbup.drivers.mitsubishi_mc:MitsubishiMCDriver",
    "omron_fins": "otitbup.drivers.omron_fins:OmronFINSDriver",
    "beckhoff_ads": "otitbup.drivers.beckhoff_ads:BeckhoffADSDriver",
    "generic_sftp": "otitbup.drivers.generic_sftp:GenericSFTPDriver",
    "codesys_ssh": "otitbup.drivers.generic_sftp:CodesysSSHDriver",
    "wago_pfc": "otitbup.drivers.generic_sftp:WagoPFCDriver",
    "phoenix_plcnext": "otitbup.drivers.generic_sftp:PhoenixPLCnextDriver",
    "generic_enip": "otitbup.drivers.generic_enip:GenericENIPDriver",
    "ge_pacsystems": "otitbup.drivers.generic_enip:GEPACSystemsDriver",
    "emerson_pacsystems": "otitbup.drivers.generic_enip:GEPACSystemsDriver",
    "siemens_sicam": "otitbup.drivers.generic_http:SiemensSicamDriver",
    "abb_rtu500": "otitbup.drivers.generic_http:AbbRtu500Driver",
    "abb_rtu520": "otitbup.drivers.generic_http:AbbRtu500Driver",
    "abb_rtu560": "otitbup.drivers.generic_http:AbbRtu500Driver",
}

# Vendor SSH profiles (presets over generic_ssh) register themselves from
# PROFILES so the registry can never drift from the profile table. The
# import is dependency-light: netmiko is only loaded on collect().
from .network_profiles import PROFILES as _NETWORK_PROFILES  # noqa: E402
from .network_profiles import _class_name as _profile_class_name  # noqa: E402

for _profile in _NETWORK_PROFILES:
    _REGISTRY[_profile] = (
        f"otitbup.drivers.network_profiles:{_profile_class_name(_profile)}"
    )


_CORE_DESCRIPTIONS = {
    "generic_file": "watch-folder ingest of engineer-exported project files",
    "generic_ssh": "any SSH-CLI device via netmiko device_type",
    "generic_sftp": "fetch files/dirs from Linux devices over SFTP",
    "generic_http": "config export over HTTP(S) from web-managed devices",
    "generic_opcua": "any OPC UA server: build info + namespace fingerprint",
    "generic_enip": "any EtherNet/IP device: CIP identity + fingerprint",
    "generic_dnp3": "any DNP3 outstation/RTU: device attributes (g0)",
    "siemens_s7": "Siemens S7 PLCs (block upload, CPU info, fingerprint)",
    "rockwell_enip": "Rockwell/Allen-Bradley Logix controllers (pycomm3)",
    "schneider_modbus": "Schneider/Modicon PLCs (device identification)",
    "mitsubishi_mc": "Mitsubishi MELSEC Q/L/iQ CPUs (MC protocol identity)",
    "omron_fins": "Omron CJ/CS/CP/NJ/NX PLCs (FINS identity)",
    "beckhoff_ads": "Beckhoff TwinCAT controllers (ADS device info)",
    "codesys_ssh": "Linux-based Codesys PLCs: boot project via SFTP",
    "wago_pfc": "WAGO PFC100/200: Codesys runtime dirs via SFTP",
    "phoenix_plcnext": "Phoenix PLCnext: /opt/plcnext/projects via SFTP",
    "ge_pacsystems": "GE/Emerson PACSystems (EtherNet/IP identity)",
    "emerson_pacsystems": "GE/Emerson PACSystems (EtherNet/IP identity)",
    "moxa_nport": "Moxa NPort serial-to-ethernet converters (HTTP export)",
    "siemens_sicam": "Siemens SICAM A8000 RTUs (web endpoints)",
    "abb_rtu500": "ABB RTU500 series RTUs (web endpoints)",
    "abb_rtu520": "ABB RTU520 (RTU500 series, web endpoints)",
    "abb_rtu560": "ABB RTU560 (RTU500 series, web endpoints)",
}


def register(name: str, cls: type[Driver]) -> None:
    _REGISTRY[name] = cls


def available_drivers() -> list[str]:
    return sorted(_REGISTRY)


def driver_descriptions() -> dict[str, str]:
    """Driver name -> one-line description, for the CLI and web UI."""
    from .network_profiles import PROFILES
    described = dict(_CORE_DESCRIPTIONS)
    for name, profile in PROFILES.items():
        described.setdefault(name, profile["description"])
    return {name: described.get(name, "") for name in available_drivers()}


def get_driver(name: str) -> Driver:
    target = _REGISTRY.get(name, name if ":" in name else None)
    if target is None:
        raise DriverError(
            f"unknown driver {name!r} (available: {', '.join(available_drivers())})"
        )
    if isinstance(target, str):
        module_name, _, class_name = target.partition(":")
        try:
            module = importlib.import_module(module_name)
            target = getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            raise DriverError(f"cannot load driver {name!r}: {exc}") from exc
    return target()


__all__ = [
    "Artifact",
    "Driver",
    "DriverError",
    "available_drivers",
    "get_driver",
    "register",
]
