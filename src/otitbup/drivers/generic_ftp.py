"""FTP / FTPS file-fetch driver, read-only, stdlib-only (ftplib).

The plain-FTP sibling of generic_sftp: for older or embedded devices that
expose their configuration and project files over FTP (many RTUs, drives,
serial gateways, HMI panels and legacy controllers still do) rather than
SSH. It fetches configured remote paths — files, directories (recursively)
or glob patterns — so the actual files are versioned, not just a
fingerprint.

    - name: panel-01
      driver: generic_ftp
      address: 10.20.0.50
      credentials: panel-01          # username/password (anonymous if unset)
      options:
        paths:
          - /project                 # a directory (fetched recursively)
          - /config/*.cfg            # or a glob pattern
        port: 21
        passive: true                # default true
        tls: false                   # true -> FTPS (explicit AUTH TLS)
        max_file_size: 104857600     # bytes, default 100 MiB
        timeout: 15

Read-only: the driver only lists (MLSD/NLST) and downloads (RETR); it never
writes to the device. Directory recursion prefers MLSD (RFC 3659) for
reliable type detection and falls back to a one-level NLST listing (noted)
when the server does not support MLSD.
"""
from __future__ import annotations

import fnmatch
import ftplib
import io
import posixpath
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


def _walk(ftp: ftplib.FTP, root: str, notes: list[str]) -> list[str]:
    """Recursively list regular-file paths under `root`. Uses MLSD where
    available, else a non-recursive NLST of `root` (noted)."""
    files: list[str] = []
    pending = [root]
    seen: set[str] = set()
    while pending and len(files) < _MAX_FILES:
        directory = pending.pop()
        if directory in seen:
            continue
        seen.add(directory)
        try:
            entries = list(ftp.mlsd(directory))
        except (ftplib.error_perm, ftplib.error_proto, OSError):
            # No MLSD: fall back to a flat NLST of this directory.
            try:
                for name in ftp.nlst(directory):
                    path = name if name.startswith("/") else posixpath.join(
                        directory, posixpath.basename(name))
                    files.append(path)
            except ftplib.error_perm as exc:
                notes.append(f"cannot list {directory}: {exc}")
            continue
        for name, facts in entries:
            if name in (".", ".."):
                continue
            path = posixpath.join(directory, name)
            etype = facts.get("type", "file")
            if etype == "dir":
                pending.append(path)
            elif etype in ("file", "OS.unix=slink"):
                files.append(path)
    return files


def _expand(ftp: ftplib.FTP, pattern: str, notes: list[str]) -> list[str]:
    """Resolve one configured path: a file, a directory (recursive), or a
    glob pattern."""
    if not any(ch in pattern for ch in "*?["):
        # Is it a directory? Try to walk it; if that yields nothing and it's
        # a file, fall back to treating it as a single file.
        try:
            size = ftp.size(pattern)
        except (ftplib.error_perm, OSError):
            size = None
        if size is not None:               # it's a downloadable file
            return [pattern]
        found = _walk(ftp, pattern, notes)
        if not found:
            notes.append(f"path not found or empty: {pattern}")
        return found
    parts = pattern.split("/")
    static = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        static.append(part)
    base = "/".join(static) or "/"
    candidates = _walk(ftp, base, notes)
    matched = [p for p in candidates
               if fnmatch.fnmatch(p, pattern)
               or fnmatch.fnmatch(p, pattern + "/*")]
    if not matched:
        notes.append(f"no files matched pattern: {pattern}")
    return matched


class GenericFTPDriver(Driver):
    name = "generic_ftp"
    default_paths: list[str] = []

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        paths = options.get("paths") or self.default_paths
        if not paths:
            raise DriverError(
                f"{device.qualified_name}: options.paths is required "
                "(remote files/directories to fetch)"
            )
        max_size = int(options.get("max_file_size", _DEFAULT_MAX_SIZE))
        timeout = float(options.get("timeout", 15))
        username = str((secrets or {}).get("username", "anonymous"))
        password = str((secrets or {}).get("password", "") or "otitbup@")

        cls = ftplib.FTP_TLS if options.get("tls") else ftplib.FTP
        ftp = cls()
        notes: list[str] = []
        try:
            ftp.connect(device.address, int(options.get("port", 21)),
                        timeout=timeout)
            ftp.login(username, password)
            if isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()                       # encrypt the data channel
            ftp.set_pasv(bool(options.get("passive", True)))
            artifacts: list[Artifact] = []
            for pattern in paths:
                for path in _expand(ftp, pattern, notes):
                    size = None
                    try:
                        size = ftp.size(path)
                    except (ftplib.error_perm, OSError):
                        pass
                    if size is not None and size > max_size:
                        notes.append(f"skipped oversize file: {path}")
                        continue
                    buffer = io.BytesIO()
                    try:
                        ftp.retrbinary(f"RETR {path}", buffer.write)
                    except ftplib.error_perm as exc:
                        notes.append(f"cannot fetch {path}: {exc}")
                        continue
                    artifacts.append(Artifact(
                        name=_artifact_name(path),
                        data=buffer.getvalue(),
                        kind="project",
                    ))
        except DriverError:
            raise
        except ftplib.all_errors as exc:      # includes OSError, EOFError
            raise DriverError(
                f"{device.qualified_name}: FTP collection failed: {exc}"
            ) from exc
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

        if not artifacts:
            raise DriverError(
                f"{device.qualified_name}: no files fetched "
                f"({'; '.join(notes) or 'nothing under configured paths'})"
            )
        if notes:
            artifacts.append(Artifact(
                name="_collection_notes.txt",
                data=("\n".join(notes) + "\n").encode(),
                kind="metadata",
            ))
        return artifacts
