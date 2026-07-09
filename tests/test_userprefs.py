from otitbup.userprefs import UserPrefs


def test_set_get_and_persist_to_disk(tmp_path):
    path = tmp_path / "sub" / "user-prefs.json"
    prefs = UserPrefs(path)

    assert prefs.get_value("alice", "theme") is None
    assert prefs.get_value("alice", "theme", "auto") == "auto"

    prefs.set_value("alice", "theme", "autumn")
    assert path.exists()                        # created lazily, on disk
    assert prefs.get_value("alice", "theme") == "autumn"

    # A fresh instance reads the same on-disk value.
    assert UserPrefs(path).get_value("alice", "theme") == "autumn"


def test_isolation_between_users(tmp_path):
    prefs = UserPrefs(tmp_path / "p.json")
    prefs.set_value("alice", "theme", "sky")
    prefs.set_value("bob", "theme", "desert")
    assert prefs.get_value("alice", "theme") == "sky"
    assert prefs.get_value("bob", "theme") == "desert"


def test_empty_value_clears_key_and_prunes_user(tmp_path):
    path = tmp_path / "p.json"
    prefs = UserPrefs(path)
    prefs.set_value("alice", "theme", "autumn")
    prefs.set_value("alice", "theme", "")       # clearing removes the key
    assert prefs.get_value("alice", "theme") is None
    # The now-empty user is pruned from the store entirely.
    import json
    assert "alice" not in json.loads(path.read_text())


def test_corrupt_file_reads_as_empty(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{ not json")
    prefs = UserPrefs(path)
    assert prefs.get_value("alice", "theme") is None
    # And a subsequent write recovers cleanly.
    prefs.set_value("alice", "theme", "spring")
    assert prefs.get_value("alice", "theme") == "spring"
