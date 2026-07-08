"""Schedules and maintenance windows (docs/REQUIREMENTS.md section 10).

Schedules are simple intervals ("30m", "4h", "1d") OR 5-field cron
expressions ("0 2 * * 0" = 02:00 on Sundays). Maintenance windows are
"HH:MM-HH:MM" ranges during which a zone may be polled; overnight windows
(e.g. "22:00-06:00") are supported and can be evaluated in a per-site
timezone.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

_INTERVAL_RE = re.compile(r"^(\d+)\s*([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def is_cron(spec: str) -> bool:
    return len(spec.split()) == 5


def parse_interval(spec: str) -> timedelta:
    m = _INTERVAL_RE.match(spec.strip().lower())
    if not m:
        raise ValueError(
            f"invalid interval {spec!r} (expected e.g. '30m', '4h', '1d')"
        )
    return timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2)])


def _cron_field(field: str, low: int, high: int) -> set[int]:
    """Parse one cron field into the set of matching values. Supports
    '*', lists (a,b), ranges (a-b), and steps (*/n, a-b/n)."""
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        for v in range(start, end + 1, step):
            if low <= v <= high:
                values.add(v)
    return values


def validate_schedule(spec: str) -> None:
    """Raise ValueError if `spec` is neither a valid interval nor cron."""
    if is_cron(spec):
        cron_matches(spec, datetime(2000, 1, 1))   # parse-check
    else:
        parse_interval(spec)


def cron_matches(spec: str, when: datetime) -> bool:
    """True if the cron expression fires at minute `when` (day-of-week:
    0 and 7 = Sunday, cron style)."""
    minute, hour, dom, month, dow = spec.split()
    minutes = _cron_field(minute, 0, 59)
    hours = _cron_field(hour, 0, 23)
    doms = _cron_field(dom, 1, 31)
    months = _cron_field(month, 1, 12)
    dows = _cron_field(dow, 0, 7)
    # cron: Sunday is both 0 and 7.
    weekday = (when.weekday() + 1) % 7  # Python Mon=0 -> cron Sun=0
    dow_ok = weekday in dows or (weekday == 0 and 7 in dows)
    return (when.minute in minutes and when.hour in hours
            and when.month in months and when.day in doms and dow_ok)


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


def in_window(spec: str | None, now: datetime | None = None,
              tz: str | None = None) -> bool:
    """True if `now` falls inside the window. No window means always open.
    If `tz` is an IANA name, the window is evaluated in that timezone."""
    if not spec:
        return True
    start, end = parse_window(spec)
    current_dt = now or datetime.now()
    if tz:
        try:
            from zoneinfo import ZoneInfo
            if current_dt.tzinfo is None:
                from datetime import timezone
                current_dt = current_dt.replace(tzinfo=timezone.utc)
            current_dt = current_dt.astimezone(ZoneInfo(tz))
        except Exception:
            pass  # unknown tz -> fall back to naive
    current = current_dt.time()
    if start <= end:
        return start <= current <= end
    # Overnight window, e.g. 22:00-06:00.
    return current >= start or current <= end
