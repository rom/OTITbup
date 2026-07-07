"""Pluggable secret backends (REQUIREMENTS.md section 8).

Ships with a plain YAML file backend and a Fernet-encrypted file backend.
External managers (HashiCorp Vault, CyberArk) can be added later by
implementing SecretsBackend and registering the backend name here.

Secrets file format (both backends):

    plc-01:
      username: backup
      password: s3cret
    core-switch:
      username: admin
      password: other
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml


class SecretsError(Exception):
    pass


class SecretsBackend(ABC):
    @abstractmethod
    def get(self, name: str) -> dict[str, Any]:
        """Return the credential mapping stored under `name`."""


class PlainFileBackend(SecretsBackend):
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise SecretsError(f"secrets file not found: {self.path}")
        with open(self.path) as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise SecretsError(f"{self.path}: top level must be a mapping")
        return data

    def get(self, name: str) -> dict[str, Any]:
        data = self._load()
        if name not in data:
            raise SecretsError(f"no credentials named {name!r} in {self.path}")
        return data[name]


class EncryptedFileBackend(PlainFileBackend):
    """Fernet-encrypted YAML file. The key comes from the OTITBUP_KEY
    environment variable or a key file (option `key_file`)."""

    def __init__(self, path: str | Path, key_file: str | None = None):
        super().__init__(path)
        self.key_file = key_file

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise SecretsError(f"secrets file not found: {self.path}")
        fernet = _fernet_cls()(_resolve_key(self.key_file))
        plaintext = fernet.decrypt(self.path.read_bytes())
        data = yaml.safe_load(plaintext) or {}
        if not isinstance(data, dict):
            raise SecretsError(f"{self.path}: top level must be a mapping")
        return data


def _fernet_cls():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise SecretsError(
            "this operation requires the 'cryptography' package "
            "(pip install otitbup[crypto])"
        ) from exc
    return Fernet


def generate_key(out: str | Path | None = None) -> str:
    """Generate a Fernet key. If `out` is given, write it there with
    owner-only permissions; otherwise the caller prints it."""
    key = _fernet_cls().generate_key().decode()
    if out:
        path = Path(out)
        path.touch(mode=0o600)
        path.write_text(key + "\n")
        path.chmod(0o600)
    return key


def _resolve_key(key_file: str | None) -> bytes:
    key = os.environ.get("OTITBUP_KEY")
    if not key and key_file:
        key = Path(key_file).read_text().strip()
    if not key:
        raise SecretsError(
            "no encryption key: set OTITBUP_KEY or pass --key-file"
        )
    return key.encode()


def encrypt_file(
    src: str | Path, dst: str | Path, key_file: str | None = None
) -> None:
    """Encrypt a plaintext secrets YAML file. Validates the YAML first so
    a malformed file is caught before it is locked away."""
    src, dst = Path(src), Path(dst)
    plaintext = src.read_bytes()
    data = yaml.safe_load(plaintext)
    if not isinstance(data, dict):
        raise SecretsError(f"{src}: top level must be a mapping")
    fernet = _fernet_cls()(_resolve_key(key_file))
    token = fernet.encrypt(plaintext)
    dst.touch(mode=0o600)
    dst.write_bytes(token)
    dst.chmod(0o600)


def decrypt_file(src: str | Path, key_file: str | None = None) -> str:
    """Decrypt an encrypted secrets file and return the plaintext YAML."""
    ciphertext = Path(src).read_bytes()
    return _fernet_cls()(_resolve_key(key_file)).decrypt(ciphertext).decode()


def load_backend(cfg: dict[str, Any], base_dir: str | Path = ".") -> SecretsBackend:
    backend = cfg.get("backend", "plainfile")
    path = Path(base_dir) / cfg.get("path", "secrets.yml")
    if backend == "plainfile":
        return PlainFileBackend(path)
    if backend == "encryptedfile":
        return EncryptedFileBackend(path, key_file=cfg.get("key_file"))
    raise SecretsError(f"unknown secrets backend: {backend!r}")
