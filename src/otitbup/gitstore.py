"""Versioned backup store: a local git repository on the appliance, with
optional push to a remote (docs/REQUIREMENTS.md section 6).

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


def manifest_artifacts(manifest) -> dict:
    """The per-artifact section of a manifest. Manifests are now
    `{provenance: {...}, artifacts: {name: {...}}}`; older ones were a flat
    `{name: {...}}`. This returns the artifact map for either shape."""
    if not isinstance(manifest, dict):
        return {}
    if "artifacts" in manifest and isinstance(manifest["artifacts"], dict):
        return manifest["artifacts"]
    # Legacy flat manifest: drop any non-dict values defensively.
    return {k: v for k, v in manifest.items() if isinstance(v, dict)}


class GitStore:
    def __init__(self, data_dir: str | Path, sign_key: str | None = None):
        self.root = Path(data_dir)
        self._lock = threading.Lock()
        # SSH signing key path for tamper-evident, attributable commits.
        self.sign_key = sign_key

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

    def write_and_commit(
        self,
        device: Device,
        artifacts: list[Artifact],
        blobstore=None,
        threshold: int | None = None,
        appliance: str | None = None,
        captured_at: str | None = None,
    ) -> str | None:
        """Replace the device's directory contents with `artifacts` and
        commit. Returns the commit hash, or None if nothing changed.

        With a blobstore and a non-zero threshold, artifacts of at least
        `threshold` bytes are stored content-addressed in the blob store
        and a pointer file is committed instead (see blobstore.py) — the
        manifest always records the real content hash either way."""
        from .blobstore import make_pointer

        with self._lock:
            device_dir = self.root / device.path
            if device_dir.exists():
                shutil.rmtree(device_dir)
            device_dir.mkdir(parents=True)

            manifest: dict[str, dict] = {}
            for artifact in artifacts:
                target = device_dir / artifact.name
                if not target.resolve().is_relative_to(device_dir.resolve()):
                    raise GitStoreError(
                        f"artifact escapes device dir: {artifact.name}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                sha256 = hashlib.sha256(artifact.data).hexdigest()
                entry: dict = {
                    "sha256": sha256,
                    "kind": artifact.kind,
                    "size": len(artifact.data),
                }
                if (
                    blobstore is not None
                    and threshold
                    and len(artifact.data) >= threshold
                ):
                    blobstore.put(artifact.data)
                    target.write_bytes(
                        make_pointer(sha256, len(artifact.data))
                    )
                    entry["offloaded"] = True
                else:
                    target.write_bytes(artifact.data)
                manifest[artifact.name] = entry
            # Stable provenance lives IN the manifest (device GUID + driver)
            # so it is content-hashed and diffable, yet — being stable — never
            # causes a spurious commit. Volatile provenance (timestamp, tool
            # and appliance) goes in the commit trailers instead.
            document = {
                "provenance": {
                    "device_guid": device.effective_guid,
                    "driver": device.driver,
                },
                "artifacts": manifest,
            }
            with open(device_dir / "manifest.yml", "w") as fh:
                yaml.safe_dump(document, fh, sort_keys=True)

            self._git("add", "-A", "--", device.path)
            if not self._git("status", "--porcelain", "--", device.path).strip():
                return None
            message = self._commit_message(
                device, len(artifacts), appliance, captured_at)
            commit_args = ["commit", "-m", message]
            if self.sign_key:
                # SSH-signed commit: attributable, tamper-evident history.
                commit_args = [
                    "-c", "gpg.format=ssh",
                    "-c", f"user.signingkey={self.sign_key}",
                ] + commit_args[:1] + ["-S"] + commit_args[1:]
            commit_args += ["--", device.path]
            self._git(*commit_args)
            return self._git("rev-parse", "HEAD").strip()

    def _commit_message(
        self, device: Device, count: int, appliance: str | None,
        captured_at: str | None,
    ) -> str:
        """Backup commit subject plus provenance trailers (chain of custody)."""
        import socket

        from . import __version__
        appliance = appliance or socket.gethostname()
        trailers = [
            f"Device-GUID: {device.effective_guid}",
            f"Driver: {device.driver}",
            f"Tool-Version: {__version__}",
            f"Appliance: {appliance}",
        ]
        if captured_at:
            trailers.append(f"Captured-At: {captured_at}")
        return (f"backup({device.qualified_name}): {count} artifact(s)\n\n"
                + "\n".join(trailers) + "\n")

    def verify_commit_signature(self, commit: str = "HEAD") -> bool:
        """True if `commit` carries a valid signature."""
        proc = subprocess.run(
            ["git", "verify-commit", commit],
            cwd=self.root, capture_output=True, text=True)
        return proc.returncode == 0

    def last_diff(self, device: Device) -> str:
        """Diff of the most recent commit touching this device. Empty when
        the repository has no commits yet (a fresh appliance before its
        first backup)."""
        last = self._git(
            "log", "-1", "--format=%H", "--", device.path, check=False,
        ).strip()
        if not last:
            return ""
        return self._git("show", "--stat", "--patch", last, "--", device.path,
                         check=False)

    def _git_bytes(self, *args: str) -> bytes:
        proc = subprocess.run(["git", *args], cwd=self.root, capture_output=True)
        if proc.returncode != 0:
            raise GitStoreError(
                f"git {' '.join(args)} failed: "
                f"{proc.stderr.decode(errors='replace').strip()}"
            )
        return proc.stdout

    def last_commit_hash(self, device: Device) -> str | None:
        commit = self._git(
            "log", "-1", "--format=%H", "--", device.path, check=False
        ).strip()
        return commit or None

    def device_commits(self, device: Device) -> list[tuple[str, int]]:
        """(commit hash, unix timestamp) per backup, newest first."""
        out = self._git(
            "log", "--format=%H %ct", "--", device.path, check=False
        )
        commits = []
        for line in out.splitlines():
            commit, _, timestamp = line.partition(" ")
            if commit and timestamp.isdigit():
                commits.append((commit, int(timestamp)))
        return commits

    def commit_summary(self, commit: str) -> str:
        return self._git(
            "show", "-s", "--format=%h  %ad%n%s", "--date=iso", commit
        ).strip()

    def list_files_at(self, commit: str, path: str) -> list[str]:
        out = self._git(
            "ls-tree", "-r", "--name-only", commit, "--", path
        )
        return [line for line in out.splitlines() if line]

    def read_file_at(self, commit: str, path: str) -> bytes:
        return self._git_bytes("show", f"{commit}:{path}")

    def last_commit_info(self, device: Device) -> str:
        """Short hash + date of the device's most recent backup, or ''."""
        return self._git(
            "log", "-1", "--format=%h %ad", "--date=format:%Y-%m-%d %H:%M",
            "--", device.path, check=False,
        ).strip()

    def commit_diff(self, device: Device, commit: str) -> str:
        """Diff of one commit, restricted to the device's path."""
        return self._git(
            "show", "--stat", "--patch", commit, "--", device.path,
            check=False,
        )

    def diff_between(self, device: Device, base: str, head: str) -> str:
        """Diff a device's tree between any two commits (base..head)."""
        return self._git(
            "diff", "--stat", "--patch", base, head, "--", device.path,
            check=False,
        )

    def search(
        self, pattern: str, commit: str = "HEAD", ignore_case: bool = True,
    ) -> list[tuple[str, int, str]]:
        """git grep over the given commit's tree — i.e. across the latest
        backup of every device. Returns (repo_path, line_no, line)."""
        args = ["grep", "-n", "-I"]
        if ignore_case:
            args.append("-i")
        args += ["-e", pattern, commit]
        out = self._git(*args, check=False)
        results = []
        for line in out.splitlines():
            # Format: <commit>:<path>:<lineno>:<text>
            parts = line.split(":", 3)
            if len(parts) == 4:
                _c, path, lineno, text = parts
                if lineno.isdigit():
                    results.append((path, int(lineno), text))
        return results

    def history(self, device: Device | None = None, limit: int = 20) -> str:
        args = ["log", f"-{limit}", "--format=%h  %ad  %s", "--date=iso"]
        if device:
            args += ["--", device.path]
        return self._git(*args, check=False)

    _NOTES_REF = "refs/notes/otitbup"

    def set_annotation(self, commit: str, text: str) -> None:
        """Attach/replace a change annotation (a git note) on a commit —
        e.g. a work-order or MOC number explaining why a config changed."""
        self._git(
            "notes", f"--ref={self._NOTES_REF}", "add", "-f",
            "-m", text, commit,
        )

    def get_annotation(self, commit: str) -> str:
        return self._git(
            "notes", f"--ref={self._NOTES_REF}", "show", commit, check=False
        ).strip()

    def annotated_commits(self) -> set[str]:
        """Full hashes of every commit that carries an annotation."""
        out = self._git(
            "notes", f"--ref={self._NOTES_REF}", "list", check=False
        )
        # Each line is "<note-object> <annotated-commit>".
        return {
            line.split()[1] for line in out.splitlines() if len(line.split()) == 2
        }

    def verify_commit(
        self, device: Device, commit: str, blobstore=None
    ) -> list[str]:
        """Re-hash every artifact at `commit` against the manifest recorded
        at backup time. Returns a list of problems (empty = intact)."""
        problems: list[str] = []
        manifest_path = f"{device.path}/manifest.yml"
        try:
            manifest = yaml.safe_load(self.read_file_at(commit, manifest_path))
        except GitStoreError:
            return [f"{device.qualified_name}@{commit[:8]}: manifest missing"]
        if not isinstance(manifest, dict):
            return [f"{device.qualified_name}@{commit[:8]}: manifest unreadable"]
        from .blobstore import parse_pointer
        for name, meta in manifest_artifacts(manifest).items():
            expected = meta.get("sha256") if isinstance(meta, dict) else None
            try:
                data = self.read_file_at(commit, f"{device.path}/{name}")
            except GitStoreError:
                problems.append(f"{name}: missing from commit")
                continue
            pointer = parse_pointer(data)
            if pointer:
                sha, _size = pointer
                if blobstore is None or not blobstore.has(sha):
                    problems.append(f"{name}: blob {sha[:8]} absent (expired?)")
                    continue
                data = blobstore.get(sha)
            if expected and hashlib.sha256(data).hexdigest() != expected:
                problems.append(f"{name}: sha256 mismatch")
        return problems

    def fsck(self) -> list[str]:
        """Run `git fsck` to detect repository-level corruption (bad or
        missing objects, broken links). Returns a list of problem lines
        (empty = healthy). Dangling objects are normal and ignored."""
        proc = subprocess.run(
            ["git", "fsck", "--full", "--no-progress", "--no-dangling"],
            cwd=self.root, capture_output=True, text=True,
        )
        problems = []
        for line in (proc.stdout + proc.stderr).splitlines():
            line = line.strip()
            if not line or line.startswith(("Checking", "dangling", "notice:")):
                continue
            problems.append(line)
        if proc.returncode != 0 and not problems:
            problems.append(f"git fsck exited {proc.returncode}")
        return problems

    def gc(self, aggressive: bool = False) -> str:
        """Repack and prune the repository to keep it small. Safe to run
        while the repo is idle; returns git's output."""
        args = ["gc", "--prune=now"]
        if aggressive:
            args.append("--aggressive")
        return self._git(*args, check=False)

    def repo_size_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return total

    def push(self, remote: str, branch: str = "main") -> None:
        if not [r for r in self._git("remote", check=False).split() if r == "origin"]:
            self._git("remote", "add", "origin", remote)
        else:
            self._git("remote", "set-url", "origin", remote)
        self._git("push", "-u", "origin", branch)
