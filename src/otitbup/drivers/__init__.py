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
    # Vendor SSH profiles (presets over generic_ssh; see network_profiles.py)
    "cisco_ios": "otitbup.drivers.network_profiles:CiscoIosDriver",
    "siemens_scalance": "otitbup.drivers.network_profiles:SiemensScalanceDriver",
    "ruggedcom_ros": "otitbup.drivers.network_profiles:RuggedcomRosDriver",
    "ruggedcom_rox": "otitbup.drivers.network_profiles:RuggedcomRoxDriver",
    "moxa_switch": "otitbup.drivers.network_profiles:MoxaSwitchDriver",
    "westermo_weos": "otitbup.drivers.network_profiles:WestermoWeosDriver",
    "westermo_merlin": "otitbup.drivers.network_profiles:WestermoMerlinDriver",
}


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
