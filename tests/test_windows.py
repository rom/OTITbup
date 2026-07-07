from datetime import datetime, timedelta

import pytest

from otitbup.windows import in_window, parse_interval


def _at(hour, minute=0):
    return datetime(2026, 7, 7, hour, minute)


def test_parse_interval():
    assert parse_interval("30m") == timedelta(minutes=30)
    assert parse_interval("4h") == timedelta(hours=4)
    assert parse_interval("1d") == timedelta(days=1)
    with pytest.raises(ValueError):
        parse_interval("soon")


def test_no_window_is_always_open():
    assert in_window(None)
    assert in_window("")


def test_daytime_window():
    assert in_window("08:00-17:00", _at(12))
    assert not in_window("08:00-17:00", _at(18))
    assert in_window("08:00-17:00", _at(8))


def test_overnight_window():
    assert in_window("22:00-06:00", _at(23))
    assert in_window("22:00-06:00", _at(3))
    assert not in_window("22:00-06:00", _at(12))
