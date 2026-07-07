"""Web UI authentication: HTTP Basic against a PBKDF2-hashed password.

Config:

    webui:
      auth:
        username: admin
        password_hash: pbkdf2_sha256$600000$<salt>$<hash>

Generate the hash with `otitbup passwd`. Passwords are never stored; the
hash format is scheme$iterations$salt_hex$hash_hex so iteration counts can
be raised later without breaking existing hashes.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from typing import Any

_SCHEME = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 600_000


def hash_password(password: str, iterations: int = _DEFAULT_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, iterations
    )
    return f"{_SCHEME}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(stored: str, password: str) -> bool:
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split("$")
        if scheme != _SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(),
            bytes.fromhex(salt_hex), int(iterations),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def check_basic_auth(header: str | None, auth_cfg: dict[str, Any]) -> bool:
    """Validate an Authorization header against the webui auth config."""
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, _, password = decoded.partition(":")
    return hmac.compare_digest(
        username, str(auth_cfg.get("username", ""))
    ) and verify_password(str(auth_cfg.get("password_hash", "")), password)
