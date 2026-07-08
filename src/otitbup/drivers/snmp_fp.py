"""SNMP fingerprint driver, stdlib-only.

The lowest-common-denominator identity capture: an SNMPv2c GET of the
standard system group (sysDescr, sysObjectID, sysName, sysContact,
sysLocation). Works on almost anything with an SNMP agent — switches,
UPSes, gateways, printers, RTUs — when nothing more specific fits, and it
enriches discovery. sysUpTime is read for the record but excluded from the
fingerprint so a reboot doesn't churn diffs.

    options:
      community: public        # SNMPv2c community
      port: 161
      extra_oids:              # optional, name -> OID
        serial: "1.3.6.1.2.1.47.1.1.1.1.11.1"
"""
from __future__ import annotations

import hashlib
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError


class SNMPFingerprintDriver(Driver):
    name = "snmp_fingerprint"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        from ..snmp import SYS_UPTIME, SYSTEM_GROUP, SNMPError, snmp_get

        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        community = (
            (secrets or {}).get("community")
            or options.get("community", "public")
        )
        oids = dict(SYSTEM_GROUP)
        for name, oid in (options.get("extra_oids") or {}).items():
            oids[str(oid)] = str(name)

        try:
            values = snmp_get(
                device.address, list(oids),
                community=community,
                port=int(options.get("port", 161)),
                timeout=float(options.get("timeout", 3)),
            )
        except SNMPError as exc:
            raise DriverError(f"{device.qualified_name}: {exc}") from exc
        if not values:
            raise DriverError(
                f"{device.qualified_name}: SNMP agent returned no values"
            )

        info = {
            oids.get(oid, oid): value for oid, value in sorted(values.items())
        }
        info_yaml = yaml.safe_dump(info, sort_keys=True).encode()
        # Fingerprint excludes the (volatile) uptime.
        stable = {
            k: v for k, v in info.items() if k != SYSTEM_GROUP.get(SYS_UPTIME)
        }
        fingerprint = yaml.safe_dump(
            {"snmp_sha256":
                hashlib.sha256(
                    yaml.safe_dump(stable, sort_keys=True).encode()
                ).hexdigest()},
            sort_keys=True,
        ).encode()
        return [
            Artifact(name="snmp_system.yml", data=info_yaml, kind="metadata"),
            Artifact(name="fingerprint.yml", data=fingerprint, kind="metadata"),
        ]
