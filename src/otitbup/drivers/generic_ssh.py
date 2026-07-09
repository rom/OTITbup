"""SSH command-capture driver for network equipment (docs/REQUIREMENTS.md
section 1). Built on netmiko so one driver covers many vendors via
device_type. Vendor-specific presets (Cisco IOS, SCALANCE, RUGGEDCOM,
Moxa, Westermo, ...) live in network_profiles.py and reuse collect_ssh().

    options:
      device_type: cisco_ios            # netmiko device type
      port: 22
      commands:
        - show running-config
        - show version
      scrub:                            # optional: drop volatile lines so
        - "uptime is"                   # they don't pollute diffs (regex)

Credentials (from the secrets backend): username, password, and optionally
enable_secret.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..models import Device
from .base import Artifact, Driver, DriverError

_DEFAULT_COMMANDS = ["show running-config"]


def _slug(command: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", command.lower()).strip("_") or "output"


def _scrub(text: str, patterns: Iterable[str]) -> str:
    compiled = [re.compile(p) for p in patterns]
    if not compiled:
        return text
    kept = [
        line for line in text.splitlines()
        if not any(c.search(line) for c in compiled)
    ]
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def collect_ssh(
    device: Device,
    secrets: dict[str, Any] | None,
    *,
    device_type: str,
    commands: list[str],
    port: int = 22,
    scrub: Iterable[str] = (),
) -> list[Artifact]:
    """Shared SSH collection core used by generic_ssh and all vendor
    profile drivers."""
    try:
        from netmiko import ConnectHandler
    except ImportError as exc:
        raise DriverError(
            f"{device.driver} requires netmiko (pip install 'otitbup[ssh]')"
        ) from exc

    if not device.address:
        raise DriverError(f"{device.qualified_name}: address is required")
    if not secrets:
        raise DriverError(f"{device.qualified_name}: credentials are required")

    params = {
        "device_type": device_type,
        "host": device.address,
        "port": port,
        "username": secrets.get("username"),
        "password": secrets.get("password"),
    }
    if secrets.get("enable_secret"):
        params["secret"] = secrets["enable_secret"]

    artifacts = []
    try:
        with ConnectHandler(**params) as conn:
            if secrets.get("enable_secret"):
                conn.enable()
            for command in commands:
                output = _scrub(conn.send_command(command) or "", scrub)
                artifacts.append(
                    Artifact(
                        name=f"{_slug(command)}.txt",
                        data=output.encode(),
                        kind="config",
                        meta={"command": command},
                    )
                )
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(
            f"{device.qualified_name}: SSH collection failed: {exc}"
        ) from exc
    return artifacts


class GenericSSHDriver(Driver):
    name = "generic_ssh"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        options = device.options
        return collect_ssh(
            device, secrets,
            device_type=options.get("device_type", "cisco_ios"),
            commands=options.get("commands") or _DEFAULT_COMMANDS,
            port=int(options.get("port", 22)),
            scrub=options.get("scrub") or (),
        )
