"""Regression tests for the web UI updates: menus (Anomaly, Administration,
Documentation), new themes, web UI settings, config-page context help and
ordering, the policy settings section, and the driver catalog."""
import textwrap
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from otitbup import configedit
from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.webui import _SIZES, _THEMES, WebUI, _Handler


@pytest.fixture
def server(tmp_path):
    cfg_path = tmp_path / "otitbup.yml"
    cfg_path.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: plant-a
            zones:
              - name: net
                devices:
                  - {name: sw-01, driver: cisco_ios, schedule: 12h}
                  - {name: gw-01, driver: generic_http, schedule: 12h}
    """))
    config = load_config(cfg_path)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    device = config.all_devices()[0]
    store.write_and_commit(device, [
        Artifact(name="show_running_config.txt",
                 data=b"hostname sw-01\ntransport input all\n"),
    ])
    ui = WebUI(config, store, config_path=str(cfg_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", ui
    httpd.shutdown()


def _get(url, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers=headers or {})
    with opener.open(req, timeout=5) as response:
        return response.status, response.headers, response.read().decode()


def _post(url, data=b"", headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers=headers or {})
    try:
        with opener.open(req, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


# -------------------------------------------------------- CSRF / redirect

def test_cross_origin_post_rejected(server):
    base, _ = server
    # A cross-site form POST (browser attaches ambient credentials) is
    # refused by the same-origin check even without a session token.
    code, body = _post(base + "/config/site-add",
                       headers={"Origin": "http://evil.example"})
    assert code == 403 and "cross-origin" in body


def test_same_origin_post_allowed(server):
    base, _ = server
    host = base.split("//", 1)[1]
    code, _ = _post(base + "/config/site-add",
                    data=b"site=", headers={"Origin": base})
    # Same-origin passes the CSRF gate (site name empty -> a normal failure
    # result page, not a 403).
    assert code != 403
    # A request with no Origin/Referer (non-browser client) is also allowed.
    code2, _ = _post(base + "/config/site-add", data=b"site=")
    assert code2 != 403
    assert host  # sanity


def test_safe_next_rejects_offsite_and_crlf():
    from otitbup.webui import _Handler
    assert _Handler._safe_next("/health") == "/health"
    assert _Handler._safe_next("//evil.example") == "/"
    assert _Handler._safe_next("/\\evil.example") == "/"
    assert _Handler._safe_next("https://evil.example") == "/"
    assert _Handler._safe_next("/ok%0d%0aSet-Cookie:x") == "/ok%0d%0aSet-Cookie:x"
    assert _Handler._safe_next("/x\r\nSet-Cookie: y") == "/"


# ------------------------------------------------------------------ menus

def test_nav_has_renamed_menus(server):
    base, _ = server
    _, _, text = _get(base + "/")
    assert "Anomaly" in text
    assert "Administration ▾" in text and "Documentation ▾" in text
    # Administration submenu
    assert "/webui-settings" in text and "/config" in text and "/users" in text
    # Documentation submenu
    for slug in ("/help", "/help/releasenotes", "/help/faq", "/help/usage",
                 "/help/sitecollector"):
        assert f"href='{slug}'" in text
    # The old standalone Policy entry is gone (replaced by Anomaly).
    assert "href='/policy'" not in text


def test_policy_redirects_to_anomaly(server):
    base, _ = server
    status, _, text = _get(base + "/policy")   # urllib follows the 303
    assert status == 200
    assert "Anomalies &amp; policy deviations" in text


def test_anomaly_page_combines_findings_and_shortcuts(server):
    base, _ = server
    status, _, text = _get(base + "/anomaly")
    assert status == 200
    assert "Anomalies" in text and "Policy deviations" in text
    # The captured config trips the vty rule.
    assert "no-vty-transport-all" in text
    # Shortcuts into the config editor sections.
    assert "/config#cfg-anomaly" in text and "/config#cfg-policy" in text


# ------------------------------------------------------------- themes/size

def test_new_themes_are_registered_and_styled(server):
    base, _ = server
    for theme in ("high-contrast", "solarized", "nord", "dracula",
                  "gruvbox", "wcag"):
        assert theme in _THEMES
    _, _, text = _get(base + "/")
    for theme in ("high-contrast", "solarized", "nord", "dracula",
                  "gruvbox", "wcag"):
        assert f'data-theme="{theme}"' in text  # CSS block present
        assert f"<option value='{theme}'" in text  # picker option


def test_webui_settings_page(server):
    base, _ = server
    status, _, text = _get(base + "/webui-settings")
    assert status == 200
    assert "Web UI settings" in text
    assert "set-theme" in text and "set-size" in text and "set-wcag" in text
    assert "WCAG" in text
    for size in _SIZES:
        assert f"<option value='{size}'" in text


def test_size_and_wcag_prefs_apply_via_cookie(server):
    base, _ = server
    status, headers, _ = _get(base + "/theme?size=large")
    assert status == 204
    assert "otitbup_size=large" in headers.get("Set-Cookie", "")
    _, _, text = _get(base + "/", headers={"Cookie": "otitbup_size=large"})
    assert "data-size='large'" in text

    status, headers, _ = _get(base + "/theme?wcag=1")
    assert "otitbup_wcag=1" in headers.get("Set-Cookie", "")
    _, _, text = _get(base + "/", headers={"Cookie": "otitbup_wcag=1"})
    assert "data-wcag='1'" in text


def test_theme_cookie_still_applies(server):
    base, _ = server
    _, _, text = _get(base + "/", headers={"Cookie": "otitbup_theme=nord"})
    assert "data-theme='nord'" in text


# ------------------------------------------------------------ config page

def test_config_page_help_ordering_and_policy_section(server):
    base, _ = server
    status, _, text = _get(base + "/config")
    assert status == 200
    # Per-setting expandable help buttons with content.
    assert "help-btn" in text and "cfg-help" in text
    # Inventory comes before global settings.
    assert text.index("<h3>Inventory</h3>") < text.index("Global settings")
    # Section anchors for the Anomaly-page shortcuts.
    assert "id='cfg-anomaly'" in text and "id='cfg-policy'" in text
    # Policy settings section with the custom-rule editor.
    assert "Policy (config compliance rules)" in text
    assert "/config/policy-rule-add" in text
    # SNMP trap version selector in the events section.
    assert "snmp_trap.version" in text and "v3.auth_protocol" in text


def test_policy_rule_add_and_delete_roundtrip(server, tmp_path):
    _, ui = server
    ok, msg, _ = ui.action_config_policy_rule_add(
        {"id": "require-ntp", "description": "no NTP", "severity": "low",
         "absent": "ntp server"}, actor="t")
    assert ok, msg
    assert any(r["id"] == "require-ntp"
               for r in ui.config.policy.get("rules", []))
    # Duplicate id is refused.
    ok, msg, _ = ui.action_config_policy_rule_add(
        {"id": "require-ntp", "match": "x"}, actor="t")
    assert not ok
    ok, msg, _ = ui.action_config_policy_rule_delete(
        {"id": "require-ntp"}, actor="t")
    assert ok, msg
    assert not ui.config.policy.get("rules")


def test_policy_rule_requires_pattern(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("data_dir: ./data\nsites: []\n")
    with pytest.raises(configedit.ConfigEditError):
        configedit.add_policy_rule(cfg, {"id": "empty-rule"})


# ---------------------------------------------------------- driver catalog

def test_driver_catalog_sorts(server):
    base, _ = server
    _, _, text = _get(base + "/drivers")
    assert "?sort=inuse" in text               # header link to toggle
    _, _, sorted_text = _get(base + "/drivers?sort=inuse")
    # In-use drivers first: cisco_ios and generic_http before an unused one.
    assert sorted_text.index("cisco_ios") < sorted_text.index("beckhoff_ads")
    assert sorted_text.index("generic_http") < sorted_text.index("omron_fins")
    # Toggle back link present.
    assert "href='/drivers'" in sorted_text


def test_driver_detail_pages(server):
    base, _ = server
    status, _, text = _get(base + "/drivers/cisco_ios")
    assert status == 200
    assert "Vendor profile" in text
    assert "show running-config" in text       # profile commands
    assert "otitbup[ssh]" in text              # dependency hint
    assert "sw-01" in text                     # device using it
    assert "Example inventory entry" in text

    status, _, text = _get(base + "/drivers/generic_http")
    assert status == 200
    assert "Setup" in text                     # module docstring shown

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(base + "/drivers/no-such-driver")
    assert excinfo.value.code == 404
