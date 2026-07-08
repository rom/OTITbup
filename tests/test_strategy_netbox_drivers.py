import json
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otitbup.config import load_config
from otitbup.drivers import available_drivers, get_driver
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.runstore import RunRecord, RunStore


# ------------------------------------------------------ new drivers

def test_new_device_drivers_resolve():
    for name in ("juniper_junos", "arista_eos", "fortinet_fortigate",
                 "paloalto_panos", "mikrotik_routeros", "vyos",
                 "ge_d20", "novatech_orion", "lantronix", "digi_connect",
                 "moxa_mgate", "ewon_flexy", "wonderware", "citect",
                 "kepware", "phoenix_fl_switch"):
        assert get_driver(name).name == name


def test_driver_count_grew():
    assert len(available_drivers()) >= 90


def test_gateway_http_driver_requires_urls():
    from otitbup.drivers.base import DriverError
    from otitbup.models import Device
    dev = Device(name="gw", driver="moxa_mgate", site="s", zone="z",
                 address="10.0.0.1", options={})
    with pytest.raises(DriverError, match="urls"):
        get_driver("moxa_mgate").collect(dev, None)


# --------------------------------------------------------- strategy

@pytest.fixture
def strat_env(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        git:
          push: true
          remote: git@offsite:ot/backups.git
        strategy:
          offsite: true
          offline:
            path: ./offline.tar.gz
            max_age_days: 7
        sites:
          - name: s
            zones:
              - name: net
                devices:
                  - {name: sw-01, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    store.write_and_commit(config.all_devices()[0],
                           [Artifact(name="c.txt", data=b"hostname sw-01\n")])
    runstore = RunStore(tmp_path / "r.db")
    runstore.record_run(RunRecord("s/net/sw-01", time.time() - 10,
                                  time.time() - 9, True, True, "abc", "ok"))
    return config, store, runstore, tmp_path


def test_strategy_321_and_32110(strat_env):
    from otitbup.strategy import evaluate
    config, store, runstore, tmp_path = strat_env
    # No offline archive yet -> 3-2-1-1-0 not met, but 3-2-1 needs 3 copies.
    result = evaluate(config, store, runstore)
    assert result.rule(["one_offsite"])          # remote mirror present
    assert not result.satisfies_32110            # offline missing

    # Create the offline archive -> now 3 copies, offline present.
    (tmp_path / "offline.tar.gz").write_bytes(b"archive")
    result = evaluate(config, store, runstore)
    assert result.copies >= 3
    assert result.satisfies_321
    assert result.satisfies_32110               # verify clean + not failing


def test_strategy_flags_stale_offline(strat_env):
    from otitbup.strategy import evaluate
    import os
    config, store, runstore, tmp_path = strat_env
    archive = tmp_path / "offline.tar.gz"
    archive.write_bytes(b"old")
    old = time.time() - 30 * 86400
    os.utime(archive, (old, old))
    result = evaluate(config, store, runstore)
    assert not result.rule(["one_offline"])     # too old


# ---------------------------------------------------------- NetBox

class _NetBoxHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != "Token tok":
            self.send_response(403)
            self.end_headers()
            return
        payload = {"results": [
            {"id": 1, "name": "core-sw",
             "primary_ip": {"address": "10.0.0.1/24"},
             "platform": {"slug": "ios"},
             "device_role": {"slug": "network"},
             "site": {"slug": "hq"},
             "device_type": {"manufacturer": {"slug": "cisco"}}},
            {"id": 2, "name": "ghost",
             "primary_ip": {"address": "10.0.0.99/24"},
             "platform": {"slug": "junos"},
             "device_role": {"slug": "network"},
             "site": {"slug": "hq"},
             "device_type": {"manufacturer": {"slug": "juniper"}}},
        ], "next": None}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def netbox_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _NetBoxHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_netbox_reconcile(netbox_server, tmp_path):
    from otitbup.netbox import NetBoxClient, reconcile_netbox
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: s
            zones:
              - name: net
                devices:
                  - {name: core, driver: cisco_ios, address: 10.0.0.1}
    """))
    config = load_config(cfg)
    client = NetBoxClient(f"http://127.0.0.1:{netbox_server}", "tok")
    rec = reconcile_netbox(config, client)
    assert "s/net/core" in rec.matched                    # 10.0.0.1 in both
    assert any(d.name == "ghost" for d in rec.not_backed_up)  # 10.0.0.99 gap


def test_netbox_import_proposal(netbox_server):
    from otitbup.netbox import NetBoxClient, import_proposal
    client = NetBoxClient(f"http://127.0.0.1:{netbox_server}", "tok")
    devices = client.devices()
    text = import_proposal(devices, "netbox", "imported")
    assert "driver: cisco_ios" in text        # ios platform -> cisco_ios
    assert "driver: juniper_junos" in text     # junos -> juniper_junos
    from otitbup.config import load_config as lc
    import tempfile
    import pathlib
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "p.yml"
        p.write_text("data_dir: ./data\n" + text)
        assert len(lc(p).all_devices()) == 2


def test_netbox_bad_token(netbox_server):
    from otitbup.netbox import NetBoxClient, NetBoxError
    client = NetBoxClient(f"http://127.0.0.1:{netbox_server}", "wrong")
    with pytest.raises(NetBoxError):
        client.devices()


def test_rt_ticket_backend(tmp_path):
    received = []

    class _RT(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            received.append((self.path, self.headers.get("Authorization"),
                             json.loads(self.rfile.read(n))))
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _RT)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from otitbup.tickets import TicketManager
        mgr = TicketManager({
            "backend": "rt", "url": f"http://127.0.0.1:{srv.server_address[1]}",
            "token": "rttoken", "queue": "OT", "on": ["backup.error"],
        })
        mgr.open_ticket("backup.error", "backup failed", "detail")
        time.sleep(0.2)
    finally:
        srv.shutdown()
    assert received
    path, auth, payload = received[0]
    assert path.endswith("/REST/2.0/ticket")
    assert auth == "token rttoken"
    assert payload["Queue"] == "OT"
