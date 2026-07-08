"""Anomaly detection over backup run history.

Failure and staleness alerts catch the obvious rot. This module catches the
subtler signals that a device is misbehaving *while still succeeding*:

  * duration spikes — a backup that suddenly takes far longer than its own
    historical norm (a struggling device, a saturated link, a driver
    retrying internally);
  * change storms — a device that normally changes rarely suddenly changing
    on most recent runs (config flapping, a stuck auto-save, tampering).

Everything here is a pure function over the plain run dicts returned by
`RunStore.recent_runs`, so it is trivially testable and has no I/O. The
runner calls `analyze` after each run and emits an ANOMALY event (which
fans out to alerts, syslog, SNMP, and tickets like any other event).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass
class Anomaly:
    device: str
    kind: str          # "slow" | "change_storm"
    message: str


def _durations(runs: list[dict]) -> list[float]:
    out = []
    for r in runs:
        if r.get("ok") and r.get("finished_at") and r.get("started_at"):
            d = float(r["finished_at"]) - float(r["started_at"])
            if d >= 0:
                out.append(d)
    return out


def _detect_slow(
    device: str, runs: list[dict], sigma: float, floor: float,
    min_history: int,
) -> Anomaly | None:
    """Flag when the most recent successful run is a statistical outlier in
    duration versus the device's own history. Needs enough history to have a
    meaningful baseline, and ignores sub-`floor` durations so millisecond
    jitter on fast local reads never trips it."""
    durations = _durations(runs)          # newest first
    if len(durations) < min_history + 1:
        return None
    latest, history = durations[0], durations[1:]
    mean = statistics.fmean(history)
    if latest < floor or latest <= mean:
        return None
    stdev = statistics.pstdev(history)
    # With near-zero variance, require a hard doubling instead of dividing by
    # a tiny stdev (which would flag trivial wobble).
    if stdev < 1e-6:
        if latest > max(mean * 2, floor):
            return Anomaly(
                device, "slow",
                f"backup took {latest:.1f}s vs ~{mean:.1f}s baseline",
            )
        return None
    if (latest - mean) / stdev >= sigma:
        return Anomaly(
            device, "slow",
            f"backup took {latest:.1f}s vs {mean:.1f}±{stdev:.1f}s baseline "
            f"({(latest - mean) / stdev:.1f}σ)",
        )
    return None


def _detect_change_storm(
    device: str, runs: list[dict], window: int, min_history: int,
    recent_rate: float, baseline_rate: float,
) -> Anomaly | None:
    """Flag when a normally-stable device changes on most of its recent
    runs. Compares the change rate over the last `window` runs against the
    long-run baseline; only fires when the recent rate is high *and* the
    baseline is low, so a device that legitimately changes often is quiet."""
    if len(runs) < max(window, min_history) + 1:
        return None
    recent = runs[:window]
    recent_changes = sum(1 for r in recent if r.get("changed"))
    r_rate = recent_changes / len(recent)
    all_changes = sum(1 for r in runs if r.get("changed"))
    b_rate = all_changes / len(runs)
    if r_rate >= recent_rate and b_rate <= baseline_rate:
        return Anomaly(
            device, "change_storm",
            f"changed on {recent_changes}/{len(recent)} recent runs "
            f"(baseline {b_rate * 100:.0f}%)",
        )
    return None


def _detect_flapping(
    device: str, runs: list[dict], window: int, min_transitions: int,
) -> Anomaly | None:
    """Flag a device oscillating between success and failure — an
    intermittent link or a device that only sometimes answers. Neither the
    failure alert (fires on a solid failure streak) nor the recovery alert
    catches a device that never settles."""
    recent = runs[:window]
    if len(recent) < 3:
        return None
    states = [bool(r.get("ok")) for r in recent]
    transitions = sum(1 for a, b in zip(states, states[1:], strict=False)
                      if a != b)
    fails = states.count(False)
    if transitions >= min_transitions and 0 < fails < len(states):
        return Anomaly(
            device, "flapping",
            f"{transitions} ok/fail transitions in last {len(recent)} runs "
            f"({fails} failed) — intermittent",
        )
    return None


def _detect_slow_trend(
    device: str, runs: list[dict], window: int, ratio: float, floor: float,
    min_history: int,
) -> Anomaly | None:
    """Flag a *gradual* slowdown: the recent window's mean duration is
    materially higher than the older baseline's. A single-point z-score
    misses a slow, sustained creep (a filling disk, a degrading link)."""
    durations = _durations(runs)          # newest first
    if len(durations) < window + min_history:
        return None
    recent = durations[:window]
    older = durations[window:]
    recent_mean = statistics.fmean(recent)
    older_mean = statistics.fmean(older)
    if older_mean <= 0 or recent_mean < floor:
        return None
    if recent_mean >= older_mean * ratio:
        return Anomaly(
            device, "slow_trend",
            f"recent backups avg {recent_mean:.1f}s vs {older_mean:.1f}s "
            f"baseline ({recent_mean / older_mean:.1f}x) — degrading",
        )
    return None


def _detect_size_drop(
    device: str, runs: list[dict], drop: float, min_history: int,
) -> Anomaly | None:
    """Flag when the latest capture is dramatically smaller than the device's
    trailing median — the signature of a truncated download or an error page
    that still 'succeeded'. Uses the size_bytes recorded per run."""
    sizes = [int(r["size_bytes"]) for r in runs
             if r.get("ok") and r.get("size_bytes")]
    if len(sizes) < min_history + 1:
        return None
    latest, history = sizes[0], sizes[1:]
    median = statistics.median(history)
    if median <= 0:
        return None
    if latest < median * drop:
        return Anomaly(
            device, "size_drop",
            f"capture {latest} bytes vs ~{median:.0f} baseline "
            f"({latest / median * 100:.0f}%) — possibly truncated",
        )
    return None


def analyze(device: str, runs: list[dict], cfg: dict | None = None) -> list[Anomaly]:
    """Return anomalies for a device given its run history (newest first).

    Tunables (all under the `anomaly:` config section):
      sigma            duration z-score threshold           (default 3.0)
      duration_floor   ignore runs faster than this, seconds (default 5.0)
      min_history      runs required before judging          (default 8)
      change_window    recent-run window for change storms   (default 5)
      change_recent    recent change-rate trigger            (default 0.8)
      change_baseline  max baseline change-rate to fire      (default 0.2)
      flap_window      recent-run window for flapping        (default 6)
      flap_transitions ok/fail transitions that trip it      (default 3)
      trend_window     recent-run window for slow trend      (default 5)
      trend_ratio      recent/baseline duration multiplier   (default 2.0)
      size_drop        flag if latest < this × median size   (default 0.5)
    """
    cfg = cfg or {}
    if cfg.get("enabled") is False:
        return []
    min_history = int(cfg.get("min_history", 8))
    floor = float(cfg.get("duration_floor", 5.0))
    out: list[Anomaly] = []
    slow = _detect_slow(
        device, runs, sigma=float(cfg.get("sigma", 3.0)),
        floor=floor, min_history=min_history,
    )
    if slow:
        out.append(slow)
    storm = _detect_change_storm(
        device, runs,
        window=int(cfg.get("change_window", 5)),
        min_history=min_history,
        recent_rate=float(cfg.get("change_recent", 0.8)),
        baseline_rate=float(cfg.get("change_baseline", 0.2)),
    )
    if storm:
        out.append(storm)
    flap = _detect_flapping(
        device, runs,
        window=int(cfg.get("flap_window", 6)),
        min_transitions=int(cfg.get("flap_transitions", 3)),
    )
    if flap:
        out.append(flap)
    trend = _detect_slow_trend(
        device, runs,
        window=int(cfg.get("trend_window", 5)),
        ratio=float(cfg.get("trend_ratio", 2.0)),
        floor=floor, min_history=min_history,
    )
    if trend:
        out.append(trend)
    size_drop = _detect_size_drop(
        device, runs,
        drop=float(cfg.get("size_drop", 0.5)),
        min_history=min_history,
    )
    if size_drop:
        out.append(size_drop)
    return out
