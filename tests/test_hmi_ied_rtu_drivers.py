"""Tests for the HMI/SCADA, substation-IED and RTU drivers added in
hmi_ied_rtu.py.

Every new name is checked for registration and a non-empty description, and
each class is checked to ride the expected existing capture core. collect()
is then exercised for more than three class-based drivers against fake
transports: a local HTTP server (like test_generic_http.py) for the web
drivers, a fake paramiko SFTP filesystem (like test_generic_sftp.py) for the
SCADA project drivers, and a local DNP3 outstation (like test_more_drivers.py)
for the RTU drivers. IEC 61850 relays reuse iec61850_mms unchanged, so their
handshake is not re-tested here; only registration/wiring is asserted.
"""
import base64
import stat as stat_module
import sys
import threading
import types
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
from otitbup.drivers.iec61850 import IEC61850MMSDriver
from otitbup.models import Device

# ---------------------------------------------------------------- names/wiring

_SFTP_NAMES = [
    "aveva_edge", "zenon", "movicon", "factorytalk_se", "vtscada",
    "clearscada", "geo_scada", "siemens_wincc_unified", "reliance_scada",
]
_IEC61850_NAMES = [
    "siemens_siprotec", "abb_relion", "schneider_micom",
    "nr_electric", "nari_relay",
]
_HTTP_NAMES = ["sel_relay", "ge_multilin", "satec_rtu"]
_DNP3_NAMES = ["kingfisher_rtu", "motorola_ace"]

_ALL_NEW_NAMES = _SFTP_NAMES + _IEC61850_NAMES + _HTTP_NAMES + _DNP3_NAMES

_CORE_FOR = [
    (_SFTP_NAMES, GenericSFTPDriver),
    (_IEC61850_NAMES, IEC61850MMSDriver),
    (_HTTP_NAMES, GenericHTTPDriver),
    (_DNP3_NAMES, GenericDNP3Driver),
]


@pytest.mark.parametrize("name", _ALL_NEW_NAMES)
def test_new_driver_is_registered(name):
    assert name in available_drivers()


@pytest.mark.parametrize("name", _ALL_NEW_NAMES)
def test_new_driver_has_nonempty_description(name):
    assert driver_descriptions()[name].strip()


# geo_scada is an alias onto ClearScadaDriver (name == "clearscada"), the
# same alias pattern as abb_rtu520 / emerson_pacsystems.
_ALIAS_NAMES = {"geo_scada": "clearscada"}


@pytest.mark.parametrize("name", _ALL_NEW_NAMES)
def test_class_driver_instantiates_with_matching_name(name):
    assert get_driver(name).name == _ALIAS_NAMES.get(name, name)


def test_class_drivers_reuse_existing_cores():
    for names, core in _CORE_FOR:
        for name in names:
            assert isinstance(get_driver(name), core), (name, core)


# ------------------------------------------------------- web-export collect

_CONFIG = b"[SATEC PM180]\nDeviceName=meter-01\nCT=100\n"


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
        name="ot-01", driver=driver, site="substation", zone="bay",
        address="127.0.0.1",
        options={"urls": [f"http://{{address}}:{port}/config.txt"]},
    )


@pytest.mark.parametrize("driver", _HTTP_NAMES)
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
        get_driver("sel_relay").collect(
            _http_device(web_console, "sel_relay"),
            {"username": "admin", "password": "wrong"},
        )


# --------------------------------------------------------- SFTP project fetch

_TREE = {
    "/projects/zenon/ws/project.zenon": b"ZENONPRJ-v1",
    "/projects/zenon/ws/screens/main.zscr": b"<screen/>",
    "/projects/edge/app.APP": b"IWS-APP",
}


class _Attr:
    def __init__(self, filename, mode, size):
        self.filename, self.st_mode, self.st_size = filename, mode, size


class FakeFile:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSFTP:
    def _dirs(self):
        dirs = set()
        for path in _TREE:
            parts = path.strip("/").split("/")
            for depth in range(1, len(parts)):
                dirs.add("/" + "/".join(parts[:depth]))
        dirs.add("/")
        return dirs

    def stat(self, path):
        path = path.rstrip("/") or "/"
        if path in _TREE:
            return _Attr(path, stat_module.S_IFREG, len(_TREE[path]))
        if path in self._dirs():
            return _Attr(path, stat_module.S_IFDIR, 0)
        raise FileNotFoundError(path)

    def listdir_attr(self, directory):
        directory = directory.rstrip("/") or "/"
        seen, entries = set(), []
        for path, data in _TREE.items():
            parent, _, name = path.rpartition("/")
            parent = parent or "/"
            if parent == directory and name not in seen:
                seen.add(name)
                entries.append(_Attr(name, stat_module.S_IFREG, len(data)))
        for sub in self._dirs():
            parent, _, name = sub.rpartition("/")
            parent = parent or "/"
            if sub != "/" and parent == directory and name not in seen:
                seen.add(name)
                entries.append(_Attr(name, stat_module.S_IFDIR, 0))
        return entries

    def open(self, path, mode="rb"):
        return FakeFile(_TREE[path])


class FakeSSHClient:
    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, address, **kwargs):
        pass

    def open_sftp(self):
        return FakeSFTP()

    def close(self):
        pass


@pytest.fixture
def fake_paramiko(monkeypatch):
    mod = types.ModuleType("paramiko")
    mod.SSHClient = FakeSSHClient
    mod.AutoAddPolicy = object
    monkeypatch.setitem(sys.modules, "paramiko", mod)
    return mod


_SECRETS = {"username": "admin", "password": "scada"}


def test_zenon_fetches_project_dir(fake_paramiko):
    device = Device(
        name="zenon-01", driver="zenon", site="plant", zone="scada",
        address="10.0.0.5", options={"paths": ["/projects/zenon/ws"]},
    )
    artifacts = get_driver("zenon").collect(device, _SECRETS)
    by_name = {a.name: a for a in artifacts}
    assert by_name["projects/zenon/ws/project.zenon"].data == b"ZENONPRJ-v1"
    assert by_name["projects/zenon/ws/screens/main.zscr"].kind == "project"


def test_aveva_edge_fetches_single_file(fake_paramiko):
    device = Device(
        name="edge-01", driver="aveva_edge", site="plant", zone="scada",
        address="10.0.0.6", options={"paths": ["/projects/edge/app.APP"]},
    )
    artifacts = get_driver("aveva_edge").collect(device, _SECRETS)
    assert {a.name for a in artifacts} == {"projects/edge/app.APP"}


def test_scada_driver_requires_paths(fake_paramiko):
    device = Device(
        name="v-01", driver="vtscada", site="plant", zone="scada",
        address="10.0.0.7", options={},
    )
    with pytest.raises(DriverError, match="paths"):
        get_driver("vtscada").collect(device, _SECRETS)


# ------------------------------------------------------------ DNP3 identity

@pytest.mark.parametrize("driver", _DNP3_NAMES)
def test_rtu_reads_dnp3_attributes(driver):
    import socketserver

    from otitbup.drivers.generic_dnp3 import build_frame

    def _attr(variation, text):
        raw = text.encode()
        return (
            bytes([0, variation, 0x00, variation, variation, 1, len(raw)])
            + raw
        )

    app = (
        bytes([0xC0, 0x81, 0x00, 0x00])
        + _attr(250, "Kingfisher CP-30")
        + _attr(252, "Servelec")
    )

    class _Outstation(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(1024)
            self.request.sendall(build_frame(1, 3, b"\xc0" + app))

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Outstation)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        device = Device(
            name="rtu-01", driver=driver, site="field", zone="rtu",
            address="127.0.0.1", options={"port": port},
        )
        artifacts = get_driver(driver).collect(device, None)
    finally:
        server.shutdown()

    import yaml
    attrs = yaml.safe_load(
        {a.name: a for a in artifacts}["device_attributes.yml"].data
    )
    assert attrs["product_name_and_model"] == "Kingfisher CP-30"
    assert attrs["device_manufacturer_name"] == "Servelec"
