import base64
import socket
import textwrap
import time

import pytest

from otitbup.alerts import AlertManager
from otitbup.auth import (authenticate, build_users, hash_password,
                          role_rank)
from otitbup.config import load_config
from otitbup.reconcile import reconcile
from otitbup.scaffold import init_project


# ------------------------------------------------------- reconciliation

@pytest.fixture
def listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    yield sock.getsockname()[1]
    sock.close()


def test_reconcile_reports_unmanaged_and_unreachable(tmp_path, listener):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent(f"""
        data_dir: ./data
        sites:
          - name: s
            zones:
              - name: net
                devices:
                  - {{name: known, driver: generic_ssh, address: 127.0.0.1,
                     options: {{device_type: cisco_ios}}}}
                  - {{name: gone, driver: generic_ssh, address: 10.255.255.254}}
    """))
    config = load_config(cfg)
    # Scan loopback/32 with the port our listener holds mapped to a driver.
    result = reconcile(
        config, ["127.0.0.1/32"],
        ports={listener: "generic_ssh"}, timeout=0.5, delay=0,
    )
    # 127.0.0.1 is in inventory and reachable -> managed_reachable.
    assert "s/net/known" in result.managed_reachable
    # 10.255.255.254 is in inventory but not scanned -> unreachable.
    assert "s/net/gone" in result.unreachable
    # Nothing unmanaged (the only responder is inventoried).
    assert result.unmanaged == []


# --------------------------------------------------------------- auth

def test_build_users_merges_auth_and_users():
    users = build_users(
        {"username": "admin", "password_hash": hash_password("a", 1000)},
        [{"username": "bob", "password_hash": hash_password("b", 1000),
          "role": "viewer"}],
    )
    assert users["admin"]["role"] == "admin"     # single auth -> admin
    assert users["bob"]["role"] == "viewer"


def test_authenticate_returns_identity():
    users = build_users(
        None,
        [{"username": "op", "password_hash": hash_password("pw", 1000),
          "role": "operator"}],
    )
    header = "Basic " + base64.b64encode(b"op:pw").decode()
    ident = authenticate(header, users)
    assert ident == {"username": "op", "role": "operator", "scopes": "*"}
    assert authenticate("Basic " + base64.b64encode(b"op:no").decode(),
                        users) is None


def test_role_rank_ordering():
    assert role_rank("viewer") < role_rank("operator") < role_rank("admin")


# ------------------------------------------------------ alert throttle

def test_alert_rate_limiting(tmp_path):
    sent = []
    mgr = AlertManager(
        {"min_interval": 100}, state_path=tmp_path / "alert-state.json"
    )
    mgr._webhook = lambda *a: None  # no real delivery
    # Patch notify's delivery by counting via _suppressed directly.
    assert mgr._suppressed("subject-A") is False   # first: allowed
    assert mgr._suppressed("subject-A") is True    # repeat: suppressed
    assert mgr._suppressed("subject-B") is False   # different subject: allowed

    # State persists: a fresh manager still suppresses within the window.
    mgr2 = AlertManager(
        {"min_interval": 100}, state_path=tmp_path / "alert-state.json"
    )
    assert mgr2._suppressed("subject-A") is True


def test_alert_no_throttle_when_disabled(tmp_path):
    mgr = AlertManager({"min_interval": 0})
    assert mgr._suppressed("x") is False
    assert mgr._suppressed("x") is False


# ------------------------------------------------------------- scaffold

def test_init_project_writes_files(tmp_path):
    written = init_project(tmp_path)
    names = {p.name for p in written}
    assert names == {"otitbup.yml", "secrets.yml"}
    # Config validates.
    load_config(tmp_path / "otitbup.yml")
    assert (tmp_path / "secrets.yml").stat().st_mode & 0o777 == 0o600


def test_init_refuses_overwrite(tmp_path):
    (tmp_path / "otitbup.yml").write_text("existing")
    with pytest.raises(FileExistsError):
        init_project(tmp_path)
