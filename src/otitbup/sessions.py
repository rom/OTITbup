"""In-memory web session store.

The appliance runs a single web process, so sessions live in memory: a
random opaque token maps to an authenticated identity with a TTL. Cookie
sessions give real login/logout semantics (which HTTP Basic auth cannot),
and the session token doubles as a CSRF token for POST actions
(double-submit: the value in the cookie must equal the value in the form).
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class Session:
    token: str
    username: str
    role: str
    created_at: float
    expires_at: float
    scopes: str = "*"


class SessionStore:
    def __init__(self, ttl_seconds: float = 8 * 3600):
        self.ttl = ttl_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str, role: str, scopes: str = "*") -> Session:
        # Opportunistically purge expired sessions on each new login, so the
        # store doesn't grow unbounded with the sessions of users who never
        # return to trigger get()'s lazy delete.
        self.sweep()
        now = time.time()
        token = secrets.token_urlsafe(32)
        session = Session(
            token=token, username=username, role=role, scopes=scopes,
            created_at=now, expires_at=now + self.ttl,
        )
        with self._lock:
            self._sessions[token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expires_at < now:
                del self._sessions[token]
                return None
            return session

    def destroy(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            return self._sessions.pop(token, None)

    def sweep(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                t for t, s in self._sessions.items() if s.expires_at < now
            ]
            for token in expired:
                del self._sessions[token]
