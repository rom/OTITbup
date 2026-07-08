"""HMI (Ignition), IEC 61850 MMS, ticketing, and discovery enrichment."""
import base64
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device

# ------------------------------------------------------ Ignition HMI

class _IgnitionHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != "Basic " + base64.b64encode(
            b"admin:pw").decode():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Ignition"')
            self.end_headers()
            return
        if self.path.startswith("/system/gwbackup"):
            body = b"PK\x03\x04gwbk-bytes"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def ignition():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _IgnitionHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_ignition_gateway_backup(ignition):
    device = Device(
        name="ign", driver="ignition_gateway", site="s", zone="scada",
        address="127.0.0.1", options={"port": ignition, "scheme": "http"},
    )
    artifacts = get_driver("ignition_gateway").collect(
        device, {"username": "admin", "password": "pw"})
    assert artifacts[0].name == "gateway-backup.gwbk"
    assert artifacts[0].data.startswith(b"PK")


def test_ignition_bad_creds(ignition):
    device = Device(
        name="ign", driver="ignition_gateway", site="s", zone="scada",
        address="127.0.0.1", options={"port": ignition},
    )
    with pytest.raises(DriverError):
        get_driver("ignition_gateway").collect(
            device, {"username": "admin", "password": "wrong"})


def test_hmi_sftp_presets_are_registered():
    for name in ("wincc", "factorytalk_view", "generic_scada"):
        drv = get_driver(name)
        assert drv.name == name


# ------------------------------------------------------ IEC 61850 MMS

class _FakeMMS(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def _tpkt(self, payload):
        return b"\x03\x00" + struct.pack(">H", len(payload) + 4) + payload

    def run(self):
        self.sock.settimeout(5)
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.recv(4096)                      # COTP CR
            # COTP CC: TPKT + COTP header with PDU type 0xD0.
            conn.sendall(self._tpkt(b"\x11\xd0\x00\x00\x00\x01\x00"))
            conn.recv(4096)                      # MMS initiate
            conn.sendall(self._tpkt(b"\x02\xf0\x80"))
            conn.recv(4096)                      # identify request
            # IdentifyResponse with three VisibleStrings (tag 0x1a).
            def vs(s):
                return b"\x1a" + bytes([len(s)]) + s
            body = (b"\x02\xf0\x80"
                    + vs(b"ACME Relays") + vs(b"REL-670") + vs(b"2.1.3"))
            conn.sendall(self._tpkt(body))


@pytest.fixture
def mms():
    m = _FakeMMS()
    m.start()
    yield m


def test_iec61850_identify(mms):
    import yaml
    device = Device(
        name="ied", driver="iec61850_mms", site="sub", zone="bay1",
        address="127.0.0.1", options={"port": mms.port},
    )
    artifacts = get_driver("iec61850_mms").collect(device, None)
    info = yaml.safe_load(
        {a.name: a for a in artifacts}["mms_identity.yml"].data)
    assert info["vendor"] == "ACME Relays"
    assert info["model"] == "REL-670"
    assert info["revision"] == "2.1.3"


def test_iec61850_refused_when_no_cotp():
    device = Device(
        name="ied", driver="iec61850_mms", site="sub", zone="bay1",
        address="127.0.0.1", options={"port": 9, "timeout": 0.3},
    )
    with pytest.raises(DriverError):
        get_driver("iec61850_mms").collect(device, None)


# --------------------------------------------------------- ticketing

class _TicketHandler(BaseHTTPRequestHandler):
    received = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        import json
        _TicketHandler.received.append(json.loads(self.rfile.read(length)))
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def ticket_server():
    _TicketHandler.received = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _TicketHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def test_ticket_opens_on_configured_event(ticket_server):
    from otitbup.tickets import TicketManager
    url = f"http://127.0.0.1:{ticket_server.server_address[1]}/hook"
    mgr = TicketManager({"backend": "generic", "url": url,
                         "on": ["backup.error"]})
    assert mgr.wants("backup.error")
    assert not mgr.wants("auth.login")
    mgr.open_ticket("backup.error", "backup failed: plc-1", "boom")
    import time
    time.sleep(0.2)
    assert _TicketHandler.received
    assert _TicketHandler.received[0]["event"] == "backup.error"


def test_ticket_ignores_unconfigured_event(ticket_server):
    from otitbup.tickets import TicketManager
    url = f"http://127.0.0.1:{ticket_server.server_address[1]}/hook"
    mgr = TicketManager({"backend": "generic", "url": url,
                         "on": ["backup.error"]})
    mgr.open_ticket("auth.login", "login", "x")
    import time
    time.sleep(0.1)
    assert _TicketHandler.received == []


def test_eventbus_creates_ticket(ticket_server, tmp_path):
    from otitbup.events import BACKUP_ERROR, EventBus
    url = f"http://127.0.0.1:{ticket_server.server_address[1]}/hook"
    bus = EventBus({}, tickets={"backend": "generic", "url": url,
                                "on": ["backup.error"]})
    bus.emit(BACKUP_ERROR, "backup failed: dev1", detail="dev1")
    import time
    time.sleep(0.2)
    assert any(r["event"] == "backup.error" for r in _TicketHandler.received)
