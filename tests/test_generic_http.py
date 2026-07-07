"""Tests the HTTP config-export driver (Moxa NPort et al.) against a real
local HTTP server requiring Basic auth."""
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otitbup.drivers import get_driver
from otitbup.drivers.base import DriverError
from otitbup.models import Device

_CONFIG = b"[NPort 5150]\nDeviceName=nport-01\nBaudrate=115200\n"


class _WebConsole(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        expected = "Basic " + base64.b64encode(b"admin:moxa").decode()
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="NPort"')
            self.end_headers()
            return
        if self.path == "/ConfigExport.txt":
            self.send_response(200)
            self.send_header("Content-Length", str(len(_CONFIG)))
            self.end_headers()
            self.wfile.write(_CONFIG)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def web_console():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WebConsole)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _device(port, driver="moxa_nport", **extra):
    return Device(
        name="nport-01", driver=driver, site="plant-a", zone="serial",
        address="127.0.0.1",
        options={
            "urls": [f"http://{{address}}:{port}/ConfigExport.txt"],
            **extra,
        },
    )


def test_fetches_config_with_basic_auth(web_console):
    artifacts = get_driver("moxa_nport").collect(
        _device(web_console), {"username": "admin", "password": "moxa"}
    )
    assert len(artifacts) == 1
    assert artifacts[0].name == "ConfigExport.txt"
    assert artifacts[0].data == _CONFIG
    assert artifacts[0].kind == "config"


def test_address_placeholder_is_substituted(web_console):
    artifacts = get_driver("generic_http").collect(
        _device(web_console, driver="generic_http"),
        {"username": "admin", "password": "moxa"},
    )
    assert artifacts[0].meta["url"].startswith("http://127.0.0.1:")


def test_wrong_password_is_driver_error(web_console):
    with pytest.raises(DriverError, match="failed"):
        get_driver("moxa_nport").collect(
            _device(web_console), {"username": "admin", "password": "wrong"}
        )


def test_missing_urls_is_clear_error():
    device = Device(
        name="n", driver="moxa_nport", site="a", zone="z",
        address="127.0.0.1", options={},
    )
    with pytest.raises(DriverError, match="urls"):
        get_driver("moxa_nport").collect(device, None)
