import base64
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from otitbup.auth import check_basic_auth, hash_password, verify_password
from otitbup.config import load_config
from otitbup.gitstore import GitStore
from otitbup.webui import WebUI, _Handler


def test_hash_verify_roundtrip():
    stored = hash_password("s3cret", iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$")
    assert verify_password(stored, "s3cret")
    assert not verify_password(stored, "wrong")
    assert not verify_password("garbage", "s3cret")
    assert not verify_password("", "s3cret")


def test_hashes_are_salted():
    assert hash_password("x", 1000) != hash_password("x", 1000)


def _basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def test_check_basic_auth():
    cfg = {"username": "admin", "password_hash": hash_password("pw", 1000)}
    assert check_basic_auth(_basic("admin", "pw"), cfg)
    assert not check_basic_auth(_basic("admin", "no"), cfg)
    assert not check_basic_auth(_basic("bob", "pw"), cfg)
    assert not check_basic_auth(None, cfg)
    assert not check_basic_auth("Bearer xyz", cfg)
    assert not check_basic_auth("Basic not-base64!!", cfg)


@pytest.fixture
def auth_server(config_file):
    config = load_config(config_file)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    auth = {"username": "admin", "password_hash": hash_password("pw", 1000)}
    ui = WebUI(config, store, auth=auth)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_no_credentials_is_401_with_challenge(auth_server):
    # No credentials on an HTML page -> the login form (session-based UI).
    with _opener().open(auth_server + "/", timeout=5) as response:
        assert response.status == 200
        assert "Sign in" in response.read().decode()
    # But API/metrics still challenge with 401.
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _opener().open(auth_server + "/api/status", timeout=5)
    assert excinfo.value.code == 401
    assert "Basic" in excinfo.value.headers.get("WWW-Authenticate", "")


def test_wrong_password_is_401(auth_server):
    request = urllib.request.Request(
        auth_server + "/", headers={"Authorization": _basic("admin", "no")}
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _opener().open(request, timeout=5)
    assert excinfo.value.code == 401


def test_valid_credentials_pass(auth_server):
    request = urllib.request.Request(
        auth_server + "/", headers={"Authorization": _basic("admin", "pw")}
    )
    with _opener().open(request, timeout=5) as response:
        assert response.status == 200
        assert "plc-01" in response.read().decode()
