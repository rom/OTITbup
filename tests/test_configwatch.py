"""Config-change detection between runs (configwatch)."""
import textwrap

import pytest
import yaml

from otitbup import configwatch
from otitbup.configwatch import ConfigChange, check, diff_raw, review

BASE = textwrap.dedent("""
    data_dir: ./data
    alerts:
      stale_days: 7
    sites:
      - name: plant-a
        zones:
          - name: cell-1
            devices:
              - name: plc-01
                driver: fake
                address: 10.0.0.1
                schedule: 1h
""")


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "otitbup.yml"
    path.write_text(BASE)
    return path, tmp_path / "config-snapshot.json"


def _rewrite(path, mutate):
    raw = yaml.safe_load(path.read_text())
    mutate(raw)
    path.write_text(yaml.safe_dump(raw))


def test_first_run_writes_snapshot_and_returns_none(cfg):
    path, snap = cfg
    assert check(path, snap) is None
    assert snap.exists()


def test_unchanged_config_is_empty_change(cfg):
    path, snap = cfg
    check(path, snap)
    change = check(path, snap)
    assert isinstance(change, ConfigChange)
    assert not change


def test_minor_change_detected(cfg):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw["alerts"].update(stale_days=14))
    change = check(path, snap)
    assert change.minor and not change.major
    assert "alerts.stale_days" in change.minor[0]


def test_added_device_is_minor(cfg):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw["sites"][0]["zones"][0]["devices"].append(
        {"name": "plc-02", "driver": "fake", "schedule": "1h"}))
    change = check(path, snap)
    assert not change.major
    assert any("plc-02" in d for d in change.minor)


def test_removed_device_is_major(cfg):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw["sites"][0]["zones"][0].update(devices=[]))
    change = check(path, snap)
    assert change.major
    assert any("plc-01" in d for d in change.major)


def test_device_driver_and_address_changes_are_major(cfg):
    path, snap = cfg
    check(path, snap)

    def mutate(raw):
        dev = raw["sites"][0]["zones"][0]["devices"][0]
        dev["driver"] = "generic_ssh"
        dev["address"] = "10.9.9.9"
    _rewrite(path, mutate)
    change = check(path, snap)
    assert len(change.major) == 2


def test_new_field_on_existing_device_is_major(cfg):
    # Adding credentials/address to a device that already existed re-points
    # what/where/how it is backed up — it must not slip through as a minor
    # 'added' change (regression: the added-is-routine rule was too broad).
    path, snap = cfg
    check(path, snap)

    def mutate(raw):
        dev = raw["sites"][0]["zones"][0]["devices"][0]
        dev["credentials"] = "attacker-creds"
    _rewrite(path, mutate)
    change = check(path, snap)
    assert change.major and not change.minor
    assert any("credentials" in d for d in change.major)


def test_new_device_still_minor_when_field_gate_tightened(cfg):
    # A brand-new device (all fields "added") stays routine even though it
    # carries a driver/address/credentials.
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw["sites"][0]["zones"][0]["devices"].append(
        {"name": "plc-99", "driver": "generic_ssh", "address": "10.0.0.9",
         "credentials": "plc-99", "schedule": "1h"}))
    change = check(path, snap)
    assert not change.major
    assert any("plc-99" in d for d in change.minor)


def test_new_retention_on_existing_device_is_minor(cfg):
    path, snap = cfg
    check(path, snap)

    def mutate(raw):
        raw["sites"][0]["zones"][0]["devices"][0]["retention"] = {
            "keep_versions": 5}
    _rewrite(path, mutate)
    change = check(path, snap)
    assert change.minor and not change.major


def test_device_schedule_change_is_minor(cfg):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw["sites"][0]["zones"][0]["devices"][0]
             .update(schedule="12h"))
    change = check(path, snap)
    assert change.minor and not change.major


def test_critical_toplevel_keys_are_major(cfg):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw.update(
        data_dir="./elsewhere",
        secrets={"backend": "plainfile", "path": "s.yml"}))
    change = check(path, snap)
    majors = " ".join(change.major)
    assert "data_dir" in majors and "secrets" in majors


def test_diff_raw_reports_removed_and_changed_values():
    old = {"retention": {"lock_days": 90}, "logging": {"level": "info"}}
    new = {"logging": {"level": "debug"}}
    change = diff_raw(old, new)
    assert any("retention.lock_days" in d for d in change.major)
    assert any("logging.level" in d for d in change.minor)


def test_review_minor_auto_accepts(cfg, capsys):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw["alerts"].update(stale_days=30))
    assert review(path, snap) is True
    # Accepted: a second review is quiet.
    assert review(path, snap) is True
    assert not check(path, snap)


def test_review_major_noninteractive_keeps_warning(cfg, monkeypatch):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw.update(data_dir="./other"))
    monkeypatch.setattr(configwatch.sys.stdin, "isatty", lambda: False)
    assert review(path, snap) is True          # unattended runs continue...
    assert check(path, snap).major             # ...but are NOT accepted


def test_review_major_assume_yes_accepts(cfg, monkeypatch):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw.update(data_dir="./other"))
    monkeypatch.setattr(configwatch.sys.stdin, "isatty", lambda: False)
    assert review(path, snap, assume_yes=True) is True
    assert not check(path, snap)


def test_review_major_interactive_accept_and_decline(cfg, monkeypatch):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw.update(data_dir="./other"))
    monkeypatch.setattr(configwatch.sys.stdin, "isatty", lambda: True)

    # Decline: aborts and keeps the old snapshot.
    assert review(path, snap, prompt=lambda _msg: "n") is False
    assert check(path, snap).major
    # Accept: proceeds and refreshes the snapshot.
    assert review(path, snap, prompt=lambda _msg: "y") is True
    assert not check(path, snap)


def test_review_emits_config_changed_event(cfg):
    path, snap = cfg
    check(path, snap)
    _rewrite(path, lambda raw: raw["alerts"].update(stale_days=1))

    emitted = []

    class Bus:
        def emit(self, event_type, message, **kw):
            emitted.append((event_type, message, kw))

    review(path, snap, events=Bus())
    assert emitted and emitted[0][0] == "config.changed"


def test_corrupt_snapshot_resets_cleanly(cfg):
    path, snap = cfg
    snap.write_text("{not json")
    assert check(path, snap) is None
    assert check(path, snap) == ConfigChange()
