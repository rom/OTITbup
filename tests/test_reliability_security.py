import subprocess
import textwrap
import time
from datetime import datetime

import pytest

from otitbup import apitoken
from otitbup.config import ConfigError, load_config
from otitbup.drivers import register
from otitbup.drivers.base import Artifact, Driver
from otitbup.gitstore import GitStore
from otitbup.runstore import RunStore, SCHEMA_VERSION
from otitbup.windows import cron_matches, in_window, is_cron, validate_schedule


# ----------------------------------------------------- schema migrations

def test_migrations_apply_and_are_idempotent(tmp_path):
    db = tmp_path / "r.db"
    rs = RunStore(db)
    assert rs.version() == SCHEMA_VERSION
    # Reopening runs no migrations and preserves data.
    rs.add_user("u", "h", "admin", time.time(), scopes="plant-a/*")
    rs2 = RunStore(db)
    assert rs2.version() == SCHEMA_VERSION
    assert rs2.get_users()["u"]["scopes"] == "plant-a/*"


def test_migration_upgrades_old_db(tmp_path):
    # Simulate a v1 database, then let RunStore migrate it forward.
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT, "
        "role TEXT, created_at REAL);"
        "CREATE TABLE audit (id INTEGER PRIMARY KEY, at REAL, actor TEXT, "
        "role TEXT, action TEXT, detail TEXT);"
        "CREATE TABLE runs (id INTEGER PRIMARY KEY, device TEXT, "
        "started_at REAL, finished_at REAL, ok INT, changed INT, "
        "commit_hash TEXT, message TEXT, expected INT);"
        "CREATE TABLE rehearsals (id INTEGER PRIMARY KEY, device TEXT, at REAL, "
        "commit_hash TEXT, result TEXT, tested_by TEXT, notes TEXT);"
        "CREATE TABLE maintenance (device TEXT PRIMARY KEY, until REAL, "
        "reason TEXT, set_by TEXT, set_at REAL);"
        "CREATE TABLE baselines (device TEXT PRIMARY KEY, commit_hash TEXT, "
        "set_at REAL, set_by TEXT, note TEXT);"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO users VALUES ('a','h','admin',0)")
    conn.commit()
    conn.close()

    rs = RunStore(db)          # should add scopes, audit chain, api_tokens
    assert rs.version() == SCHEMA_VERSION
    assert rs.get_users()["a"]["scopes"] == "*"     # backfilled default
    apitoken.create(rs, "t", "operator", "*")        # api_tokens table exists


# ------------------------------------------------ tamper-evident audit

def test_audit_chain_detects_tampering(tmp_path):
    rs = RunStore(tmp_path / "r.db")
    for i in range(5):
        rs.audit(time.time(), f"action{i}", actor="alice")
    assert rs.verify_audit() == (True, 0)

    import sqlite3
    conn = sqlite3.connect(tmp_path / "r.db")
    conn.execute("UPDATE audit SET detail='hacked' WHERE id=3")
    conn.commit()
    conn.close()
    intact, bad = rs.verify_audit()
    assert not intact and bad == 3


# ------------------------------------------------------------ cron & tz

def test_cron_schedule_parsing_and_match():
    assert is_cron("0 2 * * 0")
    assert not is_cron("12h")
    # 02:00 on a Sunday.
    assert cron_matches("0 2 * * 0", datetime(2026, 7, 5, 2, 0))   # Sun
    assert not cron_matches("0 2 * * 0", datetime(2026, 7, 6, 2, 0))  # Mon
    assert cron_matches("*/15 * * * *", datetime(2026, 7, 6, 9, 30))
    assert not cron_matches("*/15 * * * *", datetime(2026, 7, 6, 9, 31))


def test_validate_schedule_accepts_both():
    validate_schedule("30m")
    validate_schedule("0 2 * * 1-5")
    with pytest.raises(ValueError):
        validate_schedule("whenever")


def test_timezone_aware_window():
    # 23:00 UTC is 18:00 in New York (UTC-5, winter). A window of
    # 17:00-19:00 local should be open then, closed in naive UTC.
    ts = datetime(2026, 1, 15, 23, 0)   # naive, treated as UTC
    assert in_window("17:00-19:00", ts, tz="America/New_York")
    assert not in_window("17:00-19:00", ts)   # naive UTC -> 23:00, closed


# ----------------------------------------------- retry / hooks / dry-run

class FlakyDriver(Driver):
    name = "flaky"
    fail_times = 0
    calls = 0

    def collect(self, device, secrets):
        type(self).calls += 1
        if type(self).calls <= type(self).fail_times:
            raise RuntimeError("transient")
        return [Artifact(name="c.txt", data=b"ok")]


@pytest.fixture
def flaky_config(tmp_path):
    register("flaky", FlakyDriver)
    FlakyDriver.calls = 0
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        retry:
          attempts: 3
          backoff: 0.01
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {name: d1, driver: flaky}
    """))
    return load_config(cfg)


def _runner(config):
    from otitbup.runner import Runner
    store = GitStore(config.data_dir)
    return Runner(config, store)


def test_retry_recovers_after_transient_failure(flaky_config):
    FlakyDriver.fail_times = 2       # fail twice, succeed on the 3rd
    results = _runner(flaky_config).backup_devices(
        flaky_config.all_devices())
    assert results[0].ok
    assert FlakyDriver.calls == 3


def test_retry_gives_up_after_attempts(flaky_config):
    FlakyDriver.fail_times = 99
    results = _runner(flaky_config).backup_devices(
        flaky_config.all_devices())
    assert not results[0].ok
    assert FlakyDriver.calls == 3    # attempts capped


def test_dry_run_collects_but_does_not_commit(flaky_config):
    from otitbup.runner import Runner
    FlakyDriver.fail_times = 0
    config = flaky_config
    store = GitStore(config.data_dir)
    runner = Runner(config, store, dry_run=True)
    results = runner.backup_devices(config.all_devices())
    assert results[0].ok
    assert "dry run" in results[0].message
    store.ensure_repo()
    assert store.last_commit_hash(config.all_devices()[0]) is None


def test_pre_post_hooks_run(tmp_path):
    register("flaky", FlakyDriver)
    FlakyDriver.fail_times = 0
    FlakyDriver.calls = 0
    marker = tmp_path / "hook.log"
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent(f"""
        data_dir: ./data
        hooks:
          pre: "echo pre $OTITBUP_DEVICE >> {marker}"
          post: "echo post $OTITBUP_OK >> {marker}"
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {{name: d1, driver: flaky}}
    """))
    config = load_config(cfg)
    _runner(config).backup_devices(config.all_devices())
    text = marker.read_text()
    assert "pre s/z/d1" in text
    assert "post 1" in text


# --------------------------------------------------------- api tokens

def test_api_token_lifecycle(tmp_path):
    rs = RunStore(tmp_path / "r.db")
    plaintext = apitoken.create(rs, "ci", "operator", "plant-a/*", days=30)
    assert plaintext.startswith("otb_")
    ident = apitoken.authenticate(rs, plaintext)
    assert ident["role"] == "operator"
    assert ident["scopes"] == "plant-a/*"
    assert apitoken.authenticate(rs, "otb_wrong") is None
    assert rs.delete_api_token("ci") == 1
    assert apitoken.authenticate(rs, plaintext) is None


def test_api_token_expiry(tmp_path):
    rs = RunStore(tmp_path / "r.db")
    _, digest = apitoken.generate()
    rs.add_api_token(digest, "old", "viewer", "*", time.time() - 100,
                     expires_at=time.time() - 10)
    assert rs.get_api_token(digest, time.time()) is None    # expired


# --------------------------------------------------------- git housekeeping

def test_gc_runs(tmp_path):
    store = GitStore(tmp_path / "data")
    store.ensure_repo()
    from otitbup.models import Device
    dev = Device(name="d", driver="x", site="s", zone="z")
    store.write_and_commit(dev, [Artifact(name="c.txt", data=b"v1")])
    store.gc()
    assert store.repo_size_bytes() > 0


# --------------------------------------------------- signed commits

def test_signed_commit_when_key_configured(tmp_path):
    # Generate an SSH key; skip if ssh-keygen or git signing is unavailable.
    key = tmp_path / "sign_key"
    try:
        rc = subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f",
                             str(key)], capture_output=True).returncode
    except FileNotFoundError:
        pytest.skip("ssh-keygen unavailable")
    if rc != 0:
        pytest.skip("ssh-keygen failed")
    store = GitStore(tmp_path / "data", sign_key=str(key))
    store.ensure_repo()
    # Configure an allowed-signers so verify-commit can succeed; if the git
    # version doesn't support SSH signing, the commit itself fails -> skip.
    from otitbup.models import Device
    dev = Device(name="d", driver="x", site="s", zone="z")
    try:
        commit = store.write_and_commit(dev, [Artifact(name="c.txt", data=b"v1")])
    except Exception:
        pytest.skip("git SSH signing unsupported here")
    assert commit
    # The commit should carry a signature header.
    raw = subprocess.run(["git", "-C", str(store.root), "cat-file", "-p",
                          commit], capture_output=True, text=True).stdout
    assert "gpgsig" in raw or "-----BEGIN SSH SIGNATURE-----" in raw
