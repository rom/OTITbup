"""Persisted operational state: backup run results and restore rehearsals.

Git records what was backed up and when it changed; it does NOT record a
*failed* attempt (nothing is committed) or a run's duration. Without that,
a device that has been failing for two weeks looks identical to one that
is simply unchanged. This SQLite store closes that gap and is the source
for staleness alerts, health metrics, reports, and the JSON API.

Lives at <data_dir>/../runstore.db — next to, not inside, the git repo.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# Ordered schema migrations. Each entry is applied once, in order, and
# PRAGMA user_version tracks how far we've come — so upgrading the tool
# never loses data and never re-applies a step. Append new migrations;
# never edit an existing one.
_MIGRATIONS: list[str] = [
    # v1 — base schema.
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL,
        started_at REAL NOT NULL, finished_at REAL NOT NULL,
        ok INTEGER NOT NULL, changed INTEGER NOT NULL,
        commit_hash TEXT, message TEXT, expected INTEGER);
    CREATE INDEX IF NOT EXISTS ix_runs_device ON runs(device, started_at);
    CREATE TABLE IF NOT EXISTS rehearsals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL,
        at REAL NOT NULL, commit_hash TEXT, result TEXT NOT NULL,
        tested_by TEXT, notes TEXT);
    CREATE INDEX IF NOT EXISTS ix_rehearsals_device ON rehearsals(device, at);
    CREATE TABLE IF NOT EXISTS maintenance (
        device TEXT PRIMARY KEY, until REAL, reason TEXT, set_by TEXT,
        set_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS baselines (
        device TEXT PRIMARY KEY, commit_hash TEXT NOT NULL,
        set_at REAL NOT NULL, set_by TEXT, note TEXT);
    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT,
        role TEXT, action TEXT NOT NULL, detail TEXT);
    CREATE INDEX IF NOT EXISTS ix_audit_at ON audit(at);
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'viewer', created_at REAL NOT NULL);
    """,
    # v2 — per-site/zone RBAC scoping (space-separated scope globs).
    "ALTER TABLE users ADD COLUMN scopes TEXT DEFAULT '*';",
    # v3 — tamper-evident audit hash chain.
    """
    ALTER TABLE audit ADD COLUMN prev_hash TEXT;
    ALTER TABLE audit ADD COLUMN entry_hash TEXT;
    """,
    # v4 — scoped API tokens (write API).
    """
    CREATE TABLE IF NOT EXISTS api_tokens (
        token_hash TEXT PRIMARY KEY, name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'viewer', scopes TEXT DEFAULT '*',
        created_at REAL NOT NULL, expires_at REAL, last_used REAL);
    """,
    # v5 — captured size per run (for size-drop / truncation detection).
    "ALTER TABLE runs ADD COLUMN size_bytes INTEGER;",
    # v6 — legal holds (retention/immutability protection).
    """
    CREATE TABLE IF NOT EXISTS holds (
        scope TEXT PRIMARY KEY, reason TEXT, set_by TEXT,
        set_at REAL NOT NULL);
    """,
    # v7 — small key/value store (e.g. last integrity-check result).
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT, updated_at REAL NOT NULL);
    """,
    # v8 — persistent operational event log (feeds the web UI Event logs).
    # Keeps the full human-readable message that the audit chain elides
    # (the audit row stores only a short detail for backup errors et al.).
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
        type TEXT NOT NULL, severity TEXT, actor TEXT, detail TEXT,
        message TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_events_at ON events(at);
    """,
]

SCHEMA_VERSION = len(_MIGRATIONS)


@dataclass
class RunRecord:
    device: str
    started_at: float
    finished_at: float
    ok: bool
    changed: bool
    commit_hash: str | None = None
    message: str = ""
    expected: bool | None = None
    size_bytes: int | None = None


@dataclass
class DeviceStatus:
    device: str
    last_attempt: float | None = None
    last_success: float | None = None
    last_ok: bool | None = None
    last_message: str = ""
    last_change: float | None = None
    consecutive_failures: int = 0
    total_runs: int = 0


class RunStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Serialises the audit hash-chain read-modify-write within this
        # process; a BEGIN IMMEDIATE transaction (see audit()) covers other
        # processes/connections.
        self._audit_lock = threading.Lock()
        self._migrate()

    def _migrate(self) -> None:
        """Apply any pending migrations, tracked by PRAGMA user_version."""
        with self._conn() as conn:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for version in range(current, len(_MIGRATIONS)):
                conn.executescript(_MIGRATIONS[version])
                conn.execute(f"PRAGMA user_version = {version + 1}")

    def version(self) -> int:
        with self._conn() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------- runs

    def record_run(self, run: RunRecord) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (device, started_at, finished_at, ok, "
                "changed, commit_hash, message, expected, size_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.device, run.started_at, run.finished_at,
                    int(run.ok), int(run.changed), run.commit_hash,
                    run.message,
                    None if run.expected is None else int(run.expected),
                    run.size_bytes,
                ),
            )

    def recent_runs(self, device: str | None = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM runs"
        params: list = []
        if device:
            query += " WHERE device = ?"
            params.append(device)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    def status(self, device: str) -> DeviceStatus:
        with self._conn() as conn:
            rows = list(conn.execute(
                "SELECT * FROM runs WHERE device = ? ORDER BY started_at DESC",
                (device,),
            ))
        status = DeviceStatus(device=device, total_runs=len(rows))
        if rows:
            latest = rows[0]
            status.last_attempt = latest["started_at"]
            status.last_ok = bool(latest["ok"])
            status.last_message = latest["message"] or ""
            for row in rows:
                if row["ok"] and status.last_success is None:
                    status.last_success = row["started_at"]
                if row["changed"] and status.last_change is None:
                    status.last_change = row["started_at"]
            for row in rows:
                if row["ok"]:
                    break
                status.consecutive_failures += 1
        return status

    # ---------------------------------------------------- rehearsals

    def record_rehearsal(
        self, device: str, at: float, commit_hash: str | None,
        result: str, tested_by: str | None = None, notes: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO rehearsals (device, at, commit_hash, result, "
                "tested_by, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (device, at, commit_hash, result, tested_by, notes),
            )

    def last_rehearsal(self, device: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM rehearsals WHERE device = ? "
                "ORDER BY at DESC LIMIT 1",
                (device,),
            ).fetchone()
        return dict(row) if row else None

    def rehearsals(self, device: str, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            return [
                dict(row) for row in conn.execute(
                    "SELECT * FROM rehearsals WHERE device = ? "
                    "ORDER BY at DESC LIMIT ?",
                    (device, limit),
                )
            ]


    # --------------------------------------------------- maintenance

    def set_maintenance(
        self, scope: str, until: float | None, now: float,
        reason: str | None = None, set_by: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO maintenance (device, until, reason, set_by, "
                "set_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(device) DO UPDATE SET until=excluded.until, "
                "reason=excluded.reason, set_by=excluded.set_by, "
                "set_at=excluded.set_at",
                (scope, until, reason, set_by, now),
            )

    def clear_maintenance(self, scope: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM maintenance WHERE device = ?", (scope,))

    def maintenance_scopes(self, now: float) -> dict[str, dict]:
        """Active (unexpired) maintenance scopes -> row dict."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM maintenance").fetchall()
        active = {}
        for row in rows:
            if row["until"] is not None and row["until"] < now:
                continue
            active[row["device"]] = dict(row)
        return active

    def in_maintenance(self, qualified_name: str, now: float) -> bool:
        """True if the device or a covering scope (site/*, site/zone/*) is
        under active maintenance."""
        active = self.maintenance_scopes(now)
        if qualified_name in active:
            return True
        parts = qualified_name.split("/")
        if len(parts) == 3:
            site, zone, _ = parts
            return (
                f"{site}/*" in active
                or f"{site}/{zone}/*" in active
            )
        return False


    # ------------------------------------------------------ meta k/v

    def set_meta(self, key: str, value: str, at: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, value, at))

    def get_meta(self, key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM meta WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    # --------------------------------------------------- legal holds

    def set_hold(self, scope: str, now: float, reason: str | None = None,
                 set_by: str | None = None) -> None:
        """Place a legal hold on a scope (device qualified name, 'site/*',
        'site/zone/*', or '*'). Held scopes are never pruned by retention."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO holds (scope, reason, set_by, set_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(scope) DO UPDATE SET "
                "reason=excluded.reason, set_by=excluded.set_by, "
                "set_at=excluded.set_at",
                (scope, reason, set_by, now))

    def clear_hold(self, scope: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "DELETE FROM holds WHERE scope = ?", (scope,)).rowcount

    def holds(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM holds ORDER BY scope")]

    def is_held(self, qualified_name: str) -> bool:
        """True if the device or a covering scope is under legal hold."""
        scopes = {row["scope"] for row in self.holds()}
        if "*" in scopes or qualified_name in scopes:
            return True
        parts = qualified_name.split("/")
        if len(parts) == 3:
            site, zone, _ = parts
            return f"{site}/*" in scopes or f"{site}/{zone}/*" in scopes
        return False

    # ----------------------------------------------------- baselines

    def set_baseline(
        self, device: str, commit_hash: str, at: float,
        set_by: str | None = None, note: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO baselines (device, commit_hash, set_at, set_by, "
                "note) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(device) DO UPDATE SET commit_hash=excluded."
                "commit_hash, set_at=excluded.set_at, set_by=excluded.set_by, "
                "note=excluded.note",
                (device, commit_hash, at, set_by, note),
            )

    def get_baseline(self, device: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM baselines WHERE device = ?", (device,)
            ).fetchone()
        return dict(row) if row else None

    def clear_baseline(self, device: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM baselines WHERE device = ?", (device,))

    # --------------------------------------------------------- audit

    def audit(
        self, at: float, action: str, actor: str | None = None,
        role: str | None = None, detail: str | None = None,
    ) -> None:
        import hashlib
        # The chain is a read-modify-write (read last hash, link to it,
        # insert). Concurrent writers — the backup thread pool and the web
        # server's handler threads — must not read the same predecessor and
        # fork the chain, which verify_audit() would then report as
        # tampering on a healthy system. The in-process lock serialises
        # threads here; BEGIN IMMEDIATE takes SQLite's write lock up front so
        # a second process/connection blocks until this insert commits.
        with self._audit_lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prev = conn.execute(
                "SELECT entry_hash FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = (prev["entry_hash"] if prev else "") or ""
            # Chain each entry to the previous one's hash: any edit or
            # deletion breaks the chain and is detected by verify_audit().
            payload = f"{prev_hash}|{at}|{actor}|{role}|{action}|{detail}"
            entry_hash = hashlib.sha256(payload.encode()).hexdigest()
            conn.execute(
                "INSERT INTO audit (at, actor, role, action, detail, "
                "prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at, actor, role, action, detail, prev_hash, entry_hash),
            )

    def verify_audit(self) -> tuple[bool, int]:
        """Recompute the audit hash chain. Returns (intact, first_bad_id).
        first_bad_id is 0 when intact."""
        import hashlib
        with self._conn() as conn:
            rows = list(conn.execute(
                "SELECT * FROM audit ORDER BY id ASC"))
        prev_hash = ""
        for row in rows:
            payload = (f"{prev_hash}|{row['at']}|{row['actor']}|{row['role']}"
                       f"|{row['action']}|{row['detail']}")
            expected = hashlib.sha256(payload.encode()).hexdigest()
            if row["prev_hash"] != prev_hash or row["entry_hash"] != expected:
                return False, row["id"]
            prev_hash = row["entry_hash"]
        return True, 0

    def recent_audit(self, limit: int = 200) -> list[dict]:
        with self._conn() as conn:
            return [
                dict(row) for row in conn.execute(
                    "SELECT * FROM audit ORDER BY at DESC LIMIT ?", (limit,)
                )
            ]

    # --------------------------------------------------------- event log

    def record_event(
        self, at: float, event_type: str, message: str,
        severity: str | None = None, actor: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Persist one operational event with its full message (the record
        behind the web UI's Event logs page)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO events (at, type, severity, actor, detail, "
                "message) VALUES (?, ?, ?, ?, ?, ?)",
                (at, event_type, severity, actor, detail, message),
            )

    def recent_events(
        self, limit: int = 300, severities: list[str] | None = None,
    ) -> list[dict]:
        """Recent events, newest first. `severities` optionally filters to a
        set of severity names (e.g. ['error', 'warning'])."""
        query = "SELECT * FROM events"
        params: list = []
        if severities:
            marks = ",".join("?" for _ in severities)
            query += f" WHERE severity IN ({marks})"
            params.extend(severities)
        query += " ORDER BY at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(query, params)]


    # --------------------------------------------------------- users

    def add_user(
        self, username: str, password_hash: str, role: str, at: float,
        scopes: str = "*",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, "
                "created_at, scopes) VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, role, at, scopes),
            )

    def delete_user(self, username: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM users WHERE username = ?", (username,)
            )
            return cur.rowcount

    def set_user_password(self, username: str, password_hash: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (password_hash, username),
            )
            return cur.rowcount

    def get_users(self) -> dict[str, dict]:
        with self._conn() as conn:
            return {
                row["username"]: {
                    "password_hash": row["password_hash"],
                    "role": row["role"],
                    "scopes": (row["scopes"] if "scopes" in row.keys()
                               else "*") or "*",
                }
                for row in conn.execute("SELECT * FROM users")
            }

    # ----------------------------------------------------- api tokens

    def add_api_token(
        self, token_hash: str, name: str, role: str, scopes: str,
        at: float, expires_at: float | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO api_tokens (token_hash, name, role, scopes, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash, name, role, scopes, at, expires_at),
            )

    def get_api_token(self, token_hash: str, now: float) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] is not None and row["expires_at"] < now:
                return None
            conn.execute(
                "UPDATE api_tokens SET last_used = ? WHERE token_hash = ?",
                (now, token_hash))
            return dict(row)

    def list_api_tokens(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT token_hash, name, role, scopes, created_at, "
                "expires_at, last_used FROM api_tokens ORDER BY created_at")]

    def delete_api_token(self, name: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "DELETE FROM api_tokens WHERE name = ?", (name,)).rowcount


def default_runstore(config) -> RunStore:
    return RunStore(Path(config.data_dir).parent / "runstore.db")
