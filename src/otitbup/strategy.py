"""Evaluate the deployment against the 3-2-1 (and 3-2-1-1-0) backup rules.

3-2-1     : 3 copies of the data, on 2 different media, 1 kept offsite.
3-2-1-1-0 : + 1 copy offline/air-gapped, and 0 errors (backups verify).

For otitbup the copies map to:
  - copy 1  the local git repo + blob store on the appliance
  - copy 2  a git remote mirror (git.push + git.remote) — different host/medium
  - offsite the remote mirror if it is off-appliance, or a declared offsite
  - offline a portable export archive (`otitbup export`) taken to removable
            or air-gapped media; declare its cadence in strategy.offline
  - 0 errors `otitbup verify` passes and no device is currently failing

Declarations that can't be inferred live under `strategy` in the config:

    strategy:
      offsite: true            # the git remote is genuinely off-site
      offline:
        path: /mnt/usb/otitbup-export.tar.gz   # where the offline copy lives
        max_age_days: 7                        # how fresh it must be
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Check:
    key: str
    label: str
    ok: bool
    detail: str
    remediation: str = ""


@dataclass
class StrategyResult:
    checks: list[Check] = field(default_factory=list)
    copies: int = 1
    media: int = 1

    def rule(self, keys: list[str]) -> bool:
        by_key = {c.key: c for c in self.checks}
        return all(by_key.get(k, Check(k, "", False, "")).ok for k in keys)

    @property
    def satisfies_321(self) -> bool:
        return self.rule(["three_copies", "two_media", "one_offsite"])

    @property
    def satisfies_32110(self) -> bool:
        return self.satisfies_321 and self.rule(["one_offline", "zero_errors"])


def evaluate(config, store, runstore, blobstore=None,
             now: float | None = None) -> StrategyResult:
    now = now if now is not None else time.time()
    result = StrategyResult()
    git_cfg = config.git or {}
    strat = getattr(config, "strategy", {}) or {}

    has_remote = bool(git_cfg.get("push") and git_cfg.get("remote"))
    copies = 1 + (1 if has_remote else 0)
    offline_cfg = strat.get("offline") or {}
    offline_path = offline_cfg.get("path")
    offline_ok = False
    offline_detail = "no offline export declared (strategy.offline.path)"
    if offline_path:
        p = Path(offline_path)
        if not p.is_absolute():
            # Resolve relative to the appliance base (data_dir's parent).
            p = Path(config.data_dir).parent / offline_path
        if p.exists():
            age_days = (now - p.stat().st_mtime) / 86400
            max_age = float(offline_cfg.get("max_age_days", 30))
            offline_ok = age_days <= max_age
            offline_detail = (
                f"{offline_path}: {age_days:.1f}d old "
                f"(max {max_age:.0f}d)"
            )
        else:
            offline_detail = f"{offline_path}: not found"
    if offline_ok:
        copies += 1

    result.copies = copies
    result.media = 1 + (1 if has_remote else 0) + (1 if offline_ok else 0)

    result.checks.append(Check(
        "three_copies", "≥3 copies of the backup data",
        copies >= 3, f"{copies} copy(ies) detected",
        "" if copies >= 3 else
        "enable a git remote mirror (git.push+remote) and take a periodic "
        "offline export (`otitbup export`)",
    ))
    result.checks.append(Check(
        "two_media", "≥2 different media / locations",
        result.media >= 2, f"{result.media} medium(s)",
        "" if result.media >= 2 else
        "mirror to a remote git server on a different host",
    ))
    result.checks.append(Check(
        "one_offsite", "≥1 copy kept offsite",
        has_remote and bool(strat.get("offsite", has_remote)),
        "remote git mirror configured" if has_remote else "no remote mirror",
        "" if has_remote else
        "configure git.remote to an off-site git server",
    ))
    result.checks.append(Check(
        "one_offline", "≥1 copy offline / air-gapped",
        offline_ok, offline_detail,
        "" if offline_ok else
        "run `otitbup export` to removable/air-gapped media and set "
        "strategy.offline.path",
    ))

    # 0 errors: verification passes and no device currently failing.
    from .verify import verify
    report = verify(config, store, blobstore=blobstore)
    failing = 0
    for device in config.all_devices():
        if runstore.status(device.qualified_name).consecutive_failures > 0:
            failing += 1
    zero = report.ok and failing == 0
    result.checks.append(Check(
        "zero_errors", "0 errors (backups verify, none failing)",
        zero,
        f"verify: {'clean' if report.ok else str(len(report.problems)) + ' problem(s)'}"
        f", {failing} device(s) failing",
        "" if zero else "resolve failing backups and run `otitbup verify`",
    ))
    return result
