"""Schedules and maintenance windows (REQUIREMENTS.md section 10).

Schedules are simple intervals ("30m", "4h", "1d"). Maintenance windows are
"HH:MM-HH:MM" ranges during which a zone may be polled; overnight windows
(e.g. "22:00-06:00") are supported.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

_INTERVAL_RE = re.compile(r"^(\d+)\s*([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(spec: str) -> timedelta:
    m = _INTERVAL_RE.match(spec.strip().lower())
    if not m:
        raise ValueError(
            f"invalid interval {spec!r} (expected e.g. '30m', '4h', '1d')"
        )
    return timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2)])


def parse_window(spec: str) -> tuple[time, time]:
    try:
        start_s, end_s = spec.strip().split("-")
        start = time.fromisoformat(start_s.strip())
        end = time.fromisoformat(end_s.strip())
    except ValueError as exc:
        raise ValueError(
            f"invalid maintenance window {spec!r} (expected 'HH:MM-HH:MM')"
        ) from exc
    return start, end


def in_window(spec: str | None, now: datetime | None = None) -> bool:
    """True if `now` falls inside the window. No window means always open."""
    if not spec:
        return True
    start, end = parse_window(spec)
    current = (now or datetime.now()).time()
    if start <= end:
        return start <= current <= end
    # Overnight window, e.g. 22:00-06:00.
    return current >= start or current <= end
