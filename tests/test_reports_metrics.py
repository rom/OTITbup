import textwrap
import time

import pytest

from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.metrics import metrics_text, status_json
from otitbup.reports import compliance_report, dr_runbook
from otitbup.runstore import RunRecord, RunStore


@pytest.fixture
def env(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: plant-a
            zones:
              - name: net
                devices:
                  - {name: sw-01, driver: cisco_ios}
                  - {name: sw-02, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    runstore = RunStore(tmp_path / "runstore.db")
    now = time.time()
    # sw-01 healthy; sw-02 never succeeded.
    store.write_and_commit(config.all_devices()[0],
                           [Artifact(name="c.txt", data=b"hostname sw-01\n")])
    runstore.record_run(RunRecord("plant-a/net/sw-01", now - 3600, now - 3599,
                                  True, True, "abc", "changed", expected=False))
    runstore.record_run(RunRecord("plant-a/net/sw-02", now - 3600, now - 3599,
                                  False, False, None, "boom"))
    return config, store, runstore, now


def test_status_json(env):
    config, store, runstore, now = env
    data = status_json(config, store, runstore, now=now)
    assert data["totals"]["devices"] == 2
    assert data["totals"]["covered"] == 1
    assert data["totals"]["never_backed_up"] == 1
    assert data["totals"]["failing"] == 1
    devices = {d["device"]: d for d in data["devices"]}
    assert devices["plant-a/net/sw-01"]["consecutive_failures"] == 0
    assert devices["plant-a/net/sw-02"]["consecutive_failures"] == 1


def test_metrics_text_prometheus_format(env):
    config, store, runstore, now = env
    text = metrics_text(config, store, runstore, now=now)
    assert "otitbup_devices_total 2" in text
    assert "otitbup_devices_never_backed_up 1" in text
    assert 'otitbup_device_consecutive_failures{device="plant-a/net/sw-02"' in text
    assert "# TYPE otitbup_devices_total gauge" in text


def test_compliance_report_html(env):
    config, store, runstore, now = env
    # A policy-triggering config so the report has findings.
    store.write_and_commit(config.all_devices()[0], [Artifact(
        name="show_running_config.txt", data=b"ip http server\n")])
    html = compliance_report(config, store, runstore, now=now)
    assert "compliance report" in html.lower()
    assert "plant-a/net/sw-01" in html
    assert "never" in html.lower()        # sw-02 coverage
    assert "no-http-server" in html       # policy finding surfaced


def test_dr_runbook_html(env):
    config, store, runstore, now = env
    runstore.record_rehearsal("plant-a/net/sw-01", now - 86400, "abc", "pass")
    html = dr_runbook(config, store, runstore, "plant-a", now=now)
    assert "Disaster recovery runbook" in html
    assert "plant-a/net/sw-01" in html
    assert "NO BACKUP ON RECORD" in html   # sw-02 has no commit
    assert "rehearsal" in html.lower()


def test_dr_runbook_unknown_site(env):
    config, store, runstore, now = env
    with pytest.raises(KeyError):
        dr_runbook(config, store, runstore, "nope", now=now)
