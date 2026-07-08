import textwrap

import pytest

from otitbup import configedit
from otitbup.config import load_config
from otitbup.gitstore import GitStore
from otitbup.webui import WebUI


def _cfg(tmp_path):
    p = tmp_path / "otitbup.yml"
    p.write_text(textwrap.dedent("""
        # commented config
        data_dir: ./data
        sites:
          - name: plant-a
            zones:
              - name: cell-1
                devices:
                  - {name: plc-01, driver: cisco_ios, schedule: 12h}
    """))
    return p


# ------------------------------------------------------------- backend

def test_set_global_and_backup(tmp_path):
    p = _cfg(tmp_path)
    configedit.set_global(p, "offsite",
                          {"transport": "s3", "bucket": "b", "region": "eu"})
    c = load_config(p)
    assert c.offsite == {"transport": "s3", "bucket": "b", "region": "eu"}
    assert (tmp_path / "otitbup.yml.bak").exists()


def test_set_global_blank_deletes_key(tmp_path):
    p = _cfg(tmp_path)
    configedit.set_global(p, "netbox", {"url": "http://x", "token": "t"})
    configedit.set_global(p, "netbox", {"token": ""})  # clear token
    c = load_config(p)
    assert c.netbox == {"url": "http://x"}


def test_set_device_rename_and_retention(tmp_path):
    p = _cfg(tmp_path)
    qn = configedit.set_device(p, "plant-a", "cell-1", "plc-01", {
        "name": "plc-01a", "schedule": "1h", "credentials": "cred",
        "retention": {"keep_versions": 10},
    })
    assert qn == "plant-a/cell-1/plc-01a"
    dev = load_config(p).all_devices()[0]
    assert dev.name == "plc-01a" and dev.schedule == "1h"
    assert dev.retention["keep_versions"] == 10


def test_zone_maintenance_and_add_delete_device(tmp_path):
    p = _cfg(tmp_path)
    configedit.set_zone(p, "plant-a", "cell-1", {
        "maintenance_window": "18:00-06:00", "timezone": "Europe/Berlin",
        "max_concurrent": 2})
    z = load_config(p).sites[0].zones[0]
    assert z.maintenance_window == "18:00-06:00" and z.max_concurrent == 2
    configedit.add_device(p, "plant-a", "cell-1",
                         {"name": "plc-02", "driver": "schneider_modbus"})
    assert len(load_config(p).all_devices()) == 2
    configedit.delete_device(p, "plant-a", "cell-1", "plc-02")
    assert len(load_config(p).all_devices()) == 1


def test_invalid_edit_rejected_and_file_unchanged(tmp_path):
    p = _cfg(tmp_path)
    with pytest.raises(configedit.ConfigEditError):
        configedit.set_device(p, "plant-a", "cell-1", "plc-01",
                             {"schedule": "not-a-schedule"})
    # Original still valid and unchanged.
    assert load_config(p).all_devices()[0].schedule == "12h"


def test_add_and_delete_site_zone(tmp_path):
    p = _cfg(tmp_path)
    configedit.add_site(p, "plant-b")
    configedit.add_zone(p, "plant-b", "z1")
    configedit.add_device(p, "plant-b", "z1",
                         {"name": "sw-1", "driver": "cisco_ios"})
    c = load_config(p)
    assert {s.name for s in c.sites} == {"plant-a", "plant-b"}
    assert any(d.qualified_name == "plant-b/z1/sw-1" for d in c.all_devices())


def test_unknown_targets(tmp_path):
    p = _cfg(tmp_path)
    with pytest.raises(configedit.ConfigEditError):
        configedit.set_zone(p, "nope", "z", {})
    with pytest.raises(configedit.ConfigEditError):
        configedit.add_site(p, "plant-a")  # already exists


# --------------------------------------------------------- web actions

def _ui(tmp_path, with_path=True):
    p = _cfg(tmp_path)
    config = load_config(p)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    return WebUI(config, store, config_path=str(p) if with_path else None), p


def test_config_page_renders(tmp_path):
    ui, _ = _ui(tmp_path)
    page = ui.config_page("tok").decode()
    for must in ("Configuration", "Offsite copy", "LDAP", "Events",
                 "site: plant-a", "zone: cell-1", "plc-01", "add site"):
        assert must in page


def test_config_page_needs_config_path(tmp_path):
    ui, _ = _ui(tmp_path, with_path=False)
    assert b"cannot locate the config file" in ui.config_page("tok")


def test_action_global_reloads_config(tmp_path):
    ui, p = _ui(tmp_path)
    ok, msg, back = ui.action_config_global(
        "logging", {"section": "logging", "level": "debug",
                    "format": "json", "max_bytes": "1000"}, "admin")
    assert ok and back == "/config"
    # WebUI reloaded its own in-memory config.
    assert ui.config.logging["level"] == "debug"
    assert ui.config.logging["max_bytes"] == 1000


def test_action_device_add_validates(tmp_path):
    ui, p = _ui(tmp_path)
    ok, msg, _ = ui.action_config_device_add(
        {"site": "plant-a", "zone": "cell-1", "name": "", "driver": ""},
        "admin")
    assert not ok  # name+driver required


def test_action_bad_int_rejected(tmp_path):
    ui, _ = _ui(tmp_path)
    ok, msg, _ = ui.action_config_global(
        "logging", {"section": "logging", "max_bytes": "not-a-number"},
        "admin")
    assert not ok and "number" in msg


def test_action_float_csv_bool_coercion(tmp_path):
    ui, _ = _ui(tmp_path)
    assert ui.action_config_global(
        "retry", {"section": "retry", "attempts": "3", "backoff": "2.5"},
        "admin")[0]
    assert ui.config.retry == {"attempts": 3, "backoff": 2.5}
    assert ui.action_config_global(
        "alerts", {"section": "alerts", "stale_days": "7",
                   "email.to": "a@x.com, b@y.com"}, "admin")[0]
    assert ui.config.alerts["email"]["to"] == ["a@x.com", "b@y.com"]
    assert ui.action_config_global(
        "anomaly", {"section": "anomaly", "enabled": "", "sigma": "4.0"},
        "admin")[0]
    assert ui.config.anomaly["enabled"] is False
    assert ui.config.anomaly["sigma"] == 4.0


def test_action_federation_collector_add_delete(tmp_path):
    ui, _ = _ui(tmp_path)
    ok, msg, _ = ui.action_config_collector_add(
        {"name": "plant-b", "url": "https://b:8443", "token": "otb_x",
         "verify_tls": "false"}, "admin")
    assert ok
    c = ui.config.federation["collectors"][0]
    assert c == {"name": "plant-b", "url": "https://b:8443",
                 "token": "otb_x", "verify_tls": False}
    assert ui.action_config_collector_delete({"name": "plant-b"}, "admin")[0]
    assert ui.config.federation.get("collectors") == []


def test_comments_preserved_on_save(tmp_path):
    p = _cfg(tmp_path)
    configedit.set_global(p, "netbox", {"url": "http://x"})
    assert "# commented config" in p.read_text()
