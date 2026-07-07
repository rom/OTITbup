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


def register(name: str, cls: type[Driver]) -> None:
    _REGISTRY[name] = cls


def available_drivers() -> list[str]:
    return sorted(_REGISTRY)


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
