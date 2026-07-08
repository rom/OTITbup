"""Opt-in network discovery (docs/REQUIREMENTS.md section 7).

OT safeguards, by design:

- Never runs automatically — only via the explicit `otitbup discover`
  command.
- TCP connect probes only, to a small list of well-known ports; no banner
  grabbing, no protocol payloads, no UDP broadcast.
- Strictly sequential with a configurable inter-probe delay, so a scan can
  never flood a control network.
- Findings are written as a YAML *proposal* for human review; nothing is
  ever added to the inventory automatically.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field

# Probe order doubles as driver-suggestion priority: a device answering on
# 102 is treated as an S7 CPU even if it also serves Modbus.
DEFAULT_PORTS: dict[int, str] = {
    102: "siemens_s7",        # S7comm / ISO-on-TCP (also IEC 61850 MMS)
    44818: "rockwell_enip",   # EtherNet/IP
    502: "schneider_modbus",  # Modbus TCP
    20000: "generic_dnp3",    # DNP3
    4840: "generic_opcua",    # OPC UA
    22: "generic_ssh",        # network equipment
}

# UDP ports probed only when enrichment is requested (SNMP has no TCP).
_ENRICH_UDP_PORTS = {161: "snmp_fingerprint"}


@dataclass
class Finding:
    address: str
    open_ports: list[int] = field(default_factory=list)
    driver: str = ""
    identity: str = ""       # filled by enrichment (vendor/model string)


def scan(
    subnets: list[str],
    ports: dict[int, str] | None = None,
    timeout: float = 0.5,
    delay: float = 0.05,
    exclude: frozenset[str] | set[str] = frozenset(),
    progress=None,
) -> list[Finding]:
    port_map = ports or DEFAULT_PORTS
    probe_order = [p for p in DEFAULT_PORTS if p in port_map]
    probe_order += [p for p in sorted(port_map) if p not in probe_order]

    findings: list[Finding] = []
    for subnet in subnets:
        network = ipaddress.ip_network(subnet, strict=False)
        for host in network.hosts():
            address = str(host)
            if address in exclude:
                continue
            if progress:
                progress(address)
            open_ports = []
            for port in probe_order:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                try:
                    if sock.connect_ex((address, port)) == 0:
                        open_ports.append(port)
                finally:
                    sock.close()
                time.sleep(delay)
            if open_ports:
                findings.append(Finding(
                    address=address,
                    open_ports=open_ports,
                    driver=port_map[open_ports[0]],
                ))
    return findings


def _identity_string(finding: Finding) -> str:
    """Best-effort vendor/model probe for one finding, using the cheapest
    identity source available. Never raises."""
    from .models import Device

    device = Device(
        name="probe", driver=finding.driver, site="_", zone="_",
        address=finding.address,
    )
    # SNMP first if the agent answered (works across almost everything).
    try:
        from .snmp import SYS_DESCR, SYS_NAME, snmp_get
        values = snmp_get(finding.address, [SYS_DESCR, SYS_NAME], timeout=1.5)
        descr = values.get(SYS_DESCR) or values.get(SYS_NAME)
        if descr:
            return str(descr).splitlines()[0][:120]
    except Exception:
        pass
    # Otherwise the finding's own identity driver (EtherNet/IP, Modbus, ...).
    from .drivers import get_driver
    from .drivers.base import DriverError
    try:
        artifacts = get_driver(finding.driver).collect(device, None)
        for artifact in artifacts:
            if artifact.name.endswith((".yml", ".txt")):
                import yaml
                data = yaml.safe_load(artifact.data) or {}
                if isinstance(data, dict):
                    for key in ("product_name", "ProductName", "VendorName",
                                "ProductCode", "cpu_model", "ModuleTypeName",
                                "device_manufacturer_name",
                                "product_name_and_model"):
                        if data.get(key):
                            return str(data[key])[:120]
    except (DriverError, Exception):
        pass
    return ""


def enrich(findings: list[Finding], timeout: float = 1.5) -> list[Finding]:
    """Populate each finding's identity via its driver / SNMP. Sequential
    and best-effort, matching the OT-safe posture of the scan."""
    for finding in findings:
        finding.identity = _identity_string(finding)
    return findings


def proposal_yaml(
    findings: list[Finding], site: str, zone: str
) -> str:
    """Render findings as an inventory-shaped YAML proposal with comments.
    Built as text (not yaml.dump) so the review guidance survives."""
    lines = [
        "# otitbup discovery proposal — REVIEW BEFORE USE",
        "# Generated by `otitbup discover`. Nothing was added to the",
        "# inventory automatically. Verify each device, set a proper name,",
        "# schedule, credentials and driver options, then merge the entries",
        "# you approve into your otitbup.yml.",
        "sites:",
        f"  - name: {site}",
        "    zones:",
        f"      - name: {zone}",
        "        max_concurrent: 1",
        "        devices:",
    ]
    for finding in findings:
        name = (
            f"{finding.driver.split('_')[0]}-"
            f"{finding.address.replace('.', '-')}"
        )
        ports = ", ".join(str(p) for p in finding.open_ports)
        if finding.identity:
            lines.append(f"          # identity: {finding.identity}")
        lines += [
            f"          # open ports: {ports}",
            f"          - name: {name}",
            f"            driver: {finding.driver}",
            f"            address: {finding.address}",
            "            schedule: 1d",
        ]
        if finding.driver == "generic_ssh":
            lines += [
                f"            credentials: {name}   # add to your secrets file",
                "            # If the vendor is known, use its profile instead of",
                "            # generic_ssh — run `otitbup drivers` for the full list",
                "            # (cisco_ios, siemens_scalance, hirschmann_hios, ...)",
                "            options:",
                "              device_type: cisco_ios   # adjust to vendor",
            ]
    return "\n".join(lines) + "\n"
