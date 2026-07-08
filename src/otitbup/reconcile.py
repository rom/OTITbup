"""Inventory reconciliation.

Coverage you can't prove isn't coverage. This compares the configured
inventory against what is actually reachable on the network (via the same
opt-in, sequential, TCP-connect scan as discovery) and reports the two
gaps that matter:

- **unmanaged**: an address answered on an OT/IT port but is NOT in the
  inventory — something on the network nobody is backing up.
- **unreachable**: an inventoried device did not answer on any known port
  — decommissioned, moved, or down.

Read-only and safe: it never edits the inventory (nor could it — the YAML
is the source of truth). Findings are printed and, for unmanaged hosts,
rendered as a discovery-style YAML proposal for human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .discovery import DEFAULT_PORTS, Finding, scan
from .models import AppConfig


@dataclass
class Reconciliation:
    unmanaged: list[Finding] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    managed_reachable: list[str] = field(default_factory=list)


def reconcile(
    config: AppConfig,
    subnets: list[str],
    ports: dict[int, str] | None = None,
    timeout: float = 0.5,
    delay: float = 0.05,
) -> Reconciliation:
    inventory = {d.address: d for d in config.all_devices() if d.address}
    findings = scan(
        subnets, ports=ports or DEFAULT_PORTS,
        timeout=timeout, delay=delay,
    )
    found_addresses = {f.address for f in findings}

    result = Reconciliation()
    for finding in findings:
        if finding.address in inventory:
            result.managed_reachable.append(
                inventory[finding.address].qualified_name
            )
        else:
            result.unmanaged.append(finding)
    for address, device in sorted(inventory.items()):
        if address not in found_addresses:
            result.unreachable.append(device.qualified_name)
    return result
