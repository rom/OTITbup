"""Tests for the additional OT/ICS drivers: the vendor network profiles
added to network_profiles.py and the class-based drivers in more_ot.py.

Registration + descriptions are checked for every new name; real collect()
behaviour is exercised for the web-export class drivers against a local
HTTP server (same approach as test_generic_http.py) and for the DNP3-based
Emerson ROC driver via its inherited generic_dnp3 machinery.
"""
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otitbup.drivers import (
    available_drivers,
    driver_descriptions,
    get_driver,
)
from otitbup.drivers.base import DriverError
from otitbup.drivers.generic_dnp3 import GenericDNP3Driver
from otitbup.drivers.generic_http import GenericHTTPDriver
from otitbup.drivers.generic_sftp import GenericSFTPDriver
from otitbup.models import Device

# Network-profile drivers this batch relies on (auto-registered/-described).
_NETWORK_PROFILE_NAMES = [
    "juniper_junos", "arista_eos", "cisco_nxos", "hpe_comware",
    "aruba_osswitch", "huawei_vrp", "mikrotik_routeros",
    "fortinet_fortigate", "paloalto_panos", "extreme_exos", "dell_os10",
    "checkpoint_gaia", "stormshield", "phoenix_mguard", "nokia_sros",
]

# Class-based drivers registered from more_ot.py.
_CLASS_DRIVER_NAMES = [
    "yokogawa_web", "honeywell_web", "fanuc_cnc",
    "bachmann_m1", "br_automation", "emerson_roc",
]

_ALL_NEW_NAMES = _NETWORK_PROFILE_NAMES + _CLASS_DRIVER_NAMES


@pytest.mark.parametrize("name", _ALL_NEW_NAMES)
def test_new_driver_is_registered(name):
    assert name in available_drivers()


@pytest.mark.parametrize("name", _ALL_NEW_NAMES)
def test_new_driver_has_nonempty_description(name):
    assert driver_descriptions()[name].strip()


@pytest.mark.parametrize("name", _CLASS_DRIVER_NAMES)
def test_class_driver_instantiates_with_matching_name(name):
    driver = get_driver(name)
    assert driver.name == name


def test_class_drivers_reuse_existing_cores():
    # These must ride on the existing capture cores, not reinvent transports.
    assert isinstance(get_driver("yokogawa_web"), GenericHTTPDriver)
    assert isinstance(get_driver("honeywell_web"), GenericHTTPDriver)
    assert isinstance(get_driver("fanuc_cnc"), GenericHTTPDriver)
    assert isinstance(get_driver("bachmann_m1"), GenericSFTPDriver)
    assert isinstance(get_driver("br_automation"), GenericSFTPDriver)
    assert isinstance(get_driver("emerson_roc"), GenericDNP3Driver)


# --------------------------------------------------------- web-export collect

_CONFIG = b"[STARDOM FCN]\nStationName=fcn-01\nCycle=100ms\n"


class _WebConsole(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        expected = "Basic " + base64.b64encode(b"admin:secret").decode()
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="OT"')
            self.end_headers()
            return
        if self.path == "/config.txt":
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


def _http_device(port, driver):
    return Device(
        name="ot-01", driver=driver, site="plant-a", zone="cell",
        address="127.0.0.1",
        options={"urls": [f"http://{{address}}:{port}/config.txt"]},
    )


@pytest.mark.parametrize(
    "driver", ["yokogawa_web", "honeywell_web", "fanuc_cnc"]
)
def test_web_driver_fetches_config(web_console, driver):
    artifacts = get_driver(driver).collect(
        _http_device(web_console, driver),
        {"username": "admin", "password": "secret"},
    )
    assert len(artifacts) == 1
    assert artifacts[0].name == "config.txt"
    assert artifacts[0].data == _CONFIG
    assert artifacts[0].kind == "config"


def test_web_driver_wrong_password_is_driver_error(web_console):
    with pytest.raises(DriverError, match="failed"):
        get_driver("yokogawa_web").collect(
            _http_device(web_console, "yokogawa_web"),
            {"username": "admin", "password": "wrong"},
        )


# ------------------------------------------------------------- SFTP defaults

def test_sftp_driver_default_paths():
    # Bachmann ships a sensible CFC default; B&R has no universal path
    # (the deployed project dir varies by target, so it needs options.paths).
    assert get_driver("bachmann_m1").default_paths == ["/cfc0"]
    assert get_driver("br_automation").default_paths == []


# ------------------------------------------------------------- DNP3 identity

def test_emerson_roc_reads_dnp3_attributes():
    # Reuses generic_dnp3's parser end-to-end against a local DNP3 outstation.
    import socketserver

    from otitbup.drivers.generic_dnp3 import build_frame

    def _attr(variation, text):
        raw = text.encode()
        return bytes([0, variation, 0x00, variation, variation, 1, len(raw)]) + raw

    # App fragment: [app_ctrl, FC=0x81 (solicited response), IIN lo/hi, objects]
    app = (
        bytes([0xC0, 0x81, 0x00, 0x00])
        + _attr(250, "FloBoss 107")
        + _attr(252, "Emerson")
    )

    class _Outstation(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(1024)
            # Transport byte FIR|FIN|seq0 (0xC0) then the app fragment.
            self.request.sendall(build_frame(1, 3, b"\xc0" + app))

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Outstation)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        device = Device(
            name="roc-01", driver="emerson_roc", site="field", zone="rtu",
            address="127.0.0.1", options={"port": port},
        )
        artifacts = get_driver("emerson_roc").collect(device, None)
    finally:
        server.shutdown()

    import yaml
    attrs = yaml.safe_load(
        {a.name: a for a in artifacts}["device_attributes.yml"].data
    )
    assert attrs["product_name_and_model"] == "FloBoss 107"
    assert attrs["device_manufacturer_name"] == "Emerson"
