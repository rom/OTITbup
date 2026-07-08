import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.drivers.generic_ftp import GenericFTPDriver
from otitbup.drivers.generic_http import GenericHTTPDriver, _apply_scheme
from otitbup.models import Device

# ------------------------------------------------------- scheme handling

def test_apply_scheme():
    assert _apply_scheme("http://h/x", True) == "https://h/x"
    assert _apply_scheme("http://h/x", False) == "http://h/x"
    assert _apply_scheme("{a}/x".replace("{a}", "h"), True) == "https://h/x"
    assert _apply_scheme("h/x", False) == "http://h/x"
    # An explicit https URL is respected in both modes.
    assert _apply_scheme("https://h/x", False) == "https://h/x"


def test_generic_https_registered():
    drv = get_driver("generic_https")
    assert isinstance(drv, GenericHTTPDriver)
    assert drv.force_https is True


class _Console(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"config export ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Console)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address
    httpd.shutdown()


def test_https_option_upgrades_to_tls_and_fails_on_plain_http(http_server):
    host, port = http_server
    # The server speaks plain HTTP; forcing https must therefore fail the
    # TLS handshake — proving the scheme was actually upgraded.
    dev = Device(name="d", driver="generic_http", site="s", zone="z",
                 address=f"{host}:{port}",
                 options={"urls": ["http://{address}/export"], "https": True})
    with pytest.raises(DriverError):
        GenericHTTPDriver().collect(dev, None)


def test_generic_http_plain_still_works(http_server):
    host, port = http_server
    dev = Device(name="d", driver="generic_http", site="s", zone="z",
                 address=f"{host}:{port}",
                 options={"urls": ["http://{address}/export"]})
    arts = GenericHTTPDriver().collect(dev, None)
    assert arts[0].data == b"config export ok"


# ------------------------------------------------------------ FTP driver

def test_generic_ftp_registered():
    assert isinstance(get_driver("generic_ftp"), GenericFTPDriver)


class _FakeFTP:
    """A minimal stand-in for ftplib.FTP exercising the driver's logic."""
    tree = {
        "/project": [("a.txt", {"type": "file"}),
                     ("sub", {"type": "dir"})],
        "/project/sub": [("b.txt", {"type": "file"})],
    }
    blobs = {"/project/a.txt": b"AAA", "/project/sub/b.txt": b"BBB"}

    def __init__(self):
        self.connected = False

    def connect(self, host, port, timeout=15):
        self.connected = True

    def login(self, user, pw):
        assert self.connected

    def set_pasv(self, flag):
        pass

    def mlsd(self, directory):
        if directory not in self.tree:
            import ftplib
            raise ftplib.error_perm("550 no such dir")
        return list(self.tree[directory])

    def size(self, path):
        if path in self.blobs:
            return len(self.blobs[path])
        import ftplib
        raise ftplib.error_perm("550 not a file")

    def retrbinary(self, cmd, callback):
        path = cmd.split(" ", 1)[1]
        callback(self.blobs[path])

    def quit(self):
        pass

    def close(self):
        pass


def test_generic_ftp_walks_and_fetches(monkeypatch):
    import ftplib
    monkeypatch.setattr(ftplib, "FTP", _FakeFTP)
    dev = Device(name="panel", driver="generic_ftp", site="s", zone="z",
                 address="10.0.0.5", options={"paths": ["/project"]})
    arts = GenericFTPDriver().collect(dev, {"username": "u", "password": "p"})
    got = {a.name: a.data for a in arts}
    assert got["project/a.txt"] == b"AAA"
    assert got["project/sub/b.txt"] == b"BBB"


def test_generic_ftp_requires_paths():
    dev = Device(name="d", driver="generic_ftp", site="s", zone="z",
                 address="10.0.0.5", options={})
    with pytest.raises(DriverError):
        GenericFTPDriver().collect(dev, None)
