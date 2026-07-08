import json
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.runstore import RunRecord, RunStore
from otitbup.webui import WebUI, _Handler


@pytest.fixture
def server(config_file, tmp_path):
    config = load_config(config_file)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    device = config.find_devices(["plc-01"])[0]
    store.write_and_commit(device, [Artifact(name="config.txt", data=b"v1")])
    store.write_and_commit(device, [
        Artifact(name="config.txt", data=b"v2"),
        Artifact(name="blocks/OB_1.mc7", data=b"\x00\x01", kind="logic"),
    ])
    runstore = RunStore(tmp_path / "runstore.db")
    now = time.time()
    runstore.record_run(RunRecord(device.qualified_name, now - 60, now - 59,
                                  True, True, "abc", "changed", expected=False))

    ui = WebUI(config, store, runstore=runstore)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", store, device
    httpd.shutdown()


def _get(url):
    # Bypass any proxy configured in the environment for localhost.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return response.status, response.headers, response.read()


def test_index_has_tiles_grouping_and_filter(server):
    base, _, _ = server
    status, _, body = _get(base + "/")
    text = body.decode()
    assert status == 200
    assert "plc-01" in text and "sw-01" in text
    assert "plant-a / cell-1" in text          # zone grouping header
    assert "never" in text                     # sw-01 never backed up
    assert "backed up" in text                 # summary tiles
    assert 'id="filter"' in text or "id='filter'" in text


def test_device_page_shows_artifacts_history_diff(server):
    base, _, _ = server
    status, _, body = _get(base + "/device/plant-a/cell-1/plc-01")
    text = body.decode()
    assert status == 200
    assert "config.txt" in text
    assert "blocks/OB_1.mc7" in text           # artifact table
    assert "/commit/" in text                  # history links
    assert "backup(plant-a/cell-1/plc-01)" in text


def test_commit_diff_view(server):
    base, store, device = server
    commit = store.last_commit_hash(device)
    status, _, body = _get(
        base + f"/device/plant-a/cell-1/plc-01/commit/{commit}"
    )
    text = body.decode()
    assert status == 200
    assert "+v2" in text or "v2" in text
    assert "back to device" in text


def test_artifact_raw_view(server):
    base, _, _ = server
    status, headers, body = _get(
        base + "/device/plant-a/cell-1/plc-01/artifact/config.txt"
    )
    assert status == 200
    assert body == b"v2"
    assert headers["Content-Type"].startswith("text/plain")

    # Binary artifact served as octet-stream.
    _, headers, body = _get(
        base + "/device/plant-a/cell-1/plc-01/artifact/blocks/OB_1.mc7"
    )
    assert body == b"\x00\x01"
    assert headers["Content-Type"] == "application/octet-stream"


def test_artifact_path_traversal_is_404(server):
    base, _, _ = server
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        opener.open(
            base + "/device/plant-a/cell-1/plc-01/artifact/../../../etc/passwd",
            timeout=5,
        )
    assert excinfo.value.code == 404


def test_activity_page_links_devices(server):
    base, store, device = server
    status, _, body = _get(base + "/activity")
    text = body.decode()
    assert status == 200
    assert "plant-a/cell-1/plc-01" in text
    assert store.last_commit_hash(device)[:7] in text


def test_dashboard_page_has_charts(server):
    base, _, _ = server
    status, _, body = _get(base + "/dashboard")
    text = body.decode()
    assert status == 200
    assert "Overview dashboard" in text
    assert "<svg" in text                 # inline SVG charts
    assert "Coverage" in text and "Device status" in text


def test_device_page_has_run_chart(server):
    base, _, device = server
    _, _, body = _get(base + "/device/" + device.qualified_name)
    text = body.decode()
    assert "Health timeline" in text
    assert "runs per day" in text
    assert "<svg" in text                 # per-device SVG bar chart


def test_strategy_page(server):
    base, _, _ = server
    status, _, body = _get(base + "/strategy")
    text = body.decode()
    assert status == 200
    assert "3-2-1" in text
    assert "copies" in text


def test_help_page_and_popovers(server):
    base, _, _ = server
    status, _, body = _get(base + "/help")
    text = body.decode()
    assert status == 200
    assert "Getting started" in text
    assert "Roles" in text
    # Popover help markup exists on the drift page.
    _, _, drift = _get(base + "/drift")
    assert "class='help'" in drift.decode() or 'class="help"' in drift.decode()


def test_drivers_page_marks_in_use(server):
    base, _, _ = server
    status, _, body = _get(base + "/drivers")
    text = body.decode()
    assert status == 200
    assert "generic_dnp3" in text
    assert "siemens_s7" in text


def test_retention_page_shows_policies(server):
    base, _, _ = server
    status, _, body = _get(base + "/retention")
    text = body.decode()
    assert status == 200
    assert "plant-a/cell-1/plc-01" in text
    assert "unlimited" in text            # no limits configured in fixture
    assert "otitbup retention" in text    # points at the prune command
    assert "Offload threshold" in text


def test_device_page_shows_health_timeline(server):
    base, _, device = server
    # The server fixture recorded a run, so the timeline renders.
    _, _, body = _get(base + "/device/" + device.qualified_name)
    assert "Health timeline" in body.decode()


def test_device_page_shows_retention_policy(server):
    base, _, _ = server
    _, _, body = _get(base + "/device/plant-a/cell-1/plc-01")
    assert "retention" in body.decode()


def test_health_page(server):
    base, _, _ = server
    status, _, body = _get(base + "/health")
    text = body.decode()
    assert status == 200
    assert "Backup health" in text
    assert "covered" in text


def test_metrics_endpoint(server):
    base, _, _ = server
    status, headers, body = _get(base + "/metrics")
    assert status == 200
    assert headers["Content-Type"].startswith("text/plain")
    assert b"otitbup_devices_total" in body


def test_healthz_bypasses_auth(config_file, tmp_path):
    # Even with auth on, /healthz must answer for liveness probes.
    from otitbup.auth import hash_password
    config = load_config(config_file)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    ui = WebUI(config, store,
               auth={"username": "a", "password_hash": hash_password("p", 1000)})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, _, body = _get(f"http://127.0.0.1:{httpd.server_address[1]}/healthz")
        assert status == 200 and b"ok" in body
    finally:
        httpd.shutdown()


def test_api_status_and_device(server):
    base, _, device = server
    status, headers, body = _get(base + "/api/status")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    data = json.loads(body)
    assert data["totals"]["devices"] >= 1

    _, _, body = _get(base + "/api/device/" + device.qualified_name)
    dev = json.loads(body)
    assert dev["device"] == device.qualified_name
    assert dev["last_ok"] is True


def test_policy_page(server):
    base, store, device = server
    # Insecure config so the policy page has a finding.
    store.write_and_commit(device, [Artifact(
        name="show_running_config.txt", data=b"ip http server\n")])
    status, _, body = _get(base + "/policy")
    assert status == 200
    assert "policy" in body.decode().lower()


def test_search_page(server):
    base, store, device = server
    store.write_and_commit(device, [Artifact(
        name="show_running_config.txt", data=b"hostname plc-01\nvlan 77\n")])
    status, _, body = _get(base + "/search?q=vlan+77")
    text = body.decode()
    assert status == 200
    assert "vlan 77" in text
    assert "match" in text.lower()


def test_drift_page(server):
    base, _, _ = server
    status, _, body = _get(base + "/drift")
    assert status == 200
    assert "drift" in body.decode().lower()


def test_compare_view(server):
    base, store, device = server
    commits = store.device_commits(device)
    assert len(commits) >= 2
    base_c, head_c = commits[1][0], commits[0][0]
    status, _, body = _get(
        base + f"/device/{device.qualified_name}/compare?base={base_c}&head={head_c}"
    )
    assert status == 200
    assert "v2" in body.decode()


def test_audit_log_records_and_shows(server):
    base, _, _ = server
    _get(base + "/policy")            # generate an auditable view
    status, _, body = _get(base + "/audit")
    text = body.decode()
    assert status == 200
    # The audit table row (not just the nav) carries the view + path.
    assert "view" in text and "/policy" in text


def test_multiuser_auth_and_role_gate(config_file, tmp_path):
    from otitbup.auth import hash_password
    from otitbup.runstore import RunStore
    config = load_config(config_file)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    users = {
        "admin": {"password_hash": hash_password("ap", 1000), "role": "admin"},
        "viewer": {"password_hash": hash_password("vp", 1000), "role": "viewer"},
    }
    ui = WebUI(config, store, users=users, runstore=RunStore(tmp_path / "r.db"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _req(user, pw, path):
        req = urllib.request.Request(
            base + path,
            headers={"Authorization": "Basic " + __import__("base64")
                     .b64encode(f"{user}:{pw}".encode()).decode()},
        )
        return opener.open(req, timeout=5)

    try:
        # viewer can see /health but not /audit (admin only).
        assert _req("viewer", "vp", "/health").status == 200
        with pytest.raises(urllib.error.HTTPError) as exc:
            _req("viewer", "vp", "/audit")
        assert exc.value.code == 403
        assert _req("admin", "ap", "/audit").status == 200
        # bad password -> 401
        with pytest.raises(urllib.error.HTTPError) as exc:
            _req("viewer", "wrong", "/health")
        assert exc.value.code == 401
    finally:
        httpd.shutdown()


def test_unknown_routes_404(server):
    base, _, _ = server
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in ("/device/nope/nope/nope", "/device/plant-a/cell-1/plc-01/commit/zzzz"):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            opener.open(base + path, timeout=5)
        assert excinfo.value.code == 404


def test_favicon_and_brand(tmp_path):
    import http.client
    import threading
    from functools import partial
    from http.server import ThreadingHTTPServer

    from otitbup.config import load_config
    from otitbup.gitstore import GitStore
    from otitbup.webui import WebUI, _Handler

    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(
        "data_dir: ./data\nsites: [{name: s, zones: [{name: z, "
        "devices: [{name: d, driver: cisco_ios}]}]}]\n")
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    ui = WebUI(config, store)
    # Brand + favicon link are in every page.
    page = ui.index().decode()
    assert "class='brand'" in page and "/favicon.svg" in page
    assert "<span class='wm'>" in page

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        conn.request("GET", "/favicon.svg")     # public, no auth
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "image/svg+xml"
        assert b"<svg" in body and b"linearGradient" in body
    finally:
        httpd.shutdown()


def test_device_page_on_empty_repo(tmp_path):
    # A fresh appliance: repo initialised but no backups committed yet.
    # Viewing a device page must not crash (git log on an empty repo).
    from otitbup.config import load_config
    from otitbup.gitstore import GitStore
    from otitbup.webui import WebUI

    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(
        "data_dir: ./data\nsites: [{name: s, zones: [{name: z, "
        "devices: [{name: sw1, driver: cisco_ios}]}]}]\n")
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()                       # no commits
    device = config.all_devices()[0]
    assert store.last_diff(device) == ""
    page = WebUI(config, store).device(
        "s/z/sw1", ctx={"role": "admin", "csrf": "t"})
    assert b"sw1" in page
