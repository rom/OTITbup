from otitbup import anomaly


def _run(started, dur, ok=True, changed=False):
    return {"started_at": started, "finished_at": started + dur,
            "ok": ok, "changed": changed}


def test_no_anomaly_on_steady_history():
    runs = [_run(1000 - i * 100, 2.0) for i in range(12)]
    assert anomaly.analyze("s/z/d", runs) == []


def test_slow_backup_flagged():
    # Baseline ~2s, latest 30s -> a clear outlier.
    history = [_run(1000 - i * 100, 2.0 + (i % 3) * 0.1) for i in range(1, 12)]
    latest = [_run(1100, 30.0)]
    runs = latest + history
    found = anomaly.analyze("s/z/d", runs)
    assert any(a.kind == "slow" for a in found)


def test_slow_ignores_sub_floor():
    # A jump from 0.1s to 1s is proportionally huge but below the floor.
    history = [_run(1000 - i * 100, 0.1) for i in range(1, 12)]
    runs = [_run(1100, 1.0)] + history
    assert [a for a in anomaly.analyze("s/z/d", runs) if a.kind == "slow"] == []


def test_slow_needs_history():
    runs = [_run(1100, 30.0), _run(1000, 2.0)]
    assert anomaly.analyze("s/z/d", runs) == []


def test_change_storm_flagged():
    # Long-run baseline stable, but the last 5 all changed.
    stable = [_run(1000 - i * 100, 2.0, changed=False) for i in range(6, 30)]
    storm = [_run(1000 - i * 100, 2.0, changed=True) for i in range(0, 5)]
    runs = storm + stable
    found = anomaly.analyze("s/z/d", runs)
    assert any(a.kind == "change_storm" for a in found)


def test_change_storm_quiet_when_baseline_high():
    # A device that always changes should not trip the storm detector.
    runs = [_run(1000 - i * 100, 2.0, changed=True) for i in range(30)]
    found = anomaly.analyze("s/z/d", runs)
    assert not any(a.kind == "change_storm" for a in found)


def test_disabled():
    history = [_run(1000 - i * 100, 2.0) for i in range(1, 12)]
    runs = [_run(1100, 30.0)] + history
    assert anomaly.analyze("s/z/d", runs, {"enabled": False}) == []
