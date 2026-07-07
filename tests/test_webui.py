import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.webui import WebUI, _Handler


@pytest.fixture
def server(config_file):
    config = load_config(config_file)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    device = config.find_devices(["plc-01"])[0]
    store.write_and_commit(device, [Artifact(name="config.txt", data=b"v1")])

    ui = WebUI(config, store)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def _get(url):
    # Bypass any proxy configured in the environment for localhost.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return response.status, response.read().decode()


def test_index_lists_devices_with_status(server):
    status, body = _get(server + "/")
    assert status == 200
    assert "plant-a/cell-1/plc-01" in body
    assert "plant-a/network/sw-01" in body
    # plc-01 has a backup, sw-01 never ran.
    assert "never" in body


def test_device_page_shows_history_and_diff(server):
    status, body = _get(server + "/device/plant-a/cell-1/plc-01")
    assert status == 200
    assert "backup(plant-a/cell-1/plc-01)" in body
    assert "config.txt" in body


def test_unknown_device_404(server):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        opener.open(server + "/device/nope/nope/nope", timeout=5)
    assert excinfo.value.code == 404


def test_html_is_escaped(server):
    status, body = _get(server + "/")
    assert "<script" not in body.lower()
