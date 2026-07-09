"""SFTP file-fetch driver for Linux-based controllers, read-only.

Modern Codesys-family PLCs (WAGO PFC100/200, Phoenix Contact PLCnext,
Festo, Bosch Rexroth ctrlX, ...) are Linux devices whose boot project and
settings live as files on disk — the Codesys V3 wire protocol is
proprietary, but SSH/SFTP is not. This driver fetches configured remote
paths recursively, so the actual running application is versioned, not
just a fingerprint.

Registered vendor presets:

- wago_pfc:        /home/codesys (Codesys 2) and /home/codesys3
                   (e!COCKPIT / Codesys 3) — runtime dirs vary by
                   firmware; missing paths are noted, not fatal
- phoenix_plcnext: /opt/plcnext/projects
- codesys_ssh:     no preset paths — set options.paths
- generic_sftp:    the same, under a vendor-neutral name

    options:
      paths:                       # remote files, directories (recursive)
        - /home/codesys            # or glob patterns (*, ?)
        - /etc/plcnext/device.settings
      port: 22
      max_file_size: 104857600     # bytes, default 100 MiB
      timeout: 15

Credentials: username/password (from the secrets backend). Host keys are
auto-accepted on first contact — the appliance is expected to sit inside
the OT network; pin keys at the SSH layer if your policy requires it.
"""
from __future__ import annotations

import fnmatch
import posixpath
import stat as stat_module
from typing import Any

from ..models import Device
from .base import Artifact, Driver, DriverError

_DEFAULT_MAX_SIZE = 100 * 1024 * 1024
_MAX_FILES = 5000


def _artifact_name(path: str) -> str:
    name = posixpath.normpath(path).lstrip("/")
    if not name or ".." in name.split("/"):
        raise DriverError(f"unsafe remote path: {path}")
    return name


def _walk(sftp: Any, root: str) -> list[tuple[str, int]]:
    """Recursively list (path, size) of regular files under root."""
    entry = sftp.stat(root)
    if not stat_module.S_ISDIR(entry.st_mode):
        return [(root, entry.st_size)]
    files: list[tuple[str, int]] = []
    pending = [root]
    while pending and len(files) < _MAX_FILES:
        directory = pending.pop()
        for item in sorted(
            sftp.listdir_attr(directory), key=lambda a: a.filename
        ):
            path = posixpath.join(directory, item.filename)
            if stat_module.S_ISDIR(item.st_mode):
                pending.append(path)
            elif stat_module.S_ISREG(item.st_mode):
                files.append((path, item.st_size))
    return files


def _expand(sftp: Any, pattern: str, notes: list[str]) -> list[tuple[str, int]]:
    """Resolve one configured path: plain file/dir, or glob pattern."""
    if not any(ch in pattern for ch in "*?["):
        try:
            return _walk(sftp, pattern)
        except FileNotFoundError:
            notes.append(f"path not found: {pattern}")
            return []
    # Glob: walk the static prefix, fnmatch full paths against the pattern.
    parts = pattern.split("/")
    static = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        static.append(part)
    base = "/".join(static) or "/"
    try:
        candidates = _walk(sftp, base)
    except FileNotFoundError:
        notes.append(f"path not found: {base} (for pattern {pattern})")
        return []
    matched = [
        (path, size) for path, size in candidates
        if fnmatch.fnmatch(path, pattern)
        or fnmatch.fnmatch(path, pattern + "/*")
    ]
    if not matched:
        notes.append(f"no files matched pattern: {pattern}")
    return matched


class GenericSFTPDriver(Driver):
    name = "generic_sftp"
    default_paths: list[str] = []

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        try:
            import paramiko
        except ImportError as exc:
            raise DriverError(
                f"{self.name} requires paramiko "
                "(pip install 'otitbup[sftp]')"
            ) from exc

        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        if not secrets or not secrets.get("username"):
            raise DriverError(
                f"{device.qualified_name}: credentials are required"
            )
        options = device.options
        paths = options.get("paths") or self.default_paths
        if not paths:
            raise DriverError(
                f"{device.qualified_name}: options.paths is required "
                "(remote files/directories to fetch)"
            )
        max_size = int(options.get("max_file_size", _DEFAULT_MAX_SIZE))

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        notes: list[str] = []
        try:
            client.connect(
                device.address,
                port=int(options.get("port", 22)),
                username=str(secrets["username"]),
                password=str(secrets.get("password", "")),
                timeout=float(options.get("timeout", 15)),
                look_for_keys=False,
                allow_agent=False,
            )
            sftp = client.open_sftp()
            artifacts: list[Artifact] = []
            for pattern in paths:
                for path, size in _expand(sftp, pattern, notes):
                    if size > max_size:
                        notes.append(f"skipped oversize file: {path}")
                        continue
                    with sftp.open(path, "rb") as handle:
                        data = handle.read()
                    artifacts.append(
                        Artifact(
                            name=_artifact_name(path),
                            data=data,
                            kind="project",
                        )
                    )
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(
                f"{device.qualified_name}: SFTP collection failed: {exc}"
            ) from exc
        finally:
            try:
                client.close()
            except Exception:
                pass

        if not artifacts:
            raise DriverError(
                f"{device.qualified_name}: no files fetched "
                f"({'; '.join(notes) or 'nothing under configured paths'})"
            )
        if notes:
            artifacts.append(
                Artifact(
                    name="_collection_notes.txt",
                    data=("\n".join(notes) + "\n").encode(),
                    kind="metadata",
                )
            )
        return artifacts


class CodesysSSHDriver(GenericSFTPDriver):
    """Vendor-neutral name for Linux-based Codesys controllers; set
    options.paths to the runtime's project directory."""

    name = "codesys_ssh"


class WagoPFCDriver(GenericSFTPDriver):
    """WAGO PFC100/PFC200: fetches the Codesys 2 (/home/codesys) and
    e!COCKPIT / Codesys 3 (/home/codesys3) runtime directories by
    default; whichever is absent is noted, not fatal."""

    name = "wago_pfc"
    default_paths = ["/home/codesys", "/home/codesys3"]


class PhoenixPLCnextDriver(GenericSFTPDriver):
    """Phoenix Contact PLCnext (AXC F 1152/2152/3152, RFC 4072S):
    fetches /opt/plcnext/projects — the deployed PLCnext Engineer
    project — by default."""

    name = "phoenix_plcnext"
    default_paths = ["/opt/plcnext/projects"]
