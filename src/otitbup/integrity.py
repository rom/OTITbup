"""Continuous integrity checking (scrubbing).

Backups rot silently: a blob bit-flips on disk, a git object corrupts, a
commit's signature stops verifying. `otitbup verify` re-hashes stored
artifacts against their manifests — but only when a human runs it. This
module ties three layers of integrity into one check that the daemon runs
on a schedule and that alerts when something is wrong:

  1. content   — `verify`: every artifact re-hashed against its manifest,
                 and offloaded blobs present + intact;
  2. repository — `git fsck`: no missing/broken git objects;
  3. provenance — optionally, that signed commits still verify.

The result is stored (runstore `meta`, key `integrity`) so the Health page
and /metrics can show the last scrub, and a failure emits an
`integrity.error` event and an alert.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger("otitbup.integrity")


@dataclass
class IntegrityResult:
    ok: bool
    content_problems: list[str] = field(default_factory=list)
    repo_problems: list[str] = field(default_factory=list)
    signature_problems: list[str] = field(default_factory=list)
    checked_devices: int = 0
    checked_commits: int = 0
    orphan_blobs: int = 0

    @property
    def problems(self) -> list[str]:
        return (self.content_problems + self.repo_problems
                + self.signature_problems)

    def summary(self) -> str:
        if self.ok:
            return (f"integrity OK: {self.checked_commits} commit(s), "
                    f"{self.checked_devices} device(s), repo healthy")
        parts = []
        if self.content_problems:
            parts.append(f"{len(self.content_problems)} content")
        if self.repo_problems:
            parts.append(f"{len(self.repo_problems)} repo")
        if self.signature_problems:
            parts.append(f"{len(self.signature_problems)} signature")
        return "integrity FAILED: " + ", ".join(parts) + " problem(s)"


def check(config, store, blobstore=None, *, all_commits: bool = False,
          do_fsck: bool = True, do_signatures: bool = False) -> IntegrityResult:
    """Run the content/repo/(signature) integrity checks and return a
    combined result. Never raises — a checker that itself errors becomes a
    problem line."""
    from .verify import verify
    result = IntegrityResult(ok=True)

    try:
        report = verify(config, store, blobstore=blobstore,
                        all_commits=all_commits)
        result.checked_devices = report.checked_devices
        result.checked_commits = report.checked_commits
        result.orphan_blobs = getattr(report, "orphan_blobs", 0)
        for key, problems in report.problems.items():
            for problem in problems:
                result.content_problems.append(f"{key}: {problem}")
    except Exception as exc:                                  # pragma: no cover
        result.content_problems.append(f"verify failed to run: {exc}")

    if do_fsck:
        try:
            result.repo_problems = store.fsck()
        except Exception as exc:                              # pragma: no cover
            result.repo_problems = [f"git fsck failed to run: {exc}"]

    if do_signatures and getattr(store, "sign_key", None):
        try:
            for device in config.all_devices():
                commit = store.last_commit_hash(device)
                if commit and not store.verify_commit_signature(commit):
                    result.signature_problems.append(
                        f"{device.qualified_name}@{commit[:8]}: "
                        "signature does not verify")
        except Exception as exc:                              # pragma: no cover
            result.signature_problems.append(f"signature check failed: {exc}")

    result.ok = not result.problems
    return result


def run_scheduled(config, store, blobstore, runstore, events, alerts,
                  now: float, *, all_commits: bool = False) -> IntegrityResult:
    """Run an integrity scrub, persist the result for the UI/metrics, emit an
    event, and alert on failure. Called by the daemon and the CLI."""
    icfg = config.integrity or {}
    result = check(
        config, store, blobstore=blobstore,
        all_commits=bool(icfg.get("all_commits", all_commits)),
        do_fsck=icfg.get("fsck", True) is not False,
        do_signatures=bool(icfg.get("signatures", False)),
    )
    if runstore is not None:
        try:
            runstore.set_meta("integrity", json.dumps({
                "ok": result.ok,
                "at": now,
                "summary": result.summary(),
                "problems": result.problems[:50],
            }), now)
        except Exception as exc:
            log.debug("could not persist integrity result: %s", exc)

    from .events import INTEGRITY_ERROR, INTEGRITY_OK
    if result.ok:
        if events is not None:
            events.emit(INTEGRITY_OK, result.summary())
    else:
        body = "\n".join(result.problems[:50])
        if events is not None:
            events.emit(INTEGRITY_ERROR, result.summary(),
                        severity="error", detail=body)
        if alerts is not None:
            alerts.notify("otitbup: INTEGRITY CHECK FAILED",
                          result.summary() + "\n\n" + body)
    return result
