"""On-disk per-user preferences (e.g. the chosen web-UI theme).

A tiny JSON store — `{username: {key: value}}` — kept next to the data
directory. Unlike the run store's user table (which only holds
UI-created accounts), this covers *every* signed-in identity, including
config-declared users and SSO/LDAP logins, so a user's theme follows their
account across browsers and devices. Writes are atomic and lock-guarded;
a corrupt or missing file reads as empty.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class UserPrefs:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, username: str) -> dict:
        return self._load().get(username, {})

    def get_value(self, username: str, key: str, default=None):
        return self.get(username).get(key, default)

    def set_value(self, username: str, key: str, value: Any) -> None:
        with self._lock:
            data = self._load()
            user = data.setdefault(username, {})
            if value is None or value == "":
                user.pop(key, None)
            else:
                user[key] = value
            if not user:
                data.pop(username, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
            os.replace(tmp, self.path)
