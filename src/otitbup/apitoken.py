"""Scoped API tokens for the write API.

A token is a random opaque secret shown once at creation; only its sha256
hash is stored (like a password). Each token carries a role
(viewer/operator/admin) and scopes (site/zone globs), so automation can be
granted least-privilege access without sharing a user's full credentials.
"""
from __future__ import annotations

import hashlib
import secrets
import time


def generate() -> tuple[str, str]:
    """Return (plaintext_token, sha256_hash). Show the plaintext once."""
    token = "otb_" + secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(runstore, name: str, role: str, scopes: str,
           days: int | None = None) -> str:
    plaintext, digest = generate()
    expires = time.time() + days * 86400 if days else None
    runstore.add_api_token(digest, name, role, scopes, time.time(), expires)
    return plaintext


def authenticate(runstore, token: str | None) -> dict | None:
    """Resolve a Bearer/API token to {name, role, scopes} or None."""
    if not token:
        return None
    rec = runstore.get_api_token(hash_token(token), time.time())
    if rec is None:
        return None
    return {"username": f"token:{rec['name']}", "role": rec["role"],
            "scopes": rec.get("scopes") or "*"}
