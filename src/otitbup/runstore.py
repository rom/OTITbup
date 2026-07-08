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
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device      TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL NOT NULL,
    ok          INTEGER NOT NULL,
    changed     INTEGER NOT NULL,
    commit_hash TEXT,
    message     TEXT,
    expected    INTEGER            -- 1 expected, 0 unexpected, NULL n/a
);
CREATE INDEX IF NOT EXISTS ix_runs_device ON runs(device, started_at);

CREATE TABLE IF NOT EXISTS rehearsals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device     TEXT NOT NULL,
    at         REAL NOT NULL,
    commit_hash TEXT,
    result     TEXT NOT NULL,       -- pass | fail
    tested_by  TEXT,
    notes      TEXT
);
CREATE INDEX IF NOT EXISTS ix_rehearsals_device ON rehearsals(device, at);

CREATE TABLE IF NOT EXISTS maintenance (
    device    TEXT PRIMARY KEY,     -- device, or "site/*" / "site/zone/*"
    until     REAL,                 -- expiry epoch, or NULL = indefinite
    reason    TEXT,
    set_by    TEXT,
    set_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS baselines (
    device      TEXT PRIMARY KEY,   -- approved "golden" config commit
    commit_hash TEXT NOT NULL,
    set_at      REAL NOT NULL,
    set_by      TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     REAL NOT NULL,
    actor  TEXT,
    role   TEXT,
    action TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit(at);
"""


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
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

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
                "changed, commit_hash, message, expected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.device, run.started_at, run.finished_at,
                    int(run.ok), int(run.changed), run.commit_hash,
                    run.message,
                    None if run.expected is None else int(run.expected),
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

    def all_status(self, devices: list[str]) -> dict[str, DeviceStatus]:
        return {name: self.status(name) for name in devices}

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
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit (at, actor, role, action, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (at, actor, role, action, detail),
            )

    def recent_audit(self, limit: int = 200) -> list[dict]:
        with self._conn() as conn:
            return [
                dict(row) for row in conn.execute(
                    "SELECT * FROM audit ORDER BY at DESC LIMIT ?", (limit,)
                )
            ]


def default_runstore(config) -> RunStore:
    return RunStore(Path(config.data_dir).parent / "runstore.db")
