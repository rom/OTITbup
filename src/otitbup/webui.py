"""Read-only web UI (docs/REQUIREMENTS.md sections 7 and 9).

A viewer, deliberately not an editor: the YAML config stays the source of
truth. Stdlib-only (http.server), optional HTTP Basic auth (auth.py) and
TLS (webui.tls config; `otitbup certgen` makes a self-signed pair).

Routes:
    /                                        dashboard: tiles + device table
    /activity                                recent backups across all devices
    /drivers                                 driver catalog
    /device/<site>/<zone>/<name>             history, artifacts, latest diff
    /device/<site>/<zone>/<name>/commit/<h>  one backup's diff
    /device/<site>/<zone>/<name>/artifact/<path>   raw artifact at last backup
"""
from __future__ import annotations

import html
import logging
import posixpath
import re
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import yaml

from .auth import check_basic_auth
from .gitstore import GitStore
from .models import AppConfig, Device

log = logging.getLogger("otitbup.webui")

_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
_HISTORY_LINE = re.compile(r"^(\w+)\s+(\S+ \S+ \S+)\s+(.*)$")

_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 74rem;
       padding: 0 1rem; color: #1a1f24; background: #fff; }
h1 { font-size: 1.4rem; display: inline-block; margin-right: 1.5rem; }
h1 a { color: inherit; text-decoration: none; }
nav { display: inline-block; } nav a { margin-right: 1rem; }
h2 { font-size: 1.15rem; margin-top: 1.6rem; }
h3 { font-size: 1rem; margin-top: 1.4rem; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .45rem .7rem; border-bottom: 1px solid #dde3e8;
         font-size: .9rem; }
th { font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; color: #5b6570; }
a { color: #0b57d0; text-decoration: none; } a:hover { text-decoration: underline; }
pre { background: #f4f6f8; border: 1px solid #dde3e8; border-radius: 6px;
      padding: 1rem; overflow-x: auto; font-size: .85rem; line-height: 1.45; }
code { font-size: .85rem; }
.badge { font-size: .75rem; padding: .1rem .5rem; border-radius: 999px;
         background: #e7f0e8; color: #1b5e20; white-space: nowrap; }
.badge.never { background: #fdecea; color: #b3261e; }
.muted { color: #5b6570; font-size: .85rem; }
.tiles { display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0 1.5rem; }
.tile { border: 1px solid #dde3e8; border-radius: 8px; padding: .7rem 1.1rem;
        min-width: 8rem; }
.tile b { display: block; font-size: 1.5rem; }
.tile span { font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;
             color: #5b6570; }
#filter { margin: 0 0 .8rem; padding: .45rem .7rem; width: 20rem; max-width: 100%;
          border: 1px solid #dde3e8; border-radius: 6px; font-size: .9rem;
          background: inherit; color: inherit; }
.zone-head td { background: #f4f6f8; font-weight: 600; font-size: .8rem;
                text-transform: uppercase; letter-spacing: .04em; }
.sev-critical, .sev-high { color: #b3261e; font-weight: 600; }
.sev-medium { color: #b26a00; } .sev-low { color: #5b6570; }
.help { position: relative; display: inline-block; cursor: help;
        width: 15px; height: 15px; line-height: 15px; text-align: center;
        border-radius: 50%; background: #dde3e8; color: #33404d;
        font-size: 11px; font-weight: 600; margin-left: .3rem; }
.help .pop { visibility: hidden; opacity: 0; position: absolute; z-index: 10;
        left: 50%; transform: translateX(-50%); bottom: 150%; width: 15rem;
        background: #14181c; color: #f0f3f6; padding: .5rem .7rem;
        border-radius: 6px; font-size: .8rem; font-weight: normal;
        line-height: 1.4; text-align: left; transition: opacity .1s;
        box-shadow: 0 2px 8px rgba(0,0,0,.3); }
.help:hover .pop, .help:focus .pop { visibility: visible; opacity: 1; }
.ok { color: #1b7a2f; } .miss { color: #b3261e; }
.strat { font-size: 1.05rem; padding: .3rem 0; }
@media (prefers-color-scheme: dark) {
  body { background: #14181c; color: #e3e7eb; }
  th, td { border-color: #2c333a; } th { color: #98a2ad; }
  pre, .zone-head td { background: #1b2127; border-color: #2c333a; }
  a { color: #8ab4f8; }
  .badge { background: #1d3320; color: #a5d6a7; }
  .badge.never { background: #3a2222; color: #f2b8b5; }
  .muted { color: #98a2ad; }
  .tile, #filter { border-color: #2c333a; }
}
"""

_FILTER_SCRIPT = """
<script>
document.getElementById('filter').addEventListener('input', function () {
  var needle = this.value.toLowerCase();
  document.querySelectorAll('tr[data-row]').forEach(function (row) {
    row.style.display =
      row.textContent.toLowerCase().indexOf(needle) >= 0 ? '' : 'none';
  });
});
</script>
"""


def _page(title: str, body: str) -> bytes:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><header><h1><a href='/'>otitbup</a></h1>"
        f"<nav><a href='/'>Devices</a><a href='/health'>Health</a>"
        f"<a href='/search'>Search</a><a href='/drift'>Drift</a>"
        f"<a href='/activity'>Activity</a><a href='/policy'>Policy</a>"
        f"<a href='/retention'>Retention</a><a href='/strategy'>Strategy</a>"
        f"<a href='/drivers'>Drivers</a><a href='/audit'>Audit</a>"
        f"<a href='/users'>Users</a><a href='/help'>Help</a>"
        f"<a href='/logout'>Logout</a></nav></header>"
        f"{body}</body></html>"
    ).encode()


def _device_link(device: Device) -> str:
    return f"/device/{quote(device.qualified_name)}"


def _device_link_name(qualified_name: str) -> str:
    return f"/device/{quote(qualified_name)}"


def _csrf(token: str) -> str:
    return f"<input type='hidden' name='csrf' value='{html.escape(token)}'>"


def _help(text: str) -> str:
    """A small ? icon with a hover/focus popover explaining a feature."""
    return (
        f"<span class='help' tabindex='0'>?"
        f"<span class='pop'>{html.escape(text)}</span></span>"
    )


def _result_page(ok: bool, message: str, back_url: str) -> bytes:
    cls = "badge" if ok else "badge never"
    label = "OK" if ok else "FAILED"
    body = (
        f"<h2>Action result</h2>"
        f"<p><span class='{cls}'>{label}</span> {html.escape(message)}</p>"
        f"<p><a href='{html.escape(back_url)}'>&larr; back</a></p>"
    )
    return _page("otitbup — result", body)


def _button(action: str, label: str, csrf: str, fields: dict | None = None) -> str:
    hidden = _csrf(csrf) + "".join(
        f"<input type='hidden' name='{html.escape(k)}' "
        f"value='{html.escape(str(v))}'>"
        for k, v in (fields or {}).items()
    )
    return (
        f"<form method='post' action='{html.escape(action)}' "
        f"style='display:inline'>{hidden}"
        f"<button type='submit'>{html.escape(label)}</button></form>"
    )


class WebUI:
    def __init__(
        self, config: AppConfig, store: GitStore,
        auth: dict | None = None,
        blobstore=None,
        runstore=None,
        users: dict | None = None,
        events=None,
        config_path=None,
        secrets=None,
    ):
        self.config = config
        self.store = store
        self.auth = auth
        self.blobstore = blobstore
        self.runstore = runstore
        self.config_path = config_path
        self.secrets = secrets
        self._static_users = users
        from .events import NullEventBus
        from .sessions import SessionStore
        self.events = events or NullEventBus()
        self.sessions = SessionStore()

    def all_users(self) -> dict:
        """Config-declared users (static) merged with runstore users
        (GUI-managed). Config users win on conflict and cannot be deleted
        via the UI."""
        from .auth import build_users
        if self._static_users is not None:
            base = dict(self._static_users)
        else:
            base = build_users(self.auth, self.config.webui.get("users"))
        for name, rec in base.items():
            rec.setdefault("source", "config")
        if self.runstore is not None:
            for name, rec in self.runstore.get_users().items():
                if name not in base:
                    base[name] = {**rec, "source": "db"}
        return base

    @property
    def users(self) -> dict:
        return self.all_users()

    # ---------------------------------------------------- auth pages

    def login_page(self, error: str = "", next_url: str = "/") -> bytes:
        msg = (
            f"<p class='sev-high'>{html.escape(error)}</p>" if error else ""
        )
        body = (
            "<h2>Sign in</h2>" + msg
            + "<form method='post' action='/login'>"
            f"<input type='hidden' name='next' value='{html.escape(next_url)}'>"
            "<p><input name='username' placeholder='username' "
            "autocomplete='username' autofocus></p>"
            "<p><input name='password' type='password' "
            "placeholder='password' autocomplete='current-password'></p>"
            "<p><button type='submit'>Sign in</button></p></form>"
        )
        return _page("otitbup — sign in", body)

    def users_page(self, identity: dict, csrf: str) -> bytes:
        users = self.all_users()
        rows = ""
        for name, rec in sorted(users.items()):
            source = rec.get("source", "config")
            actions = ""
            if source == "db":
                actions = (
                    f"<form method='post' action='/users/delete' "
                    f"style='display:inline'>{_csrf(csrf)}"
                    f"<input type='hidden' name='username' value='"
                    f"{html.escape(name)}'>"
                    "<button type='submit'>delete</button></form>"
                )
            rows += (
                f"<tr><td>{html.escape(name)}</td>"
                f"<td>{html.escape(rec.get('role', 'viewer'))}</td>"
                f"<td>{html.escape(source)}</td><td>{actions}</td></tr>"
            )
        add_form = (
            "<h3>Add user</h3>"
            "<form method='post' action='/users/create'>" + _csrf(csrf)
            + "<input name='username' placeholder='username'> "
            "<input name='password' type='password' placeholder='password'> "
            "<select name='role'><option>viewer</option>"
            "<option>operator</option><option>admin</option></select> "
            "<button type='submit'>Create</button></form>"
        )
        passwd_form = (
            "<h3>Change a password</h3>"
            "<form method='post' action='/users/passwd'>" + _csrf(csrf)
            + "<input name='username' placeholder='username'> "
            "<input name='password' type='password' placeholder='new password'> "
            "<button type='submit'>Change</button></form>"
            "<p class='muted'>config-declared users are managed in the YAML "
            "file and cannot be edited here</p>"
        )
        body = (
            "<h2>Users</h2>"
            "<table><tr><th>Username</th><th>Role</th><th>Source</th>"
            "<th></th></tr>" + rows + "</table>" + add_form + passwd_form
        )
        return _page("otitbup — users", body)

    # ------------------------------------------------- write actions

    def action_backup(self, qualified_name: str, actor: str) -> tuple[bool, str]:
        device = self._find(qualified_name)
        if not device:
            return False, "unknown device"
        from .runner import Runner
        runner = Runner(
            self.config, self.store, secrets=self.secrets,
            runstore=self.runstore, events=self.events, force=True,
        )
        results = runner.backup_devices([device])
        r = results[0]
        return r.ok, f"{qualified_name}: {r.message}"

    def action_verify(self, qualified_name: str) -> tuple[bool, str]:
        device = self._find(qualified_name)
        if not device:
            return False, "unknown device"
        commit = self.store.last_commit_hash(device)
        if not commit:
            return False, "no backups to verify"
        problems = self.store.verify_commit(
            device, commit, blobstore=self.blobstore
        )
        if problems:
            return False, f"{len(problems)} problem(s): " + "; ".join(problems)
        return True, "verified: all hashes match the manifest"

    def action_note(
        self, qualified_name: str, commit: str, text: str, actor: str
    ) -> tuple[bool, str]:
        device = self._find(qualified_name)
        if not device:
            return False, "unknown device"
        if not _COMMIT_RE.match(commit):
            commit = self.store.last_commit_hash(device)
        if not commit:
            return False, "no commit to annotate"
        self.store.set_annotation(commit, text)
        return True, f"note saved on {commit[:10]}"

    def action_baseline(
        self, qualified_name: str, commit: str, actor: str
    ) -> tuple[bool, str]:
        import time
        device = self._find(qualified_name)
        if not device or self.runstore is None:
            return False, "unavailable"
        if not _COMMIT_RE.match(commit or ""):
            commit = self.store.last_commit_hash(device)
        if not commit:
            return False, "no backup to approve"
        self.runstore.set_baseline(
            qualified_name, commit, time.time(), set_by=actor
        )
        return True, f"baseline set to {commit[:10]}"

    def action_report(
        self, fmt: str = "html", sign: bool = False
    ) -> tuple[bool, str]:
        if self.runstore is None:
            return False, "unavailable"
        base = Path(self.config.data_dir).parent
        out = base / f"compliance-report.{fmt}"
        if fmt == "html":
            from .reports import compliance_report
            out.write_text(compliance_report(
                self.config, self.store, self.runstore))
        else:
            from .reportfmt import FORMATS, render
            if fmt not in FORMATS:
                return False, f"unknown format {fmt}"
            data, _ct, _ext = render(fmt, self.config, self.store,
                                     self.runstore)
            out.write_bytes(data)
        message = f"report written to {out}"
        if sign:
            from .signing import SigningError, sign_file
            try:
                sign_file(out, base / "report-signing.key")
                message += " (signed)"
            except SigningError as exc:
                return True, message + f" — not signed: {exc}"
        return True, message

    def reload_config(self) -> tuple[bool, str]:
        if not self.config_path:
            return False, "config path unknown (started without -c?)"
        from .config import ConfigError, load_config
        from .events import CONFIG_RELOAD
        try:
            self.config = load_config(self.config_path)
        except ConfigError as exc:
            return False, f"reload failed: {exc}"
        self.events.emit(
            CONFIG_RELOAD, "config reloaded from web UI",
            detail=self.config_path,
        )
        return True, f"config reloaded: {len(self.config.all_devices())} devices"

    def action_user_create(
        self, username: str, password: str, role: str, actor: str
    ) -> tuple[bool, str]:
        import time
        from .auth import ROLES, hash_password
        from .events import USER_CREATE
        if self.runstore is None:
            return False, "unavailable"
        if not username or not password:
            return False, "username and password required"
        if role not in ROLES:
            return False, f"invalid role (use {', '.join(ROLES)})"
        if username in self.all_users():
            return False, "user already exists"
        self.runstore.add_user(
            username, hash_password(password), role, time.time()
        )
        self.events.emit(
            USER_CREATE, f"user created: {username} ({role})",
            actor=actor, detail=username,
        )
        return True, f"user {username} created"

    def action_user_delete(
        self, username: str, actor: str
    ) -> tuple[bool, str]:
        from .events import USER_DELETE
        if self.runstore is None:
            return False, "unavailable"
        if self.all_users().get(username, {}).get("source") == "config":
            return False, "config-declared users cannot be deleted here"
        if self.runstore.delete_user(username) == 0:
            return False, "no such db user"
        self.events.emit(
            USER_DELETE, f"user deleted: {username}",
            actor=actor, detail=username,
        )
        return True, f"user {username} deleted"

    def action_user_passwd(
        self, username: str, password: str, actor: str
    ) -> tuple[bool, str]:
        from .auth import hash_password
        from .events import USER_PASSWD
        if self.runstore is None:
            return False, "unavailable"
        if not password:
            return False, "password required"
        if self.all_users().get(username, {}).get("source") == "config":
            return False, "config-declared users are managed in the YAML file"
        if self.runstore.set_user_password(username, hash_password(password)) == 0:
            return False, "no such db user"
        self.events.emit(
            USER_PASSWD, f"password changed: {username}",
            actor=actor, detail=username,
        )
        return True, f"password changed for {username}"

    # ------------------------------------------------------------ pages

    def index(self) -> bytes:
        devices = self.config.all_devices()
        backed_up = 0
        rows = []
        last_zone = None
        for device in sorted(devices, key=lambda d: (d.site, d.zone, d.name)):
            zone = self.config.find_zone(device)
            zone_key = f"{device.site} / {device.zone}"
            if zone_key != last_zone:
                window = zone.maintenance_window or "always"
                rows.append(
                    f"<tr class='zone-head'><td colspan='5'>"
                    f"{html.escape(zone_key)}"
                    f"<span class='muted'> · window {html.escape(window)}"
                    f" · max_concurrent {zone.max_concurrent}</span></td></tr>"
                )
                last_zone = zone_key
            last = self.store.last_commit_info(device)
            if last:
                backed_up += 1
                badge = f"<span class='badge'>{html.escape(last)}</span>"
            else:
                badge = "<span class='badge never'>never</span>"
            rows.append(
                f"<tr data-row><td><a href='{_device_link(device)}'>"
                f"{html.escape(device.name)}</a></td>"
                f"<td>{html.escape(device.driver)}</td>"
                f"<td>{html.escape(device.address or '-')}</td>"
                f"<td>{html.escape(device.schedule)}</td>"
                f"<td>{badge}</td></tr>"
            )
        sites = len(self.config.sites)
        tiles = (
            "<div class='tiles'>"
            f"<div class='tile'><b>{len(devices)}</b><span>devices</span></div>"
            f"<div class='tile'><b>{sites}</b><span>sites</span></div>"
            f"<div class='tile'><b>{backed_up}</b><span>backed up</span></div>"
            f"<div class='tile'><b>{len(devices) - backed_up}</b>"
            "<span>never backed up</span></div>"
            "</div>"
        )
        body = (
            tiles
            + "<input id='filter' type='search' "
              "placeholder='Filter devices…' autocomplete='off'>"
            + "<table><tr><th>Device</th><th>Driver</th><th>Address</th>"
              "<th>Schedule</th><th>Last backup</th></tr>"
            + "".join(rows) + "</table>"
            + "<p class='muted'>read-only view · the inventory is managed "
              "in the YAML config</p>"
            + _FILTER_SCRIPT
        )
        return _page("otitbup — devices", body)

    def activity(self, limit: int = 50) -> bytes:
        raw = self.store.history(None, limit=limit)
        by_path = {d.path: d for d in self.config.all_devices()}
        rows = []
        for line in raw.splitlines():
            match = _HISTORY_LINE.match(line)
            if not match:
                continue
            commit, date, subject = match.groups()
            device_cell = html.escape(subject)
            commit_cell = html.escape(commit)
            inner = re.match(r"backup\(([^)]+)\)", subject)
            if inner:
                device = next(
                    (d for d in by_path.values()
                     if d.qualified_name == inner.group(1)), None,
                )
                if device:
                    device_cell = (
                        f"<a href='{_device_link(device)}'>"
                        f"{html.escape(inner.group(1))}</a>"
                    )
                    commit_cell = (
                        f"<a href='{_device_link(device)}/commit/"
                        f"{quote(commit)}'><code>{html.escape(commit)}</code>"
                        "</a>"
                    )
            rows.append(
                f"<tr data-row><td>{html.escape(date)}</td>"
                f"<td>{device_cell}</td><td>{commit_cell}</td></tr>"
            )
        body = (
            f"<h2>Recent backups</h2>"
            "<table><tr><th>When</th><th>Device</th><th>Commit</th></tr>"
            + ("".join(rows) or "<tr><td colspan='3'>no backups yet</td></tr>")
            + "</table>"
            f"<p class='muted'>last {limit} commits</p>"
        )
        return _page("otitbup — activity", body)

    def health(self, ctx: dict | None = None) -> bytes:
        from .auth import role_rank
        ctx = ctx or {}
        csrf = ctx.get("csrf", "")
        role = ctx.get("role", "viewer")
        import time
        now = time.time()
        devices = self.config.all_devices()
        covered = never = stale = failing = 0
        rows = []
        for device in sorted(devices, key=lambda d: d.qualified_name):
            status = (
                self.runstore.status(device.qualified_name)
                if self.runstore else None
            )
            if status is None or status.last_success is None:
                never += 1
                cell = "<span class='badge never'>never</span>"
                age = "-"
            else:
                covered += 1
                age_days = (now - status.last_success) / 86400
                if age_days > 7:
                    stale += 1
                    cell = "<span class='badge never'>stale</span>"
                else:
                    cell = "<span class='badge'>ok</span>"
                age = (
                    f"{age_days:.1f}d" if age_days >= 1
                    else f"{(now - status.last_success) / 3600:.0f}h"
                )
            fails = status.consecutive_failures if status else 0
            if fails:
                failing += 1
            rows.append(
                f"<tr data-row><td><a href='{_device_link(device)}'>"
                f"{html.escape(device.qualified_name)}</a></td>"
                f"<td>{cell}</td><td>{html.escape(age)}</td>"
                f"<td>{fails or ''}</td>"
                f"<td>{html.escape(status.last_message if status else '')}</td>"
                "</tr>"
            )
        tiles = (
            "<div class='tiles'>"
            f"<div class='tile'><b>{covered}/{len(devices)}</b><span>covered"
            "</span></div>"
            f"<div class='tile'><b>{stale}</b><span>stale &gt;7d</span></div>"
            f"<div class='tile'><b>{never}</b><span>never</span></div>"
            f"<div class='tile'><b>{failing}</b><span>failing</span></div>"
            "</div>"
        )
        note = (
            "" if self.runstore else
            "<p class='muted'>run history unavailable (no run store)</p>"
        )
        controls = ""
        if role_rank(role) >= role_rank("operator"):
            controls += (
                "<form method='post' action='/report' style='display:inline'>"
                + _csrf(csrf)
                + "<select name='format'><option>html</option>"
                "<option>pdf</option><option>csv</option>"
                "<option>docx</option></select> "
                "<label><input type='checkbox' name='sign' value='1'> sign"
                "</label> <button type='submit'>Generate report</button>"
                "</form>" + _help(
                    "Writes a compliance report (coverage, changes, policy "
                    "findings) next to the data dir. PDF/DOCX/CSV are "
                    "stdlib-generated; 'sign' adds an Ed25519 signature.")
            )
        if role_rank(role) >= role_rank("admin"):
            controls += " " + _button("/reload", "Re-read config file", csrf)
            controls += _help("Re-reads otitbup.yml without a restart.")
        if controls:
            controls = "<p>" + controls + "</p>"
        body = (
            "<h2>Backup health</h2>" + tiles + controls + note
            + "<input id='filter' type='search' placeholder='Filter…' "
              "autocomplete='off'>"
            + "<table><tr><th>Device</th><th>Status</th><th>Last success</th>"
              "<th>Consec. fails</th><th>Last message</th></tr>"
            + "".join(rows) + "</table>"
            + "<p class='muted'>machine-readable: "
              "<a href='/metrics'>/metrics</a> (Prometheus) · "
              "<a href='/api/status'>/api/status</a> (JSON)</p>"
            + _FILTER_SCRIPT
        )
        return _page("otitbup — health", body)

    def search(
        self, query: str, site: str = "", zone: str = "",
        case_sensitive: bool = False,
    ) -> bytes:
        devices = self.config.all_devices()
        sites = sorted({d.site for d in devices})
        zones = sorted({d.zone for d in devices})
        by_path = {d.path: d for d in devices}
        rows = ""
        count = 0
        if query:
            for repo_path, lineno, text in self.store.search(
                query, ignore_case=not case_sensitive
            ):
                device = None
                artifact = repo_path
                for path, dev in by_path.items():
                    if repo_path.startswith(path + "/"):
                        device, artifact = dev, repo_path[len(path) + 1:]
                        break
                if site and (not device or device.site != site):
                    continue
                if zone and (not device or device.zone != zone):
                    continue
                count += 1
                if device:
                    label = f"{device.qualified_name}:{artifact}"
                    link = f"{_device_link(device)}/artifact/{quote(artifact)}"
                    cell = f"<a href='{link}'>{html.escape(label)}</a>"
                else:
                    cell = html.escape(repo_path)
                rows += (
                    f"<tr data-row><td>{cell}</td><td>{lineno}</td>"
                    f"<td><code>{html.escape(text.strip()[:200])}</code></td></tr>"
                )
        opts = lambda values, sel: "".join(  # noqa: E731
            f"<option{' selected' if v == sel else ''}>{html.escape(v)}"
            "</option>" for v in [""] + values
        )
        form = (
            "<form method='get' action='/search'>"
            f"<input type='search' name='q' value='{html.escape(query)}' "
            "placeholder='text or regex…' autocomplete='off'> "
            f"site <select name='site'>{opts(sites, site)}</select> "
            f"zone <select name='zone'>{opts(zones, zone)}</select> "
            "<label><input type='checkbox' name='cs' value='1'"
            + (" checked" if case_sensitive else "")
            + "> case</label> "
            "<button type='submit'>Search</button></form>"
        )
        body = (
            "<h2>Config search</h2>" + form
            + (
                f"<p class='muted'>{count} match(es) — latest backup of "
                "every device (regex supported)</p>"
                "<table><tr><th>Device : artifact</th><th>Line</th>"
                "<th>Match</th></tr>" + rows + "</table>"
                if query else
                "<p class='muted'>Search the latest configuration of every "
                "device — a VLAN id, an IP, a tag, a username, or a regex. "
                "Filter by site/zone.</p>"
            )
        )
        return _page("otitbup — search", body)

    def drift(self) -> bytes:
        from .baseline import all_drift
        if self.runstore is None:
            return _page("otitbup — drift",
                         "<p class='muted'>run store unavailable</p>")
        drifts = all_drift(self.config, self.store, self.runstore)
        drifted = sum(1 for d in drifts if d.drifted)
        no_baseline = sum(1 for d in drifts if not d.has_baseline)
        rows = ""
        for d in drifts:
            if not d.has_baseline:
                badge = "<span class='muted'>no baseline</span>"
            elif d.drifted:
                badge = "<span class='badge never'>DRIFTED</span>"
            else:
                badge = "<span class='badge'>matches baseline</span>"
            device = self._find(d.device)
            link = _device_link(device) if device else "#"
            rows += (
                f"<tr data-row><td><a href='{link}'>{html.escape(d.device)}"
                "</a></td>"
                f"<td>{badge}</td>"
                f"<td><code>{html.escape((d.baseline_commit or '-')[:10])}"
                "</code></td>"
                f"<td><code>{html.escape((d.latest_commit or '-')[:10])}"
                "</code></td></tr>"
            )
        tiles = (
            "<div class='tiles'>"
            f"<div class='tile'><b>{drifted}</b><span>drifted</span></div>"
            f"<div class='tile'><b>{len(drifts) - no_baseline}</b>"
            "<span>with baseline</span></div>"
            f"<div class='tile'><b>{no_baseline}</b><span>no baseline</span>"
            "</div></div>"
        )
        body = (
            "<h2>Golden-config drift "
            + _help("Drift is the difference between a device's latest "
                    "backup and its approved baseline commit. Set a baseline "
                    "from the device page or `otitbup baseline set`.")
            + "</h2>" + tiles
            + "<table><tr><th>Device</th><th>State</th><th>Baseline</th>"
              "<th>Latest</th></tr>" + rows + "</table>"
            + "<p class='muted'>set a baseline with "
              "<code>otitbup baseline set &lt;device&gt;</code></p>"
        )
        return _page("otitbup — drift", body)

    def compare(self, qualified_name: str, base: str, head: str) -> bytes | None:
        device = self._find(qualified_name)
        ref_ok = lambda r: r == "HEAD" or bool(_COMMIT_RE.match(r))  # noqa: E731
        if not device or not ref_ok(base) or not ref_ok(head):
            return None
        diff = self.store.diff_between(device, base, head).strip()
        body = (
            f"<h2>{html.escape(device.qualified_name)}</h2>"
            f"<p class='muted'><code>{html.escape(base)}</code> .. "
            f"<code>{html.escape(head)}</code> · "
            f"<a href='{_device_link(device)}'>&larr; device</a></p>"
            f"<pre>{html.escape(diff) or 'no differences'}</pre>"
        )
        return _page(f"otitbup — compare {device.qualified_name}", body)

    def audit(self) -> bytes:
        if self.runstore is None:
            return _page("otitbup — audit", "<p class='muted'>unavailable</p>")
        import datetime as _dt
        rows = "".join(
            "<tr data-row><td>"
            + _dt.datetime.fromtimestamp(
                r["at"], _dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
            + f"</td><td>{html.escape(r.get('actor') or '-')}</td>"
            f"<td>{html.escape(r.get('role') or '-')}</td>"
            f"<td>{html.escape(r.get('action') or '')}</td>"
            f"<td>{html.escape(r.get('detail') or '')}</td></tr>"
            for r in self.runstore.recent_audit(limit=300)
        )
        body = (
            "<h2>Audit log</h2>"
            "<table><tr><th>When (UTC)</th><th>User</th><th>Role</th>"
            "<th>Action</th><th>Detail</th></tr>" + rows + "</table>"
        )
        return _page("otitbup — audit", body)

    def policy(self) -> bytes:
        from .policy import check_all, load_rules, severity_rank
        findings = check_all(self.config, self.store)
        rules = load_rules(self.config)
        flat = [f for group in findings.values() for f in group]
        flat.sort(key=lambda f: severity_rank(f.severity), reverse=True)
        rows = "".join(
            f"<tr data-row><td><a href='{_device_link_name(f.device)}'>"
            f"{html.escape(f.device)}</a></td>"
            f"<td class='sev-{html.escape(f.severity)}'>"
            f"{html.escape(f.severity)}</td>"
            f"<td><code>{html.escape(f.rule_id)}</code></td>"
            f"<td>{html.escape(f.description)}</td>"
            f"<td>{html.escape(f.artifact)}</td></tr>"
            for f in flat
        )
        body = (
            "<h2>Config policy findings</h2>"
            f"<p class='muted'>{len(flat)} finding(s) across "
            f"{len(findings)} device(s) · {len(rules)} rule(s) active</p>"
            + (
                "<input id='filter' type='search' placeholder='Filter…' "
                "autocomplete='off'>"
                "<table><tr><th>Device</th><th>Severity</th><th>Rule</th>"
                "<th>Description</th><th>Artifact</th></tr>" + rows
                + "</table>" + _FILTER_SCRIPT
                if flat else "<p class='badge'>No policy findings.</p>"
            )
        )
        return _page("otitbup — policy", body)

    def retention(self) -> bytes:
        from .models import DEFAULT_RETENTION
        from .retention import describe_policy

        def _cell(policy: dict, sources: dict, key: str) -> str:
            value = policy.get(key, 0)
            if key == "large_file_threshold":
                if not value:
                    shown = "off"
                elif value >= 1024:
                    shown = f"{value // 1024} KiB"
                else:
                    shown = f"{value} B"
            else:
                shown = str(value) if value else "unlimited"
            source = sources.get(key, "default")
            note = (
                f" <span class='muted'>({html.escape(source)})</span>"
                if source != "default" else ""
            )
            return f"{html.escape(shown)}{note}"

        rows = []
        for device in self.config.all_devices():
            policy = self.config.retention_for(device)
            sources = self.config.retention_sources(device)
            rows.append(
                f"<tr data-row><td><a href='{_device_link(device)}'>"
                f"{html.escape(device.qualified_name)}</a></td>"
                f"<td>{_cell(policy, sources, 'keep_versions')}</td>"
                f"<td>{_cell(policy, sources, 'keep_days')}</td>"
                f"<td>{_cell(policy, sources, 'large_file_threshold')}</td>"
                "</tr>"
            )
        tiles = ""
        if self.blobstore is not None:
            blobs = self.blobstore.all_blobs()
            tiles = (
                "<div class='tiles'>"
                f"<div class='tile'><b>{len(blobs)}</b><span>blobs</span></div>"
                f"<div class='tile'><b>{sum(blobs.values()) / 1048576:.1f}"
                "</b><span>MiB offloaded</span></div>"
                "</div>"
            )
        defaults = describe_policy(dict(DEFAULT_RETENTION))
        global_policy = {**DEFAULT_RETENTION, **self.config.retention}
        body = (
            "<h2>Retention policies</h2>"
            + tiles
            + f"<p class='muted'>built-in defaults: {html.escape(defaults)}"
              " · global config: "
              f"{html.escape(describe_policy(global_policy))}</p>"
            + "<table><tr><th>Device</th><th>Keep versions</th>"
              "<th>Keep days</th><th>Offload threshold</th></tr>"
            + "".join(rows) + "</table>"
            + "<p class='muted'>effective policy per device: device &gt; "
              "zone &gt; site &gt; global &gt; default (per field); "
              "expired blobs are pruned with <code>otitbup retention "
              "--apply</code> — git history is never rewritten</p>"
        )
        return _page("otitbup — retention", body)

    def strategy(self) -> bytes:
        from .strategy import evaluate
        if self.runstore is None:
            return _page("otitbup — strategy",
                         "<p class='muted'>run store unavailable</p>")
        result = evaluate(self.config, self.store, self.runstore,
                          blobstore=self.blobstore)
        rows = ""
        for check in result.checks:
            mark = ("<span class='ok'>✔</span>" if check.ok
                    else "<span class='miss'>✘</span>")
            rem = (f"<br><span class='muted'>→ {html.escape(check.remediation)}"
                   "</span>" if not check.ok and check.remediation else "")
            rows += (
                f"<div class='strat'>{mark} {html.escape(check.label)} "
                f"<span class='muted'>· {html.escape(check.detail)}</span>"
                f"{rem}</div>"
            )
        def badge(ok):
            return ("<span class='badge'>SATISFIED</span>" if ok
                    else "<span class='badge never'>NOT met</span>")
        body = (
            "<h2>Backup strategy "
            + _help("3 copies of your data, on 2 different media, 1 offsite. "
                    "3-2-1-1-0 adds 1 offline/air-gapped copy and 0 errors "
                    "(backups verify). otitbup maps these to the local repo, "
                    "a git remote mirror, and an offline export archive.")
            + "</h2>"
            f"<div class='tiles'><div class='tile'><b>{result.copies}</b>"
            "<span>copies</span></div>"
            f"<div class='tile'><b>{result.media}</b><span>media</span></div>"
            f"<div class='tile'>{badge(result.satisfies_321)}"
            "<span>3-2-1</span></div>"
            f"<div class='tile'>{badge(result.satisfies_32110)}"
            "<span>3-2-1-1-0</span></div></div>"
            + rows
            + "<p class='muted'>Take an offline copy with "
              "<code>otitbup export</code>; enable a remote mirror with "
              "<code>git.push</code> + <code>git.remote</code>; declare the "
              "offline copy under <code>strategy.offline</code>.</p>"
        )
        return _page("otitbup — strategy", body)

    def help_page(self) -> bytes:
        sections = [
            ("Getting started",
             "The inventory (otitbup.yml) is the source of truth: sites → "
             "zones → devices. Every collector is read-only toward devices. "
             "Run backups from the CLI or, as an operator, from a device "
             "page here."),
            ("Devices & health",
             "The Devices page lists everything with a live filter. Health "
             "shows coverage, staleness and failures. A device page shows "
             "artifacts, history, per-commit and two-backup diffs, run "
             "timeline, notes, baseline drift and rehearsals."),
            ("Search",
             "Search runs across the latest backup of every device — a VLAN, "
             "IP, tag or username, or a regex — filterable by site/zone."),
            ("Change management",
             "Unexpected changes (outside a maintenance window) alert as the "
             "unauthorized-change signal. Set a baseline to track golden-"
             "config drift. Add a note to a backup to record why it changed."),
            ("Policy & compliance",
             "Policy lints captured configs for insecure settings. Generate "
             "compliance reports (HTML/CSV/PDF/DOCX, optionally signed) from "
             "the Health page or `otitbup report`."),
            ("Retention & strategy",
             "Large artifacts offload to a deduplicated blob store and expire "
             "by policy. The Strategy page evaluates your 3-2-1 / 3-2-1-1-0 "
             "posture and tells you what's missing."),
            ("Recovery",
             "Export a hash-verified restore bundle per device; network gear "
             "can be restored automatically (dry-run first). DR runbooks and "
             "restore rehearsals are tracked per site/device."),
            ("Roles",
             "viewer: read-only. operator: back up, verify, note, baseline, "
             "report. admin: reload config, manage users, view the audit "
             "log. Every action and page view is audited."),
            ("Integrations",
             "Prometheus /metrics and JSON /api/* for dashboards; syslog and "
             "SNMP traps and ServiceNow/Jira/RT tickets for events; NetBox "
             "reconciliation for coverage."),
        ]
        blocks = "".join(
            f"<h3>{html.escape(t)}</h3><p>{html.escape(b)}</p>"
            for t, b in sections
        )
        body = (
            "<h2>Help</h2>"
            "<p class='muted'>Full guides: USAGE.md, CONFIGURATION.md and "
            "FAQ.md in the docs directory.</p>" + blocks
        )
        return _page("otitbup — help", body)

    def drivers(self) -> bytes:
        from .drivers import driver_descriptions
        in_use = {d.driver for d in self.config.all_devices()}
        rows = [
            f"<tr data-row><td><code>{html.escape(name)}</code></td>"
            f"<td>{html.escape(description)}</td>"
            f"<td>{'✓' if name in in_use else ''}</td></tr>"
            for name, description in driver_descriptions().items()
        ]
        body = (
            "<h2>Driver catalog</h2>"
            "<table><tr><th>Driver</th><th>Description</th><th>In use</th></tr>"
            + "".join(rows) + "</table>"
        )
        return _page("otitbup — drivers", body)

    def device(self, qualified_name: str, ctx: dict | None = None) -> bytes | None:
        from .auth import role_rank
        ctx = ctx or {}
        can_write = role_rank(ctx.get("role", "viewer")) >= role_rank("operator")
        csrf = ctx.get("csrf", "")
        device = self._find(qualified_name)
        if not device:
            return None
        zone = self.config.find_zone(device)
        commit = self.store.last_commit_hash(device)

        artifact_rows = []
        if commit:
            manifest = self._manifest(device, commit)
            for name, meta in sorted(manifest.items()):
                link = (
                    f"{_device_link(device)}/artifact/{quote(name)}"
                )
                artifact_rows.append(
                    f"<tr><td><a href='{link}'>{html.escape(name)}</a></td>"
                    f"<td>{html.escape(str(meta.get('kind', '')))}</td>"
                    f"<td><code>{html.escape(str(meta.get('sha256', ''))[:16])}"
                    "…</code></td></tr>"
                )

        annotated = self.store.annotated_commits()
        history_rows = []
        for line in self.store.history(device, limit=30).splitlines():
            match = _HISTORY_LINE.match(line)
            if not match:
                continue
            chash, date, subject = match.groups()
            full = self.store.last_commit_hash(device) if not history_rows else None
            note = ""
            # Match short hash against annotated full hashes.
            if any(a.startswith(chash) for a in annotated):
                annotation = self.store.get_annotation(chash)
                note = (
                    f" <span class='badge'>note</span> "
                    f"{html.escape(annotation.splitlines()[0] if annotation else '')}"
                )
            history_rows.append(
                f"<tr><td>{html.escape(date)}</td>"
                f"<td><a href='{_device_link(device)}/commit/{quote(chash)}'>"
                f"<code>{html.escape(chash)}</code></a></td>"
                f"<td>{html.escape(subject)}{note}</td></tr>"
            )

        # Run status, health timeline, and rehearsal history.
        status_line = ""
        rehearsal_block = ""
        timeline_block = ""
        if self.runstore is not None:
            import datetime as _dt
            runs = self.runstore.recent_runs(device.qualified_name, limit=60)
            if runs:
                cells = ""
                for run in reversed(runs):   # oldest -> newest
                    when = _dt.datetime.fromtimestamp(
                        run["started_at"], _dt.timezone.utc
                    ).strftime("%Y-%m-%d %H:%M")
                    if not run["ok"]:
                        color, sym = "#b3261e", "fail"
                    elif run["changed"]:
                        color, sym = "#0b57d0", "change"
                    else:
                        color, sym = "#1b7a2f", "ok"
                    cells += (
                        f"<span title='{when}: {sym}' style='display:inline-"
                        f"block;width:10px;height:18px;margin:1px;background:"
                        f"{color};border-radius:2px'></span>"
                    )
                timeline_block = (
                    "<h3>Health timeline "
                    "<span class='muted' style='font-weight:normal'>"
                    "(oldest → newest; green ok, blue change, red fail)"
                    "</span></h3><p>" + cells + "</p>"
                )
            import time
            now = time.time()
            st = self.runstore.status(device.qualified_name)
            if st.last_attempt is not None:
                ok = "ok" if st.last_ok else "FAILED"
                last_success = (
                    "never" if st.last_success is None
                    else f"{(now - st.last_success) / 86400:.1f}d ago"
                )
                status_line = (
                    f" · last attempt {ok}"
                    + (f", {st.consecutive_failures} consecutive failures"
                       if st.consecutive_failures else "")
                    + f", last success {html.escape(last_success)}"
                )
            reh = self.runstore.rehearsals(device.qualified_name, limit=10)
            if reh:
                import datetime as _dt
                rows = "".join(
                    "<tr><td>"
                    + _dt.datetime.fromtimestamp(
                        r["at"], _dt.timezone.utc
                    ).strftime("%Y-%m-%d %H:%M")
                    + f"</td><td>{html.escape(r['result'])}</td>"
                    f"<td>{html.escape(r.get('tested_by') or '')}</td>"
                    f"<td>{html.escape(r.get('notes') or '')}</td></tr>"
                    for r in reh
                )
                rehearsal_block = (
                    "<h3>Restore rehearsals</h3>"
                    "<table><tr><th>When</th><th>Result</th><th>By</th>"
                    "<th>Notes</th></tr>" + rows + "</table>"
                )

        # Policy findings for this device.
        from .policy import check_device, severity_rank
        pf = sorted(
            check_device(self.config, self.store, device),
            key=lambda f: severity_rank(f.severity), reverse=True,
        )
        policy_block = ""
        if pf:
            rows = "".join(
                f"<tr><td class='sev-{html.escape(f.severity)}'>"
                f"{html.escape(f.severity)}</td>"
                f"<td><code>{html.escape(f.rule_id)}</code></td>"
                f"<td>{html.escape(f.description)}</td></tr>" for f in pf
            )
            policy_block = (
                "<h3>Policy findings</h3>"
                "<table><tr><th>Severity</th><th>Rule</th>"
                "<th>Description</th></tr>" + rows + "</table>"
            )

        # Baseline / golden-config drift.
        drift_block = ""
        if self.runstore is not None:
            from .baseline import device_drift, drift_diff
            dr = device_drift(self.store, self.runstore, device)
            if dr.has_baseline:
                if dr.drifted:
                    dd = drift_diff(self.store, self.runstore, device).strip()
                    drift_block = (
                        "<h3>Baseline drift "
                        "<span class='badge never'>DRIFTED</span></h3>"
                        f"<p class='muted'>baseline "
                        f"<code>{html.escape((dr.baseline_commit or '')[:10])}"
                        "</code> vs latest "
                        f"<code>{html.escape((dr.latest_commit or '')[:10])}"
                        "</code></p>"
                        f"<pre>{html.escape(dd)}</pre>"
                    )
                else:
                    drift_block = (
                        "<h3>Baseline <span class='badge'>matches</span></h3>"
                        f"<p class='muted'>baseline "
                        f"<code>{html.escape((dr.baseline_commit or '')[:10])}"
                        "</code></p>"
                    )

        # Write-action bar (operator+): back up now, verify, approve baseline.
        action_bar = ""
        note_form = ""
        compare_form = ""
        if can_write:
            dl = _device_link(device)
            action_bar = (
                "<p>"
                + _button(f"{dl}/backup", "Back up now", csrf)
                + " " + _button(f"{dl}/verify", "Verify backup", csrf)
                + (
                    " " + _button(f"{dl}/baseline", "Set baseline", csrf,
                                  {"commit": commit or ""})
                    if commit else ""
                )
                + "</p>"
            )
            note_form = (
                "<h3>Add note to latest backup</h3>"
                f"<form method='post' action='{dl}/note'>" + _csrf(csrf)
                + f"<input type='hidden' name='commit' value='{commit or ''}'>"
                "<input name='text' placeholder='e.g. MOC-1234: firmware "
                "upgrade' size='50'> <button type='submit'>Save note</button>"
                "</form>"
            )
        commits = self.store.device_commits(device)
        if len(commits) >= 2:
            options = "".join(
                f"<option value='{c}'>{c[:10]} · {ts}</option>"
                for c, ts in commits
            )
            compare_form = (
                "<h3>Compare two backups</h3>"
                f"<form method='get' action='{_device_link(device)}/compare'>"
                "base <select name='base'>" + options + "</select> "
                "head <select name='head'>" + options + "</select> "
                "<button type='submit'>Compare</button></form>"
            )

        diff = self.store.last_diff(device).strip()
        from .retention import describe_policy
        policy_line = describe_policy(self.config.retention_for(device))
        body = (
            f"<h2>{html.escape(device.qualified_name)}</h2>"
            f"<p class='muted'>driver {html.escape(device.driver)} · "
            f"address {html.escape(device.address or '-')} · "
            f"schedule {html.escape(device.schedule)} · "
            f"window {html.escape(zone.maintenance_window or 'always')} · "
            f"<a href='/retention'>retention</a> "
            f"{html.escape(policy_line)}{status_line}</p>"
            + action_bar
            + timeline_block
            + policy_block
            + "<h3>Artifacts (latest backup)</h3>"
            + (
                "<table><tr><th>Artifact</th><th>Kind</th><th>sha256</th></tr>"
                + "".join(artifact_rows) + "</table>"
                if artifact_rows else "<p class='muted'>no backups yet</p>"
            )
            + "<h3>History</h3>"
            + (
                "<table><tr><th>When</th><th>Commit</th><th>Subject</th></tr>"
                + "".join(history_rows) + "</table>"
                if history_rows else "<p class='muted'>no backups yet</p>"
            )
            + note_form
            + compare_form
            + rehearsal_block
            + drift_block
            + "<h3>Latest change</h3>"
            + f"<pre>{html.escape(diff) or 'no backups yet'}</pre>"
        )
        return _page(f"otitbup — {device.qualified_name}", body)

    def commit(self, qualified_name: str, commit: str) -> bytes | None:
        device = self._find(qualified_name)
        if not device or not _COMMIT_RE.match(commit):
            return None
        diff = self.store.commit_diff(device, commit).strip()
        if not diff:
            return None
        body = (
            f"<h2>{html.escape(device.qualified_name)} · "
            f"<code>{html.escape(commit)}</code></h2>"
            f"<p><a href='{_device_link(device)}'>&larr; back to device</a></p>"
            f"<pre>{html.escape(diff)}</pre>"
        )
        return _page(
            f"otitbup — {device.qualified_name} @ {commit[:10]}", body
        )

    def artifact(
        self, qualified_name: str, artifact: str
    ) -> tuple[bytes, str] | None:
        device = self._find(qualified_name)
        if not device:
            return None
        clean = posixpath.normpath(artifact)
        if clean.startswith(("/", "..")):
            return None
        commit = self.store.last_commit_hash(device)
        if not commit:
            return None
        try:
            data = self.store.read_file_at(commit, f"{device.path}/{clean}")
        except Exception:
            return None
        from .blobstore import parse_pointer
        pointer = parse_pointer(data)
        if pointer:
            sha, _size = pointer
            try:
                if self.blobstore is None:
                    raise KeyError(sha)
                data = self.blobstore.get(sha)
            except KeyError:
                data = (
                    data + b"\n# offloaded content expired by retention "
                    b"or blob store unavailable\n"
                )
        content_type = "text/plain; charset=utf-8"
        if b"\x00" in data:
            content_type = "application/octet-stream"
        else:
            try:
                data.decode()
            except UnicodeDecodeError:
                content_type = "application/octet-stream"
        return data, content_type

    # ---------------------------------------------------------- helpers

    def _find(self, qualified_name: str) -> Device | None:
        return next(
            (d for d in self.config.all_devices()
             if d.qualified_name == qualified_name), None,
        )

    def _manifest(self, device: Device, commit: str) -> dict:
        try:
            raw = self.store.read_file_at(
                commit, f"{device.path}/manifest.yml"
            )
            manifest = yaml.safe_load(raw)
            return manifest if isinstance(manifest, dict) else {}
        except Exception:
            return {}


class _Handler(BaseHTTPRequestHandler):
    def __init__(self, ui: WebUI, *args, **kwargs):
        self.ui = ui
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # route to logging, not stderr
        log.debug(fmt, *args)

    def _api(self, path: str):
        """Read-only JSON API mirroring the UI, for CMDBs/dashboards."""
        from .metrics import status_json
        if path == "/api/status":
            return status_json(
                self.ui.config, self.ui.store, self.ui.runstore,
                self.ui.blobstore,
            )
        if path == "/api/devices":
            return {
                "devices": [
                    {
                        "device": d.qualified_name, "site": d.site,
                        "zone": d.zone, "driver": d.driver,
                        "address": d.address, "schedule": d.schedule,
                    }
                    for d in self.ui.config.all_devices()
                ]
            }
        if path == "/api/policy":
            from .policy import check_all
            findings = check_all(self.ui.config, self.ui.store)
            return {
                "findings": [
                    {
                        "device": f.device, "rule": f.rule_id,
                        "severity": f.severity, "description": f.description,
                        "artifact": f.artifact,
                    }
                    for group in findings.values() for f in group
                ]
            }
        if path.startswith("/api/device/"):
            name = path[len("/api/device/"):]
            device = self.ui._find(name)
            if not device:
                return None
            status = (
                self.ui.runstore.status(name) if self.ui.runstore else None
            )
            return {
                "device": name, "driver": device.driver,
                "address": device.address,
                "last_success": status.last_success if status else None,
                "last_attempt": status.last_attempt if status else None,
                "last_ok": status.last_ok if status else None,
                "consecutive_failures":
                    status.consecutive_failures if status else 0,
                "recent_runs": (
                    self.ui.runstore.recent_runs(name, limit=20)
                    if self.ui.runstore else []
                ),
            }
        return None

    def _send(self, status: int, content: bytes,
              content_type: str = "text/html; charset=utf-8",
              headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def _cookie_token(self) -> str | None:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "otitbup_session":
                return value
        return None

    def _identify(self) -> dict | None:
        """Resolve the request identity: cookie session first (browser),
        then HTTP Basic (API/CLI/scrapers). Returns {username, role, token,
        via} or None."""
        session = self.ui.sessions.get(self._cookie_token())
        if session:
            return {
                "username": session.username, "role": session.role,
                "token": session.token, "via": "session",
            }
        from .auth import authenticate
        ident = authenticate(self.headers.get("Authorization"), self.ui.users)
        if ident:
            ident["via"] = "basic"
        return ident

    def _redirect(self, location: str, headers: dict | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        from urllib.parse import parse_qs
        raw = self.path.split("?", 1)
        path = unquote(raw[0])
        query = parse_qs(raw[1]) if len(raw) > 1 else {}
        if path == "/healthz":
            return self._send(200, b'{"status":"ok"}\n', "application/json")
        if path == "/login":
            return self._send(200, self.ui.login_page(
                next_url=query.get("next", ["/"])[0]))

        identity = self._identify()
        # Auth required if any users are configured. A client that SENT
        # (invalid) Basic credentials gets a 401 challenge; a browser with
        # no credentials gets the login page.
        if self.ui.users and identity is None:
            sent_basic = bool(self.headers.get("Authorization"))
            if sent_basic or path.startswith("/api/") or path == "/metrics":
                return self._send(
                    401, b'{"error":"unauthorized"}\n', "application/json",
                    headers={"WWW-Authenticate": 'Basic realm="otitbup"'},
                )
            return self._send(200, self.ui.login_page(next_url=path))
        self._identity = identity
        role = identity["role"] if identity else "admin"
        csrf = identity["token"] if identity and identity["via"] == "session" else ""

        # Audit page views (not assets/API/metrics/liveness) for compliance.
        if (
            self.ui.runstore is not None
            and not path.startswith(("/api/", "/metrics"))
            and "/artifact/" not in path
        ):
            import time
            try:
                self.ui.runstore.audit(
                    time.time(), "view",
                    actor=identity["username"] if identity else None,
                    role=role, detail=path,
                )
            except Exception:
                pass

        # Machine-readable endpoints.
        if path == "/metrics":
            from .metrics import metrics_text
            return self._send(200, metrics_text(
                self.ui.config, self.ui.store, self.ui.runstore,
                self.ui.blobstore,
            ).encode(), "text/plain; version=0.0.4; charset=utf-8")
        if path.startswith("/api/"):
            result = self._api(path)
            if result is None:
                return self._send(
                    404, b'{"error":"not found"}\n', "application/json"
                )
            import json
            return self._send(
                200, json.dumps(result, indent=2).encode() + b"\n",
                "application/json",
            )

        # Role-gated pages (admin only).
        from .auth import role_rank
        if path in ("/audit", "/users"):
            if self.ui.users and role_rank(role) < role_rank("admin"):
                return self._send(
                    403, _page("forbidden", "<p>admin role required</p>")
                )
            page = self.ui.audit() if path == "/audit" else self.ui.users_page(
                identity or {}, csrf
            )
            return self._send(200, page)

        content: bytes | None = None
        if path in ("/", "/index.html"):
            content = self.ui.index()
        elif path == "/health":
            content = self.ui.health(ctx={"role": role, "csrf": csrf})
        elif path == "/search":
            content = self.ui.search(
                query.get("q", [""])[0], site=query.get("site", [""])[0],
                zone=query.get("zone", [""])[0],
                case_sensitive=query.get("cs", [""])[0] == "1",
            )
        elif path == "/drift":
            content = self.ui.drift()
        elif path == "/strategy":
            content = self.ui.strategy()
        elif path == "/help":
            content = self.ui.help_page()
        elif path == "/policy":
            content = self.ui.policy()
        elif path == "/activity":
            content = self.ui.activity()
        elif path == "/retention":
            content = self.ui.retention()
        elif path == "/drivers":
            content = self.ui.drivers()
        elif path.startswith("/device/"):
            rest = path[len("/device/"):].strip("/")
            parts = rest.split("/")
            if len(parts) >= 5 and parts[3] == "commit":
                content = self.ui.commit("/".join(parts[:3]), parts[4])
            elif len(parts) >= 4 and parts[3] == "compare":
                content = self.ui.compare(
                    "/".join(parts[:3]),
                    query.get("base", [""])[0], query.get("head", ["HEAD"])[0],
                )
            elif len(parts) >= 5 and parts[3] == "artifact":
                result = self.ui.artifact(
                    "/".join(parts[:3]), "/".join(parts[4:])
                )
                if result is not None:
                    return self._send(200, result[0], result[1])
            elif len(parts) == 3:
                content = self.ui.device(
                    rest, ctx={"role": role, "csrf": csrf}
                )
        if content is not None:
            return self._send(200, content)
        self._send(404, _page("not found", "<p>not found</p>"))

    def do_POST(self):
        from urllib.parse import parse_qs
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        path = unquote(self.path.split("?", 1)[0])

        # Login/logout are their own auth flow.
        if path == "/login":
            return self._handle_login(form)
        if path == "/logout":
            return self._handle_logout()

        identity = self._identify()
        if self.ui.users and identity is None:
            return self._send(401, _page("unauthorized", "<p>sign in</p>"))
        actor = identity["username"] if identity else "anonymous"
        role = identity["role"] if identity else "admin"

        # CSRF: cookie-session POSTs must echo the session token.
        if identity and identity["via"] == "session":
            if form.get("csrf") != identity["token"]:
                return self._send(403, _page("forbidden", "<p>bad CSRF token</p>"))

        from .auth import role_rank
        ok, message, back = self._dispatch_post(path, form, actor, role, role_rank)
        if ok is None:
            return self._send(404, _page("not found", "<p>not found</p>"))
        return self._send(
            200, _result_page(ok, message, back)
        )

    def _handle_login(self, form: dict) -> None:
        from .auth import authenticate
        from .events import LOGIN
        next_url = form.get("next", "/")
        header = "Basic " + __import__("base64").b64encode(
            f"{form.get('username', '')}:{form.get('password', '')}".encode()
        ).decode()
        ident = authenticate(header, self.ui.users)
        if ident is None:
            return self._send(200, self.ui.login_page(
                error="invalid username or password", next_url=next_url))
        session = self.ui.sessions.create(ident["username"], ident["role"])
        self.ui.events.emit(
            LOGIN, f"login: {ident['username']} ({ident['role']})",
            actor=ident["username"], detail=ident["username"],
        )
        self._redirect(next_url if next_url.startswith("/") else "/", headers={
            "Set-Cookie":
                f"otitbup_session={session.token}; HttpOnly; SameSite=Strict; "
                "Path=/",
        })

    def _handle_logout(self) -> None:
        from .events import LOGOUT
        token = self._cookie_token()
        session = self.ui.sessions.destroy(token)
        if session:
            self.ui.events.emit(
                LOGOUT, f"logout: {session.username}",
                actor=session.username, detail=session.username,
            )
        self._redirect("/login", headers={
            "Set-Cookie":
                "otitbup_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
        })

    def _dispatch_post(self, path, form, actor, role, role_rank):
        """Returns (ok|None, message, back_url). ok is None for 404."""
        def need(level):
            return not self.ui.users or role_rank(role) >= role_rank(level)

        if path == "/reload":
            if not need("admin"):
                return False, "admin role required", "/"
            ok, msg = self.ui.reload_config()
            return ok, msg, "/"
        if path == "/report":
            if not need("operator"):
                return False, "operator role required", "/health"
            ok, msg = self.ui.action_report(
                fmt=form.get("format", "html"),
                sign=form.get("sign") == "1",
            )
            return ok, msg, "/health"
        if path == "/users/create":
            if not need("admin"):
                return False, "admin role required", "/users"
            ok, msg = self.ui.action_user_create(
                form.get("username", ""), form.get("password", ""),
                form.get("role", "viewer"), actor)
            return ok, msg, "/users"
        if path == "/users/delete":
            if not need("admin"):
                return False, "admin role required", "/users"
            ok, msg = self.ui.action_user_delete(form.get("username", ""), actor)
            return ok, msg, "/users"
        if path == "/users/passwd":
            if not need("admin"):
                return False, "admin role required", "/users"
            ok, msg = self.ui.action_user_passwd(
                form.get("username", ""), form.get("password", ""), actor)
            return ok, msg, "/users"
        if path.startswith("/device/"):
            parts = path[len("/device/"):].strip("/").split("/")
            if len(parts) == 4:
                qn, verb = "/".join(parts[:3]), parts[3]
                back = _device_link_name(qn)
                if not need("operator"):
                    return False, "operator role required", back
                if verb == "backup":
                    ok, msg = self.ui.action_backup(qn, actor)
                    return ok, msg, back
                if verb == "verify":
                    ok, msg = self.ui.action_verify(qn)
                    return ok, msg, back
                if verb == "note":
                    ok, msg = self.ui.action_note(
                        qn, form.get("commit", ""), form.get("text", ""), actor)
                    return ok, msg, back
                if verb == "baseline":
                    ok, msg = self.ui.action_baseline(
                        qn, form.get("commit", ""), actor)
                    return ok, msg, back
        return None, "", "/"


def serve(
    config: AppConfig, store: GitStore,
    host: str = "127.0.0.1", port: int = 8080,
    auth: dict | None = None,
    tls: dict | None = None,
    blobstore=None,
    runstore=None,
    events=None,
    config_path=None,
) -> None:
    from .auth import build_users
    secrets = None
    if config.secrets:
        from pathlib import Path as _P

        from .secrets import load_backend
        try:
            secrets = load_backend(
                config.secrets, base_dir=_P(config.data_dir).parent
            )
        except Exception as exc:
            log.warning("secrets backend unavailable for web actions: %s", exc)
    ui = WebUI(
        config, store, auth=auth, blobstore=blobstore, runstore=runstore,
        events=events, config_path=config_path, secrets=secrets,
    )
    if not ui.users and host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "web UI on %s has NO authentication configured — set "
            "webui.auth/users in the config (see `otitbup passwd`)", host,
        )
    server = ThreadingHTTPServer((host, port), partial(_Handler, ui))
    scheme = "http"
    if tls:
        import ssl
        if not tls.get("cert_file") or not tls.get("key_file"):
            raise ValueError(
                "webui.tls requires cert_file and key_file "
                "(generate a pair with `otitbup certgen`)"
            )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls["cert_file"], tls["key_file"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    log.info(
        "web UI listening on %s://%s:%d", scheme, host, server.server_port
    )
    server.serve_forever()