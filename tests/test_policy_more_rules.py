"""The extended built-in policy rule set."""
import textwrap

import pytest

from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.policy import BUILTIN_RULES, check_device


@pytest.fixture
def setup(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: plant-a
            zones:
              - name: net
                devices:
                  - {name: sw-01, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    return config, store


def _findings(config, store, text):
    device = config.all_devices()[0]
    store.write_and_commit(
        device, [Artifact(name="show_running_config.txt",
                          data=text.encode())])
    return {f.rule_id for f in check_device(config, store, device)}


@pytest.mark.parametrize("line,rule", [
    ("ftp-server enable", "no-ftp-server"),
    ("feature ftp-server", "no-ftp-server"),
    ("tftp-server flash:c2960-image", "no-tftp-server"),
    ("ip ssh version 1", "no-ssh-v1"),
    ("snmp-server user monitor grp v3 auth sha k priv des k2",
     "no-snmpv3-des"),
    ("username fred password 0 letmein", "no-weak-user-password"),
    ("username fred password 7 0822455D0A16", "no-weak-user-password"),
    ("username admin password admin", "no-default-admin-password"),
    ("access-list 100 permit ip any any", "no-permit-any-any"),
    ("permit ip any any", "no-permit-any-any"),
    ("transport input all", "no-vty-transport-all"),
    ("enable secret 5 $1$abcd$xyz", "weak-enable-md5"),
    ("ip source-route", "no-ip-source-route"),
])
def test_new_rules_fire(setup, line, rule):
    config, store = setup
    assert rule in _findings(config, store, f"hostname sw-01\n{line}\n")


def test_hardened_config_is_clean(setup):
    config, store = setup
    hardened = textwrap.dedent("""
        hostname sw-01
        no ip http server
        ip ssh version 2
        username fred secret 9 $9$salt$hash
        enable secret 9 $9$salt$hash
        snmp-server user monitor grp v3 auth sha k priv aes 128 k2
        line vty 0 4
         transport input ssh
        access-list 100 permit ip 10.0.0.0 0.0.0.255 any
        no ip source-route
    """)
    assert _findings(config, store, hardened) == set()


def test_hashed_user_password_not_flagged(setup):
    config, store = setup
    found = _findings(config, store,
                      "hostname sw-01\nusername ops password 5 $1$x$y\n")
    assert "no-weak-user-password" not in found


def test_rules_have_unique_ids_and_valid_severities():
    ids = [r.id for r in BUILTIN_RULES]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 16
    assert all(r.severity in ("low", "medium", "high", "critical")
               for r in BUILTIN_RULES)
    assert all(r.match or r.absent for r in BUILTIN_RULES)
