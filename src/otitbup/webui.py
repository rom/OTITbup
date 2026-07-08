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
        f"<nav><a href='/'>Devices</a><a href='/activity'>Activity</a>"
        f"<a href='/retention'>Retention</a>"
        f"<a href='/drivers'>Drivers</a></nav></header>"
        f"{body}</body></html>"
    ).encode()


def _device_link(device: Device) -> str:
    return f"/device/{quote(device.qualified_name)}"


class WebUI:
    def __init__(
        self, config: AppConfig, store: GitStore,
        auth: dict | None = None,
        blobstore=None,
    ):
        self.config = config
        self.store = store
        self.auth = auth
        self.blobstore = blobstore

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

    def device(self, qualified_name: str) -> bytes | None:
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

        history_rows = []
        for line in self.store.history(device, limit=30).splitlines():
            match = _HISTORY_LINE.match(line)
            if not match:
                continue
            chash, date, subject = match.groups()
            history_rows.append(
                f"<tr><td>{html.escape(date)}</td>"
                f"<td><a href='{_device_link(device)}/commit/{quote(chash)}'>"
                f"<code>{html.escape(chash)}</code></a></td>"
                f"<td>{html.escape(subject)}</td></tr>"
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
            f"{html.escape(policy_line)}</p>"
            "<h3>Artifacts (latest backup)</h3>"
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

    def _send(self, status: int, content: bytes,
              content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.ui.auth and not check_basic_auth(
            self.headers.get("Authorization"), self.ui.auth
        ):
            content = _page("unauthorized", "<p>unauthorized</p>")
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="otitbup"')
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        path = unquote(self.path.split("?", 1)[0])
        content: bytes | None = None
        if path in ("/", "/index.html"):
            content = self.ui.index()
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
            elif len(parts) >= 5 and parts[3] == "artifact":
                result = self.ui.artifact(
                    "/".join(parts[:3]), "/".join(parts[4:])
                )
                if result is not None:
                    return self._send(200, result[0], result[1])
            elif len(parts) == 3:
                content = self.ui.device(rest)
        if content is not None:
            return self._send(200, content)
        self._send(404, _page("not found", "<p>not found</p>"))


def serve(
    config: AppConfig, store: GitStore,
    host: str = "127.0.0.1", port: int = 8080,
    auth: dict | None = None,
    tls: dict | None = None,
    blobstore=None,
) -> None:
    if not auth and host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "web UI on %s has NO authentication configured — set "
            "webui.auth in the config (see `otitbup passwd`)", host,
        )
    ui = WebUI(config, store, auth=auth, blobstore=blobstore)
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