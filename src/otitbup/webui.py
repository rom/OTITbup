"""Read-only web UI (REQUIREMENTS.md sections 7 and 9).

A viewer, deliberately not an editor: the YAML config stays the source of
truth. Stdlib-only (http.server) so the appliance carries no web framework.
Binds to 127.0.0.1 by default; there is no authentication yet (open
question in REQUIREMENTS.md), so only expose it on trusted networks.

Routes:
    /                                   device dashboard
    /device/<site>/<zone>/<name>        backup history + latest diff
"""
from __future__ import annotations

import html
import logging
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from .gitstore import GitStore
from .models import AppConfig, Device

log = logging.getLogger("otitbup.webui")

_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 70rem;
       padding: 0 1rem; color: #1a1f24; background: #fff; }
h1 { font-size: 1.4rem; } h1 a { color: inherit; text-decoration: none; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .45rem .7rem; border-bottom: 1px solid #dde3e8; }
th { font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; color: #5b6570; }
a { color: #0b57d0; text-decoration: none; } a:hover { text-decoration: underline; }
pre { background: #f4f6f8; border: 1px solid #dde3e8; border-radius: 6px;
      padding: 1rem; overflow-x: auto; font-size: .85rem; line-height: 1.45; }
.badge { font-size: .75rem; padding: .1rem .5rem; border-radius: 999px;
         background: #e7f0e8; color: #1b5e20; }
.badge.never { background: #fdecea; color: #b3261e; }
.muted { color: #5b6570; font-size: .85rem; }
@media (prefers-color-scheme: dark) {
  body { background: #14181c; color: #e3e7eb; }
  th, td { border-color: #2c333a; } th { color: #98a2ad; }
  pre { background: #1b2127; border-color: #2c333a; }
  a { color: #8ab4f8; }
  .badge { background: #1d3320; color: #a5d6a7; }
  .badge.never { background: #3a2222; color: #f2b8b5; }
  .muted { color: #98a2ad; }
}
"""


def _page(title: str, body: str) -> bytes:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><h1><a href='/'>otitbup</a></h1>{body}</body></html>"
    ).encode()


class WebUI:
    def __init__(self, config: AppConfig, store: GitStore):
        self.config = config
        self.store = store

    def index(self) -> bytes:
        rows = []
        for device in self.config.all_devices():
            zone = self.config.find_zone(device)
            last = self.store.last_commit_info(device)
            badge = (
                f"<span class='badge'>{html.escape(last)}</span>"
                if last else "<span class='badge never'>never</span>"
            )
            link = html.escape(f"/device/{device.qualified_name}")
            rows.append(
                f"<tr><td><a href='{link}'>"
                f"{html.escape(device.qualified_name)}</a></td>"
                f"<td>{html.escape(device.driver)}</td>"
                f"<td>{html.escape(device.schedule)}</td>"
                f"<td>{html.escape(zone.maintenance_window or 'always')}</td>"
                f"<td>{badge}</td></tr>"
            )
        body = (
            "<table><tr><th>Device</th><th>Driver</th><th>Schedule</th>"
            "<th>Window</th><th>Last backup</th></tr>"
            + "".join(rows) + "</table>"
            f"<p class='muted'>{len(rows)} device(s) · read-only view · "
            "inventory is managed in the YAML config</p>"
        )
        return _page("otitbup — devices", body)

    def device(self, qualified_name: str) -> bytes | None:
        matches = [
            d for d in self.config.all_devices()
            if d.qualified_name == qualified_name
        ]
        if not matches:
            return None
        device: Device = matches[0]
        history = self.store.history(device, limit=30).strip()
        diff = self.store.last_diff(device).strip()
        body = (
            f"<h2>{html.escape(device.qualified_name)}</h2>"
            f"<p class='muted'>driver {html.escape(device.driver)} · "
            f"schedule {html.escape(device.schedule)}</p>"
            "<h3>History</h3>"
            f"<pre>{html.escape(history) or 'no backups yet'}</pre>"
            "<h3>Latest change</h3>"
            f"<pre>{html.escape(diff) or 'no backups yet'}</pre>"
        )
        return _page(f"otitbup — {device.qualified_name}", body)


class _Handler(BaseHTTPRequestHandler):
    def __init__(self, ui: WebUI, *args, **kwargs):
        self.ui = ui
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # route to logging, not stderr
        log.debug(fmt, *args)

    def _send(self, status: int, content: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path in ("/", "/index.html"):
            return self._send(200, self.ui.index())
        if path.startswith("/device/"):
            content = self.ui.device(path[len("/device/"):].strip("/"))
            if content is not None:
                return self._send(200, content)
        self._send(404, _page("not found", "<p>not found</p>"))


def serve(
    config: AppConfig, store: GitStore,
    host: str = "127.0.0.1", port: int = 8080,
) -> None:
    ui = WebUI(config, store)
    server = ThreadingHTTPServer((host, port), partial(_Handler, ui))
    log.info("web UI listening on http://%s:%d", host, server.server_port)
    server.serve_forever()
