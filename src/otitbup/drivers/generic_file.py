"""Watch-folder ingest driver (REQUIREMENTS.md section 2, item 2).

Versions engineer-exported project files (TIA Portal, Studio 5000, Unity)
dropped into a directory. Options:

    options:
      path: /srv/exports/plc-01      # required: folder to ingest
      patterns: ["**/*.zap16"]        # optional glob patterns, default all
      max_file_size: 104857600        # optional, bytes, default 100 MiB
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import Device
from .base import Artifact, Driver, DriverError

_DEFAULT_MAX_SIZE = 100 * 1024 * 1024


class GenericFileDriver(Driver):
    name = "generic_file"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        options = device.options
        source = options.get("path")
        if not source:
            raise DriverError(
                f"{device.qualified_name}: generic_file requires options.path"
            )
        root = Path(source).expanduser()
        if not root.is_dir():
            raise DriverError(
                f"{device.qualified_name}: source folder not found: {root}"
            )
        patterns = options.get("patterns") or ["**/*"]
        max_size = int(options.get("max_file_size", _DEFAULT_MAX_SIZE))

        files: set[Path] = set()
        for pattern in patterns:
            files.update(p for p in root.glob(pattern) if p.is_file())

        artifacts = []
        skipped = []
        for file in sorted(files):
            if file.stat().st_size > max_size:
                skipped.append(str(file.relative_to(root)))
                continue
            artifacts.append(
                Artifact(
                    name=str(file.relative_to(root)),
                    data=file.read_bytes(),
                    kind="project",
                )
            )
        if skipped:
            artifacts.append(
                Artifact(
                    name="_skipped_oversize.txt",
                    data=("\n".join(skipped) + "\n").encode(),
                    kind="metadata",
                )
            )
        if not artifacts:
            raise DriverError(
                f"{device.qualified_name}: no files matched in {root}"
            )
        return artifacts
