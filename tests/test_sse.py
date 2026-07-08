import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer

from otitbup.config import load_config
from otitbup.events import Broadcaster, Event, EventBus
from otitbup.gitstore import GitStore
from otitbup.webui import WebUI, _Handler


def test_broadcaster_fanout_and_drop():
    b = Broadcaster(maxsize=2)
    q1 = b.subscribe()
    q2 = b.subscribe()
    b.publish(Event("t", "one"))
    assert q1.get_nowait().message == "one"
    assert q2.get_nowait().message == "one"
    # A slow subscriber (never drains) overflows and is dropped; a fast one
    # that keeps draining survives.
    for msg in ("a", "b", "c"):
        b.publish(Event("t", msg))
        q2.get_nowait()   # q2 drains each time; q1 backs up
    assert q1 not in b._subscribers
    assert q2 in b._subscribers


def test_eventbus_publishes_to_broadcaster():
    bus = EventBus()
    bus.broadcaster = Broadcaster()
    q = bus.broadcaster.subscribe()
    bus.emit("backup.start", "hi")
    ev = q.get_nowait()
    assert ev.type == "backup.start" and ev.message == "hi"


def _make_server(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(
        "data_dir: ./data\n"
        "sites:\n  - name: s\n    zones:\n      - name: z\n"
        "        devices:\n          - {name: d, driver: cisco_ios}\n"
    )
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    bus = EventBus()
    ui = WebUI(config, store, events=bus)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, ui


def test_sse_stream_delivers_events(tmp_path):
    import http.client
    httpd, ui = _make_server(tmp_path)
    try:
        addr = httpd.server_address
        conn = http.client.HTTPConnection(*addr, timeout=5)
        conn.request("GET", "/events/stream")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "text/event-stream"
        # Read the initial ": connected" comment.
        first = resp.fp.readline()
        assert b"connected" in first
        # Emit an event; it should arrive on the stream.
        time.sleep(0.1)
        ui.events.emit("backup.start", "device d starting", detail="s/z/d")
        # Read lines until we see the data payload.
        deadline = time.time() + 5
        seen = b""
        while time.time() < deadline:
            line = resp.fp.readline()
            seen += line
            if b"device d starting" in line:
                break
        assert b"device d starting" in seen
        conn.close()
    finally:
        httpd.shutdown()


def test_abrupt_disconnect_no_crash(tmp_path):
    import socket
    import time as _t
    httpd, ui = _make_server(tmp_path)
    try:
        addr = httpd.server_address
        # Open a connection, send a partial request line, then reset it
        # (SO_LINGER 0 -> RST) — the stdlib server used to dump a traceback.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(addr)
        s.sendall(b"GET /he")
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                     __import__("struct").pack("ii", 1, 0))
        s.close()
        _t.sleep(0.1)
        # Server must still be serving normal requests afterwards.
        import http.client
        conn = http.client.HTTPConnection(*addr, timeout=5)
        conn.request("GET", "/healthz")
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        httpd.shutdown()
