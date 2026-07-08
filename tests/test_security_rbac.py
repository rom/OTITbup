import textwrap
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from otitbup import apitoken
from otitbup.auth import scope_allows
from otitbup.blobstore import BlobStore
from otitbup.config import load_config
from otitbup.drivers import register
from otitbup.drivers.base import Artifact, Driver
from otitbup.gitstore import GitStore
from otitbup.runstore import RunStore
from otitbup.webui import WebUI, _Handler

pytest.importorskip("cryptography")
from cryptography.fernet import Fernet  # noqa: E402

# ------------------------------------------------ blob encryption at rest

def test_blob_store_encrypted_at_rest(tmp_path):
    key = Fernet.generate_key()
    store = BlobStore(tmp_path / "blobs", key=key)
    data = b"SECRET PLC PROGRAM" * 100
    sha = store.put(data)
    # Content-addressed by plaintext hash (dedup + manifest unchanged).
    import hashlib
    assert sha == hashlib.sha256(data).hexdigest()
    # On disk it is ciphertext, not the plaintext.
    on_disk = (tmp_path / "blobs" / sha[:2] / sha).read_bytes()
    assert b"SECRET PLC PROGRAM" not in on_disk
    # get() transparently decrypts.
    assert store.get(sha) == data
    # Wrong key can't read it.
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        BlobStore(tmp_path / "blobs", key=Fernet.generate_key()).get(sha)


# -------------------------------------------------------- scope matching

def test_scope_allows():
    assert scope_allows("*", "plant-a/cell-1/plc-01")
    assert scope_allows("plant-a/*", "plant-a/cell-1/plc-01")
    assert not scope_allows("plant-a/*", "plant-b/cell-1/plc-01")
    assert scope_allows("plant-a/cell-1/*", "plant-a/cell-1/plc-01")
    assert not scope_allows("plant-a/cell-1/*", "plant-a/cell-2/plc-01")
    assert scope_allows("plant-a/* plant-b/*", "plant-b/z/d")


# --------------------------------------------- write API + scoped tokens

class FakeDriver(Driver):
    name = "fake"

    def collect(self, device, secrets):
        return [Artifact(name="c.txt", data=b"cfg")]


@pytest.fixture
def api_server(tmp_path):
    register("fake", FakeDriver)
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: plant-a
            zones:
              - name: z
                devices:
                  - {name: d1, driver: fake}
          - name: plant-b
            zones:
              - name: z
                devices:
                  - {name: d2, driver: fake}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    runstore = RunStore(tmp_path / "r.db")
    # A viewer must exist so auth is enforced (any users -> auth required).
    from otitbup.auth import hash_password
    runstore.add_user("admin", hash_password("pw", 1000), "admin", time.time())
    ui = WebUI(config, store, runstore=runstore)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address, runstore
    httpd.shutdown()


def _post(address, path, token=None):
    import http.client
    conn = http.client.HTTPConnection(*address, timeout=5)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", path, "", headers)
    resp = conn.getresponse()
    return resp.status, resp.read().decode()


def test_write_api_requires_token(api_server):
    address, _ = api_server
    status, _ = _post(address, "/api/device/plant-a/z/d1/backup")
    assert status == 401


def test_write_api_backup_with_token(api_server):
    address, runstore = api_server
    tok = apitoken.create(runstore, "ci", "operator", "*")
    status, body = _post(address, "/api/device/plant-a/z/d1/backup", tok)
    assert status == 200
    assert '"ok": true' in body
    assert runstore.status("plant-a/z/d1").last_attempt is not None


def test_write_api_enforces_scope(api_server):
    address, runstore = api_server
    tok = apitoken.create(runstore, "scoped", "operator", "plant-a/*")
    # In scope -> ok.
    status, _ = _post(address, "/api/device/plant-a/z/d1/backup", tok)
    assert status == 200
    # Out of scope -> 403.
    status, body = _post(address, "/api/device/plant-b/z/d2/backup", tok)
    assert status == 403
    assert "out of scope" in body


def test_write_api_enforces_role(api_server):
    address, runstore = api_server
    tok = apitoken.create(runstore, "ro", "viewer", "*")
    status, body = _post(address, "/api/device/plant-a/z/d1/backup", tok)
    assert status == 403
    assert "operator role required" in body
