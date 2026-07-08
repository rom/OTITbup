"""Driver contract.

Drivers are strictly read-only in phase 1 (docs/REQUIREMENTS.md sections 3 and
10): collect() gathers artifacts from a device and must never change device
state. No write paths ship until restore is designed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models import Device


class DriverError(Exception):
    pass


@dataclass
class Artifact:
    """One piece of collected data, stored at `name` (a relative path)
    inside the device's directory in the backup repository."""

    name: str
    data: bytes
    kind: str = "config"  # config | logic | metadata | project
    meta: dict[str, Any] = field(default_factory=dict)


class Driver(ABC):
    name: str = "base"

    @abstractmethod
    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        """Collect artifacts from the device. Read-only by contract."""
