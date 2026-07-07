"""Generic OPC UA identity/fingerprint driver (asyncua), read-only.

One driver covers every controller that exposes an OPC UA server —
S7-1200/1500, Omron NJ/NX, Beckhoff, WAGO, B&R, and many more. It reads
the server's self-description, which changes when firmware or the loaded
application changes:

- server_info.yml (metadata): BuildInfo (manufacturer, product,
  software version, build number/date) and the namespace array
- values.yml (config, optional): extra nodes listed in options.nodes
- fingerprint.yml (metadata): sha256 over everything above

    options:
      port: 4840
      endpoint: "opc.tcp://{address}:4840"   # override the default URL
      nodes:                                  # optional extra reads
        plc_serial: "ns=3;s=SerialNumber"

Credentials (optional): username/password for servers that require it.
Anonymous access is attempted otherwise. Encrypted endpoints with
certificates are not yet supported — use an unencrypted (or SignOnly)
endpoint for the backup user.
"""
from __future__ import annotations

import hashlib
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError

# BuildInfo children under Server_ServerStatus_BuildInfo (i=2260).
_BUILD_INFO_NODES = {
    "ProductUri": "i=2262",
    "ManufacturerName": "i=2263",
    "ProductName": "i=2261",
    "SoftwareVersion": "i=2264",
    "BuildNumber": "i=2265",
    "BuildDate": "i=2266",
}


class GenericOPCUADriver(Driver):
    name = "generic_opcua"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        try:
            from asyncua.sync import Client
        except ImportError as exc:
            raise DriverError(
                "generic_opcua requires asyncua "
                "(pip install otitbup[opcua])"
            ) from exc

        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        endpoint = options.get("endpoint") or (
            f"opc.tcp://{device.address}:{int(options.get('port', 4840))}"
        )
        endpoint = endpoint.format(address=device.address)

        notes: list[str] = []
        client = Client(endpoint, timeout=float(options.get("timeout", 10)))
        if secrets and secrets.get("username"):
            client.set_user(str(secrets["username"]))
            client.set_password(str(secrets.get("password", "")))
        try:
            client.connect()
        except Exception as exc:
            raise DriverError(
                f"{device.qualified_name}: OPC UA connect to {endpoint} "
                f"failed: {exc}"
            ) from exc

        try:
            info: dict[str, Any] = {"endpoint": endpoint}
            for label, node_id in _BUILD_INFO_NODES.items():
                try:
                    info[label] = str(client.get_node(node_id).read_value())
                except Exception as exc:
                    notes.append(f"read {label} failed: {exc}")
            try:
                info["NamespaceArray"] = [
                    str(ns) for ns in client.get_namespace_array()
                ]
            except Exception as exc:
                notes.append(f"read NamespaceArray failed: {exc}")
            if notes:
                info["collection_notes"] = notes

            values: dict[str, str] = {}
            for label, node_id in (options.get("nodes") or {}).items():
                try:
                    values[str(label)] = str(
                        client.get_node(str(node_id)).read_value()
                    )
                except Exception as exc:
                    values[str(label)] = f"<read failed: {exc}>"
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

        info_yaml = yaml.safe_dump(info, sort_keys=True).encode()
        artifacts = [
            Artifact(name="server_info.yml", data=info_yaml, kind="metadata"),
        ]
        digest = hashlib.sha256(info_yaml)
        if values:
            values_yaml = yaml.safe_dump(values, sort_keys=True).encode()
            artifacts.append(
                Artifact(name="values.yml", data=values_yaml, kind="config")
            )
            digest.update(values_yaml)
        artifacts.append(
            Artifact(
                name="fingerprint.yml",
                data=yaml.safe_dump(
                    {"server_sha256": digest.hexdigest()}, sort_keys=True
                ).encode(),
                kind="metadata",
            )
        )
        return artifacts
