"""Pluggable secret backends (docs/REQUIREMENTS.md section 8).

Backends: plain YAML file, Fernet-encrypted file, HashiCorp Vault (KV v2
over the HTTP API, stdlib-only) and CyberArk Central Credential Provider
(CCP REST, stdlib-only). Requests to external managers never go through a
proxy — the manager is expected to sit inside the OT/DMZ network.

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
            "(pip install 'otitbup[crypto]')"
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


def _http_opener(
    verify_tls: bool = True,
    ca_cert: str | None = None,
    client_cert: str | None = None,
    client_key: str | None = None,
):
    import ssl
    import urllib.request

    handlers: list = [urllib.request.ProxyHandler({})]
    context = ssl.create_default_context(cafile=ca_cert)
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if client_cert:
        context.load_cert_chain(client_cert, client_key or None)
    handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


class VaultBackend(SecretsBackend):
    """HashiCorp Vault, KV version 2, over the plain HTTP API.

    Config:
        secrets:
          backend: vault
          url: https://vault.plant.local:8200
          mount: secret            # KV v2 mount point
          path_prefix: otitbup     # secret path: <mount>/data/<prefix>/<name>
          # token_file: vault.token   # or set VAULT_TOKEN
          # verify_tls: true / ca_cert: internal-ca.pem

    Each device credential is one KV secret whose keys are the credential
    fields (username, password, enable_secret, ...).
    """

    def __init__(
        self,
        url: str,
        mount: str = "secret",
        path_prefix: str = "otitbup",
        token: str | None = None,
        token_file: str | None = None,
        verify_tls: bool = True,
        ca_cert: str | None = None,
        timeout: float = 10.0,
    ):
        self.url = url.rstrip("/")
        self.mount = mount.strip("/")
        self.path_prefix = path_prefix.strip("/")
        self.token = token
        self.token_file = token_file
        self.timeout = timeout
        self.opener = _http_opener(verify_tls=verify_tls, ca_cert=ca_cert)

    def _resolve_token(self) -> str:
        token = os.environ.get("VAULT_TOKEN") or self.token
        if not token and self.token_file:
            token = Path(self.token_file).read_text().strip()
        if not token:
            raise SecretsError(
                "no Vault token: set VAULT_TOKEN or configure token_file"
            )
        return token

    def get(self, name: str) -> dict[str, Any]:
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        prefix = f"{self.path_prefix}/" if self.path_prefix else ""
        request = urllib.request.Request(
            f"{self.url}/v1/{self.mount}/data/"
            f"{urllib.parse.quote(prefix + name)}",
            headers={"X-Vault-Token": self._resolve_token()},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SecretsError(
                    f"no credentials named {name!r} in Vault "
                    f"({self.mount}/{prefix}{name})"
                ) from exc
            raise SecretsError(
                f"Vault request for {name!r} failed: HTTP {exc.code}"
            ) from exc
        except OSError as exc:
            raise SecretsError(f"Vault unreachable: {exc}") from exc
        data = (body.get("data") or {}).get("data")
        if not isinstance(data, dict):
            raise SecretsError(
                f"Vault secret {name!r} has no KV v2 data (wrong mount "
                "or KV v1?)"
            )
        return data


class CyberArkCCPBackend(SecretsBackend):
    """CyberArk Central Credential Provider (CCP) REST API.

    Config:
        secrets:
          backend: cyberark
          url: https://ccp.plant.local
          app_id: otitbup
          safe: OT-Backup
          # folder: Root
          # object_prefix: otitbup-     # object looked up: <prefix><name>
          # client_cert: appcert.pem / client_key: appkey.pem
          # verify_tls: true / ca_cert: internal-ca.pem

    Returns {"username": <UserName>, "password": <Content>} plus any
    Address/Port properties, lowercased.
    """

    def __init__(
        self,
        url: str,
        app_id: str,
        safe: str | None = None,
        folder: str | None = None,
        object_prefix: str = "",
        verify_tls: bool = True,
        ca_cert: str | None = None,
        client_cert: str | None = None,
        client_key: str | None = None,
        timeout: float = 10.0,
    ):
        self.url = url.rstrip("/")
        self.app_id = app_id
        self.safe = safe
        self.folder = folder
        self.object_prefix = object_prefix
        self.timeout = timeout
        self.opener = _http_opener(
            verify_tls=verify_tls, ca_cert=ca_cert,
            client_cert=client_cert, client_key=client_key,
        )

    def get(self, name: str) -> dict[str, Any]:
        import json
        import urllib.error
        import urllib.parse

        params = {"AppID": self.app_id, "Object": self.object_prefix + name}
        if self.safe:
            params["Safe"] = self.safe
        if self.folder:
            params["Folder"] = self.folder
        url = (
            f"{self.url}/AIMWebService/api/Accounts?"
            + urllib.parse.urlencode(params)
        )
        try:
            with self.opener.open(url, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise SecretsError(
                f"CyberArk CCP lookup for {name!r} failed: HTTP {exc.code}"
            ) from exc
        except OSError as exc:
            raise SecretsError(f"CyberArk CCP unreachable: {exc}") from exc

        if "Content" not in body:
            raise SecretsError(
                f"CyberArk CCP returned no password for {name!r}"
            )
        secret: dict[str, Any] = {"password": body["Content"]}
        if body.get("UserName"):
            secret["username"] = body["UserName"]
        for extra in ("Address", "Port", "Database"):
            if body.get(extra):
                secret[extra.lower()] = body[extra]
        return secret


def load_backend(cfg: dict[str, Any], base_dir: str | Path = ".") -> SecretsBackend:
    backend = cfg.get("backend", "plainfile")
    if backend == "plainfile":
        return PlainFileBackend(Path(base_dir) / cfg.get("path", "secrets.yml"))
    if backend == "encryptedfile":
        return EncryptedFileBackend(
            Path(base_dir) / cfg.get("path", "secrets.yml"),
            key_file=cfg.get("key_file"),
        )
    if backend == "vault":
        if not cfg.get("url"):
            raise SecretsError("vault backend requires 'url'")
        return VaultBackend(
            url=cfg["url"],
            mount=cfg.get("mount", "secret"),
            path_prefix=cfg.get("path_prefix", "otitbup"),
            token=cfg.get("token"),
            token_file=cfg.get("token_file"),
            verify_tls=cfg.get("verify_tls", True),
            ca_cert=cfg.get("ca_cert"),
            timeout=float(cfg.get("timeout", 10)),
        )
    if backend == "cyberark":
        if not cfg.get("url") or not cfg.get("app_id"):
            raise SecretsError("cyberark backend requires 'url' and 'app_id'")
        return CyberArkCCPBackend(
            url=cfg["url"],
            app_id=cfg["app_id"],
            safe=cfg.get("safe"),
            folder=cfg.get("folder"),
            object_prefix=cfg.get("object_prefix", ""),
            verify_tls=cfg.get("verify_tls", True),
            ca_cert=cfg.get("ca_cert"),
            client_cert=cfg.get("client_cert"),
            client_key=cfg.get("client_key"),
            timeout=float(cfg.get("timeout", 10)),
        )
    raise SecretsError(f"unknown secrets backend: {backend!r}")
