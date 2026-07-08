"""Web UI authentication: HTTP Basic against PBKDF2-hashed passwords, with
optional multiple users and roles.

Single user (back-compatible):

    webui:
      auth:
        username: admin
        password_hash: pbkdf2_sha256$600000$<salt>$<hash>

Multiple users with roles (viewer < operator < admin):

    webui:
      users:
        - {username: alice, password_hash: pbkdf2_sha256$..., role: admin}
        - {username: bob,   password_hash: pbkdf2_sha256$..., role: viewer}

Generate a hash with `otitbup passwd`. Passwords are never stored; the
hash format is scheme$iterations$salt_hex$hash_hex so iteration counts can
be raised later without breaking existing hashes. Roles gate access to the
audit log (admin) and, in future, any write actions; the UI is read-only
today so viewer/operator/admin all see the same pages except /audit.
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

ROLES = ("viewer", "operator", "admin")


def role_rank(role: str) -> int:
    try:
        return ROLES.index(role)
    except ValueError:
        return 0


def scope_allows(scopes: str, qualified_name: str) -> bool:
    """True if a space-separated scope list (globs like 'plant-a/*',
    'plant-a/cell-1/*', or '*') covers a device's site/zone/name path."""
    import fnmatch
    for scope in (scopes or "*").split():
        if fnmatch.fnmatch(qualified_name, scope):
            return True
        # 'plant-a/*' should also cover 'plant-a/zone/dev'.
        if scope.endswith("/*") and qualified_name.startswith(scope[:-1]):
            return True
    return False


def ldap_authenticate(cfg: dict, username: str, password: str) -> dict | None:
    """Bind to LDAP/AD with the user's credentials. Returns
    {"username", "role"} on success. Role comes from group membership
    (cfg.role_map: {group_dn_or_cn: role}) or cfg.default_role. Requires
    the optional 'ldap3' package (pip install otitbup[ldap])."""
    if not username or not password:
        return None
    try:
        import ldap3
    except ImportError:
        return None
    server = ldap3.Server(cfg["url"], get_info=ldap3.NONE)
    user_dn = cfg["user_dn_template"].format(username=username)
    try:
        conn = ldap3.Connection(server, user=user_dn, password=password,
                                auto_bind=True)
    except Exception:
        return None
    role = cfg.get("default_role", "viewer")
    role_map = cfg.get("role_map") or {}
    if role_map and cfg.get("group_base"):
        try:
            conn.search(cfg["group_base"],
                        f"(member={user_dn})",
                        attributes=["cn"])
            groups = {str(e["cn"]) for e in conn.entries}
            for group, mapped in role_map.items():
                if group in groups:
                    role = mapped
                    break
        except Exception:
            pass
    conn.unbind()
    return {"username": username, "role": role}


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


def _decode_basic(header: str | None) -> tuple[str, str] | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, _, password = decoded.partition(":")
    return username, password


def check_basic_auth(header: str | None, auth_cfg: dict[str, Any]) -> bool:
    """Validate an Authorization header against a single-user auth config."""
    creds = _decode_basic(header)
    if creds is None:
        return False
    username, password = creds
    return hmac.compare_digest(
        username, str(auth_cfg.get("username", ""))
    ) and verify_password(str(auth_cfg.get("password_hash", "")), password)


def build_users(auth_cfg: dict | None, users_cfg: list | None) -> dict[str, dict]:
    """Merge the single-user `auth` block and the multi-user `users` list
    into {username: {password_hash, role}}. A single `auth` user is admin."""
    users: dict[str, dict] = {}
    if auth_cfg and auth_cfg.get("username"):
        users[str(auth_cfg["username"])] = {
            "password_hash": str(auth_cfg.get("password_hash", "")),
            "role": auth_cfg.get("role", "admin"),
        }
    for entry in users_cfg or []:
        if entry.get("username"):
            users[str(entry["username"])] = {
                "password_hash": str(entry.get("password_hash", "")),
                "role": entry.get("role", "viewer"),
            }
    return users


def authenticate(header: str | None, users: dict[str, dict]) -> dict | None:
    """Return {"username", "role"} for valid credentials, else None."""
    creds = _decode_basic(header)
    if creds is None:
        return None
    username, password = creds
    record = users.get(username)
    if record and verify_password(record["password_hash"], password):
        return {"username": username, "role": record.get("role", "viewer"),
                "scopes": record.get("scopes", "*")}
    return None
