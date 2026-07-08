import json
import textwrap
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer

from otitbup import desired, federation
from otitbup.config import load_config
from otitbup.drivers import register
from otitbup.drivers.base import Artifact, Driver
from otitbup.gitstore import GitStore


class OneFileDriver(Driver):
    name = "onefile"
    _data = b"hostname router-1\nntp server 10.0.0.1\n"

    def collect(self, device, secrets):
        return [Artifact(name="config.txt", data=self._data)]


def _config(tmp_path):
    register("onefile", OneFileDriver)
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        desired:
          dir: ./desired
        sites:
          - name: plant-a
            zones:
              - name: z
                devices:
                  - {name: d1, driver: onefile}
    """))
    return load_config(cfg)


# ------------------------------------------------------ config-as-code

def test_desired_in_sync_and_drift(tmp_path):
    config = _config(tmp_path)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    [device] = config.all_devices()
    store.write_and_commit(device, OneFileDriver().collect(device, None))

    ddir = tmp_path / "desired" / "plant-a" / "z" / "d1"
    ddir.mkdir(parents=True)
    # Identical desired -> in sync.
    (ddir / "config.txt").write_bytes(OneFileDriver._data)
    results = desired.check_all(config, store)
    assert len(results) == 1
    assert results[0].in_sync

    # Change the desired file -> drift with a diff.
    (ddir / "config.txt").write_bytes(b"hostname router-1\nntp server 9.9.9.9\n")
    results = desired.check_all(config, store)
    assert not results[0].in_sync
    art = [a for a in results[0].artifacts if a.status == "drift"][0]
    assert "9.9.9.9" in art.diff and "10.0.0.1" in art.diff


def test_desired_undeclared_skipped(tmp_path):
    config = _config(tmp_path)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    # No desired dir at all -> nothing checked.
    assert desired.check_all(config, store) == []


# ------------------------------------------------------------ federation

class _StatusHandler:
    pass


def test_federation_poll_and_aggregate(tmp_path):
    from http.server import BaseHTTPRequestHandler

    payloads = {
        "/api/status": {"totals": {
            "devices": 10, "covered": 8, "never_backed_up": 2,
            "stale": 1, "failing": 1, "blob_bytes": 100}},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payloads.get(self.path, {})).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        host, port = httpd.server_address
        cfg = {"collectors": [
            {"name": "plant-a", "url": f"http://{host}:{port}"},
            {"name": "dead", "url": "http://127.0.0.1:1"},
        ]}
        healths = federation.poll_all(cfg, timeout=3)
        by_name = {h.name: h for h in healths}
        assert by_name["plant-a"].ok
        assert by_name["plant-a"].totals["devices"] == 10
        assert not by_name["dead"].ok
        agg = federation.aggregate(healths)
        assert agg["reachable"] == 1
        assert agg["unreachable"] == 1
        assert agg["totals"]["devices"] == 10
    finally:
        httpd.shutdown()


def test_federation_no_collectors():
    assert federation.poll_all({}) == []
    assert federation.aggregate([])["totals"]["devices"] == 0
