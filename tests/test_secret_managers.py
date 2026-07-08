"""Vault (KV v2) and CyberArk CCP backends against local HTTP servers."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from otitbup.secrets import SecretsError, load_backend

_VAULT_SECRETS = {
    "otitbup/plc-01": {"username": "backup", "password": "vaulted"},
}


class _VaultHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get("X-Vault-Token") != "test-token":
            self._json(403, {"errors": ["permission denied"]})
            return
        prefix = "/v1/secret/data/"
        if not self.path.startswith(prefix):
            self._json(404, {"errors": []})
            return
        name = self.path[len(prefix):]
        if name not in _VAULT_SECRETS:
            self._json(404, {"errors": []})
            return
        self._json(200, {"data": {"data": _VAULT_SECRETS[name]}})

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _CCPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/AIMWebService/api/Accounts":
            self.send_response(404)
            self.end_headers()
            return
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.last_query = query
        if query.get("Object") != "otitbup-plc-01":
            body = json.dumps({"ErrorCode": "APPAP004E"}).encode()
            self.send_response(404)
        else:
            body = json.dumps({
                "Content": "arked",
                "UserName": "backup",
                "Address": "10.0.0.5",
            }).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def vault_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _VaultHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def ccp_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CCPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def test_vault_get(vault_server, monkeypatch):
    monkeypatch.setenv("VAULT_TOKEN", "test-token")
    backend = load_backend({"backend": "vault", "url": vault_server})
    secret = backend.get("plc-01")
    assert secret == {"username": "backup", "password": "vaulted"}


def test_vault_token_file(vault_server, tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    token_file = tmp_path / "token"
    token_file.write_text("test-token\n")
    backend = load_backend({
        "backend": "vault", "url": vault_server,
        "token_file": str(token_file),
    })
    assert backend.get("plc-01")["password"] == "vaulted"


def test_vault_missing_secret_and_bad_token(vault_server, monkeypatch):
    monkeypatch.setenv("VAULT_TOKEN", "test-token")
    backend = load_backend({"backend": "vault", "url": vault_server})
    with pytest.raises(SecretsError, match="no credentials"):
        backend.get("nope")

    monkeypatch.setenv("VAULT_TOKEN", "wrong")
    with pytest.raises(SecretsError, match="HTTP 403"):
        backend.get("plc-01")


def test_vault_no_token_is_clear_error(monkeypatch):
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    backend = load_backend({"backend": "vault", "url": "http://127.0.0.1:1"})
    with pytest.raises(SecretsError, match="no Vault token"):
        backend.get("plc-01")


def test_cyberark_get_maps_fields(ccp_server):
    url = f"http://127.0.0.1:{ccp_server.server_address[1]}"
    backend = load_backend({
        "backend": "cyberark", "url": url, "app_id": "otitbup",
        "safe": "OT-Backup", "object_prefix": "otitbup-",
    })
    secret = backend.get("plc-01")
    assert secret == {
        "password": "arked", "username": "backup", "address": "10.0.0.5",
    }
    assert ccp_server.last_query["AppID"] == "otitbup"
    assert ccp_server.last_query["Safe"] == "OT-Backup"
    assert ccp_server.last_query["Object"] == "otitbup-plc-01"


def test_cyberark_unknown_object(ccp_server):
    url = f"http://127.0.0.1:{ccp_server.server_address[1]}"
    backend = load_backend({
        "backend": "cyberark", "url": url, "app_id": "otitbup",
        "object_prefix": "otitbup-",
    })
    with pytest.raises(SecretsError, match="HTTP 404"):
        backend.get("unknown")


def test_backend_config_validation():
    with pytest.raises(SecretsError, match="requires 'url'"):
        load_backend({"backend": "vault"})
    with pytest.raises(SecretsError, match="app_id"):
        load_backend({"backend": "cyberark", "url": "https://x"})
