"""Versioned backup store: a local git repository on the appliance, with
optional push to a remote (REQUIREMENTS.md section 6).

Repository layout:

    sites/<site>/<zone>/<device>/
        <artifacts...>
        manifest.yml    # sha256 fingerprints, kinds — change detection even
                        # for binary artifacts

Each device backup is committed individually so history reads as one commit
per device per change.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
from pathlib import Path

import yaml

from .drivers.base import Artifact
from .models import Device


class GitStoreError(Exception):
    pass


class GitStore:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self._lock = threading.Lock()

    def _git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise GitStoreError(
                f"git {' '.join(args)} failed: {proc.stderr.strip()}"
            )
        return proc.stdout

    def ensure_repo(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self._git("init", "--initial-branch=main")
            self._git("config", "user.name", "otitbup")
            self._git("config", "user.email", "otitbup@localhost")

    def write_and_commit(self, device: Device, artifacts: list[Artifact]) -> str | None:
        """Replace the device's directory contents with `artifacts` and
        commit. Returns the commit hash, or None if nothing changed."""
        with self._lock:
            device_dir = self.root / device.path
            if device_dir.exists():
                shutil.rmtree(device_dir)
            device_dir.mkdir(parents=True)

            manifest: dict[str, dict[str, str]] = {}
            for artifact in artifacts:
                target = device_dir / artifact.name
                if not target.resolve().is_relative_to(device_dir.resolve()):
                    raise GitStoreError(
                        f"artifact escapes device dir: {artifact.name}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(artifact.data)
                manifest[artifact.name] = {
                    "sha256": hashlib.sha256(artifact.data).hexdigest(),
                    "kind": artifact.kind,
                }
            with open(device_dir / "manifest.yml", "w") as fh:
                yaml.safe_dump(manifest, fh, sort_keys=True)

            self._git("add", "-A", "--", device.path)
            if not self._git("status", "--porcelain", "--", device.path).strip():
                return None
            self._git(
                "commit",
                "-m",
                f"backup({device.qualified_name}): {len(artifacts)} artifact(s)",
                "--",
                device.path,
            )
            return self._git("rev-parse", "HEAD").strip()

    def last_diff(self, device: Device) -> str:
        """Diff of the most recent commit touching this device."""
        last = self._git(
            "log", "-1", "--format=%H", "--", device.path
        ).strip()
        if not last:
            return ""
        return self._git("show", "--stat", "--patch", last, "--", device.path)

    def history(self, device: Device | None = None, limit: int = 20) -> str:
        args = ["log", f"-{limit}", "--format=%h  %ad  %s", "--date=iso"]
        if device:
            args += ["--", device.path]
        return self._git(*args, check=False)

    def push(self, remote: str, branch: str = "main") -> None:
        if not [r for r in self._git("remote", check=False).split() if r == "origin"]:
            self._git("remote", "add", "origin", remote)
        else:
            self._git("remote", "set-url", "origin", remote)
        self._git("push", "-u", "origin", branch)
