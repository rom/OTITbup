"""Read/write web UI: login/logout, session + CSRF, and POST actions."""
import http.client
import textwrap
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from otitbup.auth import hash_password
from otitbup.config import load_config
from otitbup.drivers import register
from otitbup.drivers.base import Artifact, Driver
from otitbup.events import EventBus
from otitbup.gitstore import GitStore
from otitbup.runstore import RunStore
from otitbup.webui import WebUI, _Handler


class FakeDriver(Driver):
    name = "fake"

    def collect(self, device, secrets):
        return [Artifact(name="show_running_config.txt",
                        data=b"hostname x\nvlan 5\n")]


@pytest.fixture
def rw_server(tmp_path):
    register("fake", FakeDriver)
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {name: d1, driver: fake}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    store.write_and_commit(config.all_devices()[0],
                           [Artifact(name="show_running_config.txt",
                                     data=b"hostname x\nvlan 5\n")])
    runstore = RunStore(tmp_path / "r.db")
    runstore.add_user("op", hash_password("op", 1000), "operator", time.time())
    runstore.add_user("adm", hash_password("adm", 1000), "admin", time.time())
    runstore.add_user("vw", hash_password("vw", 1000), "viewer", time.time())
    ui = WebUI(config, store, runstore=runstore, events=EventBus({}, runstore),
               config_path=str(cfg))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address, ui, runstore
    httpd.shutdown()


class Client:
    def __init__(self, address):
        self.host, self.port = address
        self.cookie = None

    def _conn(self):
        return http.client.HTTPConnection(self.host, self.port, timeout=5)

    def login(self, user, pw):
        body = f"username={user}&password={pw}&next=/"
        conn = self._conn()
        conn.request("POST", "/login", body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        setcookie = resp.getheader("Set-Cookie") or ""
        if "otitbup_session=" in setcookie:
            self.cookie = setcookie.split(";", 1)[0]
        return resp.status

    def get(self, path):
        conn = self._conn()
        headers = {"Cookie": self.cookie} if self.cookie else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode(), resp

    def post(self, path, fields):
        from urllib.parse import urlencode
        conn = self._conn()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        conn.request("POST", path, urlencode(fields), headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode(), resp

    def csrf(self):
        # The session token is the CSRF token.
        return self.cookie.split("=", 1)[1] if self.cookie else ""


def test_login_logout_flow_and_events(rw_server):
    address, ui, runstore = rw_server
    c = Client(address)
    assert c.login("op", "op") == 303
    assert c.cookie
    status, body, _ = c.get("/")
    assert status == 200 and "d1" in body

    # LOGIN event recorded.
    assert any(r["action"] == "auth.login" for r in runstore.recent_audit())

    # Logout clears the session and emits LOGOUT.
    status, _, resp = c.post("/logout", {})
    assert status == 303
    assert any(r["action"] == "auth.logout" for r in runstore.recent_audit())


def test_bad_login_shows_error(rw_server):
    address, _, _ = rw_server
    conn = http.client.HTTPConnection(*address, timeout=5)
    conn.request("POST", "/login", "username=op&password=wrong",
                 {"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    assert "invalid" in resp.read().decode().lower()


def test_operator_can_trigger_backup(rw_server):
    address, ui, runstore = rw_server
    c = Client(address)
    c.login("op", "op")
    status, body, _ = c.post("/device/s/z/d1/backup",
                             {"csrf": c.csrf()})
    assert status == 200
    assert "OK" in body
    # A run was recorded and a backup.start event emitted.
    assert runstore.status("s/z/d1").last_attempt is not None
    assert any(r["action"] == "backup.start" for r in runstore.recent_audit())


def test_verify_and_note_and_baseline(rw_server):
    address, ui, runstore = rw_server
    c = Client(address)
    c.login("op", "op")
    csrf = c.csrf()
    assert "OK" in c.post("/device/s/z/d1/verify", {"csrf": csrf})[1]
    assert "OK" in c.post("/device/s/z/d1/note",
                          {"csrf": csrf, "text": "MOC-9"})[1]
    assert "OK" in c.post("/device/s/z/d1/baseline", {"csrf": csrf})[1]
    assert runstore.get_baseline("s/z/d1") is not None


def test_csrf_required_for_session_post(rw_server):
    address, _, _ = rw_server
    c = Client(address)
    c.login("op", "op")
    status, body, _ = c.post("/device/s/z/d1/verify", {})  # no csrf
    assert status == 403
    assert "CSRF" in body


def test_viewer_cannot_write(rw_server):
    address, _, _ = rw_server
    c = Client(address)
    c.login("vw", "vw")
    status, body, _ = c.post("/device/s/z/d1/backup", {"csrf": c.csrf()})
    assert "operator role required" in body


def test_admin_user_management(rw_server):
    address, ui, runstore = rw_server
    c = Client(address)
    c.login("adm", "adm")
    csrf = c.csrf()
    # Create.
    ok = c.post("/users/create",
                {"csrf": csrf, "username": "newbie", "password": "pw",
                 "role": "viewer"})[1]
    assert "created" in ok
    assert "newbie" in runstore.get_users()
    assert any(r["action"] == "user.create" for r in runstore.recent_audit())
    # Change password.
    assert "changed" in c.post(
        "/users/passwd", {"csrf": csrf, "username": "newbie", "password": "x"})[1]
    # Delete.
    assert "deleted" in c.post(
        "/users/delete", {"csrf": csrf, "username": "newbie"})[1]
    assert "newbie" not in runstore.get_users()


def test_non_admin_cannot_manage_users(rw_server):
    address, _, _ = rw_server
    c = Client(address)
    c.login("op", "op")
    status, body, _ = c.post(
        "/users/create",
        {"csrf": c.csrf(), "username": "x", "password": "y", "role": "viewer"})
    assert "admin role required" in body


def test_admin_reload_and_report(rw_server):
    address, ui, runstore = rw_server
    c = Client(address)
    c.login("adm", "adm")
    csrf = c.csrf()
    assert "reloaded" in c.post("/reload", {"csrf": csrf})[1]
    assert "report" in c.post("/report", {"csrf": csrf})[1].lower()
