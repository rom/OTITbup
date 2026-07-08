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


def test_unknown_routes_404(server):
    base, _, _ = server
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in ("/device/nope/nope/nope", "/device/plant-a/cell-1/plc-01/commit/zzzz"):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            opener.open(base + path, timeout=5)
        assert excinfo.value.code == 404
