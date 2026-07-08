import socket

import pytest
import yaml

from otitbup.discovery import Finding, proposal_yaml, scan


@pytest.fixture
def listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    yield sock.getsockname()[1]
    sock.close()


def test_scan_finds_open_port_and_suggests_driver(listener):
    findings = scan(
        ["127.0.0.1/32"],
        ports={listener: "siemens_s7"},
        timeout=0.5, delay=0,
    )
    assert len(findings) == 1
    assert findings[0].address == "127.0.0.1"
    assert findings[0].open_ports == [listener]
    assert findings[0].driver == "siemens_s7"


def test_scan_respects_exclusions(listener):
    findings = scan(
        ["127.0.0.1/32"],
        ports={listener: "siemens_s7"},
        timeout=0.5, delay=0,
        exclude={"127.0.0.1"},
    )
    assert findings == []


def test_scan_silent_host_yields_nothing():
    # A port with nothing listening on loopback fails fast.
    findings = scan(
        ["127.0.0.1/32"], ports={1: "generic_ssh"}, timeout=0.5, delay=0
    )
    assert findings == []


def test_enrich_adds_identity_via_snmp(monkeypatch):
    import otitbup.snmp as snmp_mod
    from otitbup.discovery import Finding, enrich

    def fake_get(host, oids, community="public", port=161, timeout=3.0):
        return {snmp_mod.SYS_DESCR: "Moxa EDS-408A switch"}

    monkeypatch.setattr(snmp_mod, "snmp_get", fake_get)
    findings = [Finding(address="10.0.0.5", open_ports=[22],
                        driver="generic_ssh")]
    enrich(findings)
    assert findings[0].identity == "Moxa EDS-408A switch"


def test_enrich_survives_probe_failure(monkeypatch):
    import otitbup.snmp as snmp_mod
    from otitbup.discovery import Finding, enrich

    def boom(*a, **k):
        raise snmp_mod.SNMPError("no agent")

    monkeypatch.setattr(snmp_mod, "snmp_get", boom)
    # A driver that also fails to resolve -> identity stays empty, no raise.
    findings = [Finding(address="10.0.0.9", open_ports=[502],
                        driver="schneider_modbus")]
    enrich(findings)
    assert findings[0].identity == ""


def test_proposal_includes_identity_comment():
    from otitbup.discovery import Finding, proposal_yaml
    findings = [Finding(address="10.0.0.5", open_ports=[161],
                        driver="snmp_fingerprint", identity="Cisco C2960")]
    text = proposal_yaml(findings, "s", "z")
    assert "# identity: Cisco C2960" in text


def test_proposal_is_valid_inventory_yaml():
    findings = [
        Finding(address="10.0.0.5", open_ports=[102, 502], driver="siemens_s7"),
        Finding(address="10.0.0.9", open_ports=[22], driver="generic_ssh"),
    ]
    text = proposal_yaml(findings, site="plant-a", zone="found")
    assert "REVIEW BEFORE USE" in text

    data = yaml.safe_load(text)
    devices = data["sites"][0]["zones"][0]["devices"]
    assert devices[0]["driver"] == "siemens_s7"
    assert devices[0]["address"] == "10.0.0.5"
    assert devices[1]["driver"] == "generic_ssh"
    assert devices[1]["options"]["device_type"] == "cisco_ios"

    # The proposal must load through the real config validator.
    import pathlib
    import tempfile

    from otitbup.config import load_config
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "proposal.yml"
        path.write_text("data_dir: ./data\n" + text.split("sites:", 1)[0]
                        + "sites:" + text.split("sites:", 1)[1])
        config = load_config(path)
        assert len(config.all_devices()) == 2
