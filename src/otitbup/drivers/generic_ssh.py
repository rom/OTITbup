"""SSH command-capture driver for network equipment (REQUIREMENTS.md
section 1). Built on netmiko so one driver covers Cisco IOS/IOS-XE,
Hirschmann, Moxa, Westermo and many other vendors via device_type.

    options:
      device_type: cisco_ios            # netmiko device type
      port: 22
      commands:
        - show running-config
        - show version

Credentials (from the secrets backend): username, password, and optionally
enable_secret.
"""
from __future__ import annotations

import re
from typing import Any

from ..models import Device
from .base import Artifact, Driver, DriverError

_DEFAULT_COMMANDS = ["show running-config"]


def _slug(command: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", command.lower()).strip("_") or "output"


class GenericSSHDriver(Driver):
    name = "generic_ssh"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        try:
            from netmiko import ConnectHandler
        except ImportError as exc:
            raise DriverError(
                "generic_ssh requires netmiko (pip install otitbup[ssh])"
            ) from exc

        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        if not secrets:
            raise DriverError(
                f"{device.qualified_name}: credentials are required"
            )

        options = device.options
        commands = options.get("commands") or _DEFAULT_COMMANDS
        params = {
            "device_type": options.get("device_type", "cisco_ios"),
            "host": device.address,
            "port": int(options.get("port", 22)),
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
                    output = conn.send_command(command)
                    artifacts.append(
                        Artifact(
                            name=f"{_slug(command)}.txt",
                            data=(output or "").encode(),
                            kind="config",
                            meta={"command": command},
                        )
                    )
        except Exception as exc:
            raise DriverError(
                f"{device.qualified_name}: SSH collection failed: {exc}"
            ) from exc
        return artifacts
