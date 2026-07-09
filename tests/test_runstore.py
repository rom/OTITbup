import pytest

from otitbup.runstore import RunRecord, RunStore


@pytest.fixture
def rs(tmp_path):
    return RunStore(tmp_path / "runstore.db")


def _run(device, started, ok, changed, expected=None):
    return RunRecord(
        device=device, started_at=started, finished_at=started + 1,
        ok=ok, changed=changed, commit_hash="abc" if changed else None,
        message="changed" if changed else "no change", expected=expected,
    )


def test_status_tracks_success_failure_change(rs):
    rs.record_run(_run("d1", 100, True, True))       # success + change
    rs.record_run(_run("d1", 200, False, False))     # fail
    rs.record_run(_run("d1", 300, False, False))     # fail again
    status = rs.status("d1")
    assert status.total_runs == 3
    assert status.last_attempt == 300
    assert status.last_ok is False
    assert status.last_success == 100
    assert status.last_change == 100
    assert status.consecutive_failures == 2


def test_status_no_runs(rs):
    status = rs.status("unknown")
    assert status.last_attempt is None
    assert status.consecutive_failures == 0


def test_recent_runs_ordering(rs):
    for t in (100, 200, 300):
        rs.record_run(_run("d1", t, True, False))
    runs = rs.recent_runs("d1", limit=2)
    assert [r["started_at"] for r in runs] == [300, 200]


def test_rehearsals(rs):
    rs.record_rehearsal("d1", 500, "abc123", "pass", tested_by="alice",
                        notes="ok")
    last = rs.last_rehearsal("d1")
    assert last["result"] == "pass"
    assert last["tested_by"] == "alice"
    assert rs.last_rehearsal("d2") is None


def test_event_log_records_and_filters(rs):
    rs.record_event(100.0, "backup.stop", "backup finished: d1",
                    severity="info", detail="d1")
    rs.record_event(200.0, "backup.error",
                    "backup failed: d1: siemens_s7 requires python-snap7",
                    severity="error", detail="d1")
    rs.record_event(150.0, "anomaly.detected", "slow backup on d1",
                    severity="warning", detail="d1")

    events = rs.recent_events()
    assert [e["at"] for e in events] == [200.0, 150.0, 100.0]  # newest first
    assert events[0]["message"].endswith("python-snap7")
    assert events[0]["type"] == "backup.error"

    # Severity filter keeps only the requested levels.
    problems = rs.recent_events(severities=["error", "warning"])
    assert {e["severity"] for e in problems} == {"error", "warning"}
    assert all(e["type"] != "backup.stop" for e in problems)


def test_maintenance_scope_and_expiry(rs):
    rs.set_maintenance("plant-a/cell-1/plc-01", None, now=1000)
    assert rs.in_maintenance("plant-a/cell-1/plc-01", 2000)

    # Zone wildcard covers a device in that zone.
    rs.set_maintenance("plant-a/cell-2/*", until=5000, now=1000)
    assert rs.in_maintenance("plant-a/cell-2/plc-09", 4000)
    assert not rs.in_maintenance("plant-a/cell-2/plc-09", 6000)  # expired

    # Site wildcard.
    rs.set_maintenance("plant-b/*", None, now=1000)
    assert rs.in_maintenance("plant-b/zoneX/dev", 2000)

    assert not rs.in_maintenance("other/z/d", 2000)

    rs.clear_maintenance("plant-a/cell-1/plc-01")
    assert not rs.in_maintenance("plant-a/cell-1/plc-01", 2000)
