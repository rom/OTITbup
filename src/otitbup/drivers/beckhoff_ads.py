"""Beckhoff TwinCAT driver via ADS (pyads), read-only.

Captures the controller identity and runtime state:

- device_info.yml (metadata): ADS device name, ADS/TwinCAT version, ADS
  state (Run/Stop/Config) and device state
- fingerprint.yml (metadata): sha256 over the identity

TwinCAT projects are versioned with generic_file exports. An ADS route to
this machine must be configured on the target (TwinCAT router security) —
without a route the connection is refused.

    options:
      ams_net_id: "5.12.34.56.1.1"   # default: "<address>.1.1"
      ams_port: 851                   # 851 = TC3 PLC runtime 1;
                                      # 10000 = system service
      timeout: 10

Credentials are not used by ADS itself (routes carry the trust).
"""
from __future__ import annotations

import hashlib
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError

_ADS_STATES = {
    0: "Invalid", 1: "Idle", 2: "Reset", 3: "Init", 4: "Start",
    5: "Run", 6: "Stop", 7: "SaveConfig", 8: "LoadConfig",
    9: "PowerFailure", 10: "PowerGood", 11: "Error", 12: "Shutdown",
    13: "Suspend", 14: "Resume", 15: "Config", 16: "Reconfig",
}


class BeckhoffADSDriver(Driver):
    name = "beckhoff_ads"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        try:
            import pyads
        except ImportError as exc:
            raise DriverError(
                "beckhoff_ads requires pyads (pip install otitbup[beckhoff])"
            ) from exc

        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        ams_net_id = options.get("ams_net_id") or f"{device.address}.1.1"
        ams_port = int(options.get("ams_port", 851))

        connection = pyads.Connection(ams_net_id, ams_port, device.address)
        info: dict[str, Any] = {
            "ams_net_id": ams_net_id,
            "ams_port": ams_port,
        }
        notes: list[str] = []
        try:
            connection.open()
            try:
                name, version = connection.read_device_info()
                info["device_name"] = str(name)
                info["ads_version"] = (
                    f"{version.version}.{version.revision}.{version.build}"
                )
            except Exception as exc:
                notes.append(f"read_device_info failed: {exc}")
            try:
                ads_state, device_state = connection.read_state()
                info["ads_state"] = _ADS_STATES.get(
                    int(ads_state), f"unknown ({ads_state})"
                )
                info["device_state"] = int(device_state)
            except Exception as exc:
                notes.append(f"read_state failed: {exc}")
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(
                f"{device.qualified_name}: ADS connection failed: {exc} "
                "(is an ADS route to this host configured on the target?)"
            ) from exc
        finally:
            try:
                connection.close()
            except Exception:
                pass

        if "device_name" not in info and "ads_state" not in info:
            raise DriverError(
                f"{device.qualified_name}: ADS returned nothing "
                f"({'; '.join(notes)})"
            )
        if notes:
            info["collection_notes"] = notes

        info_yaml = yaml.safe_dump(info, sort_keys=True).encode()
        fingerprint = yaml.safe_dump(
            {"device_sha256": hashlib.sha256(info_yaml).hexdigest()},
            sort_keys=True,
        ).encode()
        return [
            Artifact(name="device_info.yml", data=info_yaml, kind="metadata"),
            Artifact(name="fingerprint.yml", data=fingerprint, kind="metadata"),
        ]
