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
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import yaml

from .gitstore import GitStore
from .models import AppConfig, Device

log = logging.getLogger("otitbup.webui")

_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
_HISTORY_LINE = re.compile(r"^(\w+)\s+(\S+ \S+ \S+)\s+(.*)$")

_STYLE = """
:root {
  --bg: #eef1f6; --surface: #ffffff; --text: #10151c; --muted: #5b6672;
  --border: #e4e9f0; --line: #eef2f7; --hover: #f3f6fb; --th-bg: #f7f9fc;
  --field: #ffffff; --accent: #2563eb; --accent-ink: #1d4ed8;
  --accent-soft: #e7efff; --teal: #0ea5a4;
  --ok: #167a37; --ok-soft: #e6f4ea; --warn: #b26a00; --warn-soft: #fbf0dd;
  --danger: #c02636; --danger-soft: #fdeaec; --danger-border: #f0c2c7;
  --radius: 12px;
  --shadow: 0 1px 2px rgba(16,24,40,.05), 0 12px 30px -18px rgba(16,24,40,.22);
  --shadow-sm: 0 1px 2px rgba(16,24,40,.06);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1015; --surface: #161b22; --text: #e6eaf0; --muted: #98a2ad;
    --border: #262d37; --line: #20262f; --hover: #1c222b; --th-bg: #1a2029;
    --field: #11151b; --accent: #6ea8fe; --accent-ink: #8bbcff;
    --accent-soft: #1b2740; --teal: #2dd4bf;
    --ok: #5cbd6c; --ok-soft: #16281a; --warn: #e0a54a; --warn-soft: #2e2413;
    --danger: #f2848d; --danger-soft: #2f1a1d; --danger-border: #5a2a2f;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 14px 34px -20px rgba(0,0,0,.75);
    --shadow-sm: 0 1px 2px rgba(0,0,0,.4);
  }
}
/* Selectable colour themes (via <html data-theme>); these override the
   OS-driven auto light/dark above. */
:root[data-theme="light"] {
  --bg:#eef1f6; --surface:#fff; --text:#10151c; --muted:#5b6672;
  --border:#e4e9f0; --line:#eef2f7; --hover:#f3f6fb; --th-bg:#f7f9fc;
  --field:#fff; --accent:#2563eb; --accent-ink:#1d4ed8; --accent-soft:#e7efff;
  --teal:#0ea5a4; --ok:#167a37; --ok-soft:#e6f4ea; --warn:#b26a00;
  --warn-soft:#fbf0dd; --danger:#c02636; --danger-soft:#fdeaec;
  --danger-border:#f0c2c7;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 12px 30px -18px rgba(16,24,40,.22);
  --shadow-sm:0 1px 2px rgba(16,24,40,.06);
}
:root[data-theme="dark"] {
  --bg:#0d1015; --surface:#161b22; --text:#e6eaf0; --muted:#98a2ad;
  --border:#262d37; --line:#20262f; --hover:#1c222b; --th-bg:#1a2029;
  --field:#11151b; --accent:#6ea8fe; --accent-ink:#8bbcff;
  --accent-soft:#1b2740; --teal:#2dd4bf; --ok:#5cbd6c; --ok-soft:#16281a;
  --warn:#e0a54a; --warn-soft:#2e2413; --danger:#f2848d; --danger-soft:#2f1a1d;
  --danger-border:#5a2a2f;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 14px 34px -20px rgba(0,0,0,.75);
  --shadow-sm:0 1px 2px rgba(0,0,0,.4);
}
:root[data-theme="sky"] {
  --bg:#e9f2fb; --surface:#fff; --text:#0f2233; --muted:#5a7085;
  --border:#d3e3f2; --line:#e6f0f9; --hover:#eef6fd; --th-bg:#edf5fc;
  --field:#fff; --accent:#0284c7; --accent-ink:#036ba1; --accent-soft:#d6ecfb;
  --teal:#0891b2; --ok:#0f766e; --ok-soft:#d7f0ec; --warn:#b45309;
  --warn-soft:#fbeddb; --danger:#be123c; --danger-soft:#fbe0e6;
  --danger-border:#f3c2ce;
  --shadow:0 1px 2px rgba(3,105,161,.08),0 14px 32px -18px rgba(3,105,161,.28);
  --shadow-sm:0 1px 2px rgba(3,105,161,.1);
}
:root[data-theme="desert"] {
  --bg:#f4ecdd; --surface:#fffdf7; --text:#3b2f1e; --muted:#8a7a5f;
  --border:#e6d8c0; --line:#f0e7d5; --hover:#f7f0e2; --th-bg:#f6efe0;
  --field:#fffdf7; --accent:#b45309; --accent-ink:#92400e;
  --accent-soft:#f3e3cb; --teal:#a16207; --ok:#5f7d1f; --ok-soft:#eaf0d6;
  --warn:#b7791f; --warn-soft:#f6ead0; --danger:#b23a2e; --danger-soft:#f6ddd7;
  --danger-border:#e6c3ba;
  --shadow:0 1px 2px rgba(120,80,20,.08),0 14px 32px -18px rgba(120,80,20,.3);
  --shadow-sm:0 1px 2px rgba(120,80,20,.1);
}
:root[data-theme="autumn"] {
  --bg:#1c1512; --surface:#271d18; --text:#f1e4d8; --muted:#b39d89;
  --border:#3a2c23; --line:#31251d; --hover:#31251d; --th-bg:#2f2219;
  --field:#1f1712; --accent:#ea7a3c; --accent-ink:#f2925c;
  --accent-soft:#3a271a; --teal:#c98a2b; --ok:#8bbf5a; --ok-soft:#25301a;
  --warn:#e0a54a; --warn-soft:#332616; --danger:#f0836f; --danger-soft:#331d18;
  --danger-border:#5a2f26;
  --shadow:0 1px 2px rgba(0,0,0,.35),0 14px 34px -20px rgba(0,0,0,.8);
  --shadow-sm:0 1px 2px rgba(0,0,0,.45);
}
:root[data-theme="spring"] {
  --bg:#eaf6ec; --surface:#fff; --text:#12271a; --muted:#5c7564;
  --border:#d3e8d8; --line:#e6f2e9; --hover:#eef8f0; --th-bg:#edf7ef;
  --field:#fff; --accent:#16a34a; --accent-ink:#15803d; --accent-soft:#d6f0dd;
  --teal:#0d9488; --ok:#15803d; --ok-soft:#d8f0de; --warn:#a16207;
  --warn-soft:#f3ecd0; --danger:#be123c; --danger-soft:#fbe0e6;
  --danger-border:#f0c2cd;
  --shadow:0 1px 2px rgba(16,90,40,.08),0 14px 32px -18px rgba(16,90,40,.26);
  --shadow-sm:0 1px 2px rgba(16,90,40,.1);
}
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
       margin: 0; color: var(--text); background: var(--bg);
       -webkit-font-smoothing: antialiased; line-height: 1.5; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* App bar */
.appbar { position: sticky; top: 0; z-index: 30; background: var(--surface);
          border-bottom: 1px solid var(--border); box-shadow: var(--shadow-sm); }
.bar { max-width: 80rem; margin: 0 auto; padding: .55rem 1.25rem;
       display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
h1 { font-size: 1.2rem; margin: 0; }
h1 a { color: inherit; text-decoration: none; }
.brand { display: inline-flex; align-items: center; gap: .55rem;
         text-decoration: none; }
.brand .mark { display: block; border-radius: 8px; flex: none;
     box-shadow: 0 1px 3px rgba(15,30,60,.28); }
.brand:hover { text-decoration: none; }
.wm { font-size: 1.3rem; font-weight: 800; letter-spacing: -.02em;
      color: var(--text); }
.wm .ot { color: var(--teal); } .wm .it { color: var(--accent); }
nav { display: flex; flex-wrap: wrap; gap: .12rem; margin-left: auto; }
nav a { padding: .34rem .6rem; border-radius: 8px; color: var(--muted);
        font-size: .855rem; font-weight: 500; white-space: nowrap; }
nav a:hover { background: var(--hover); color: var(--text);
              text-decoration: none; }
nav a.active { background: var(--accent-soft); color: var(--accent-ink);
               font-weight: 600; }
.who { display: inline-flex; align-items: center; gap: .35rem;
       color: var(--muted); font-size: .8rem; padding-left: .7rem;
       margin-left: .2rem; border-left: 1px solid var(--border);
       white-space: nowrap; }
.who b { color: var(--text); font-weight: 600; }
.theme-pick { margin-left: .4rem; padding: .22rem 1.3rem .22rem .5rem;
       font-size: .78rem; border: 1px solid var(--border); border-radius: 999px;
       background: var(--surface); color: var(--muted); cursor: pointer;
       text-transform: capitalize; }
.theme-pick:hover { color: var(--text); }
.who .role { font-size: .68rem; text-transform: uppercase; letter-spacing: .04em;
       background: var(--accent-soft); color: var(--accent-ink);
       padding: .05rem .4rem; border-radius: 999px; font-weight: 600; }

/* Content surface */
main { max-width: 80rem; margin: 1.5rem auto; padding: 1.6rem 1.9rem;
       background: var(--surface); border: 1px solid var(--border);
       border-radius: var(--radius); box-shadow: var(--shadow); }
main > :first-child { margin-top: 0; }
h2 { font-size: 1.2rem; font-weight: 700; letter-spacing: -.01em;
     margin: 1.8rem 0 .8rem; }
h3 { font-size: .98rem; font-weight: 650; margin: 1.4rem 0 .6rem; }
p { margin: .6rem 0; }

/* Tables */
table { border-collapse: separate; border-spacing: 0; width: 100%;
        border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
        margin: .6rem 0 1.3rem; font-size: .885rem; }
th, td { text-align: left; padding: .55rem .8rem;
         border-bottom: 1px solid var(--line); }
th { background: var(--th-bg); font-size: .72rem; font-weight: 600;
     text-transform: uppercase; letter-spacing: .045em; color: var(--muted); }
tbody tr:hover td, tr:hover td { background: var(--hover); }
tr:last-child td { border-bottom: none; }

pre { background: var(--th-bg); border: 1px solid var(--border);
      border-radius: 10px; padding: 1rem; overflow-x: auto; font-size: .84rem;
      line-height: 1.5; }
code { font-size: .85em; font-family: ui-monospace, SFMono-Regular, Menlo,
       monospace; }

/* Controls */
button, input, select, textarea { font: inherit; }
button { font-size: .855rem; font-weight: 600; padding: .42rem .85rem;
         border: 1px solid var(--border); border-radius: 8px;
         background: var(--surface); color: var(--text); cursor: pointer;
         transition: background .12s, filter .12s; }
button:hover { background: var(--hover); }
button[type=submit] { background: var(--accent); border-color: var(--accent);
         color: #fff; }
button[type=submit]:hover { filter: brightness(1.07); background: var(--accent); }
button.danger, button[formaction*="delete"] { background: var(--surface);
         color: var(--danger); border-color: var(--danger-border); }
button.danger:hover, button[formaction*="delete"]:hover {
         background: var(--danger-soft); filter: none; }
input, select, textarea { padding: .42rem .55rem; border: 1px solid var(--border);
         border-radius: 8px; background: var(--field); color: var(--text); }
input:focus, select:focus, textarea:focus { outline: none;
         border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }

/* Badges & status */
.badge { font-size: .72rem; font-weight: 600; padding: .12rem .55rem;
         border-radius: 999px; background: var(--ok-soft); color: var(--ok);
         white-space: nowrap; }
.badge.never { background: var(--danger-soft); color: var(--danger); }
.muted { color: var(--muted); font-size: .85rem; }
.sev-critical, .sev-high { color: var(--danger); font-weight: 600; }
.sev-medium { color: var(--warn); } .sev-low { color: var(--muted); }
.ok { color: var(--ok); } .miss { color: var(--danger); }
.strat { font-size: 1.02rem; padding: .3rem 0; }

/* Stat tiles */
.tiles { display: flex; gap: .9rem; flex-wrap: wrap; margin: 1rem 0 1.5rem; }
.tile { background: var(--surface); border: 1px solid var(--border);
        border-radius: 12px; padding: .85rem 1.15rem; min-width: 8.5rem;
        box-shadow: var(--shadow-sm); }
.tile b { display: block; font-size: 1.7rem; font-weight: 700;
          letter-spacing: -.02em; }
.tile span { font-size: .72rem; text-transform: uppercase;
             letter-spacing: .045em; color: var(--muted); font-weight: 600; }

#filter { margin: 0 0 1rem; padding: .5rem .75rem; width: 22rem;
          max-width: 100%; }

.zone-head td { background: var(--th-bg); font-weight: 700; font-size: .74rem;
                text-transform: uppercase; letter-spacing: .045em;
                color: var(--muted); }

/* Help tooltip */
.help { position: relative; display: inline-flex; align-items: center;
        justify-content: center; cursor: help; width: 16px; height: 16px;
        border-radius: 50%; background: var(--border); color: var(--muted);
        font-size: 11px; font-weight: 700; margin-left: .3rem; }
.help .pop { visibility: hidden; opacity: 0; position: absolute; z-index: 40;
        left: 50%; transform: translateX(-50%); bottom: 150%; width: 15rem;
        background: #10151c; color: #f0f3f6; padding: .5rem .7rem;
        border-radius: 8px; font-size: .8rem; font-weight: normal;
        line-height: 1.45; text-align: left; transition: opacity .1s;
        box-shadow: 0 6px 20px rgba(0,0,0,.35); }
.help:hover .pop, .help:focus .pop { visibility: visible; opacity: 1; }

/* Config editor */
details { margin: .4rem 0; border: 1px solid var(--border); border-radius: 10px;
          padding: .3rem .8rem; background: var(--surface); }
details[open] { box-shadow: var(--shadow-sm); }
summary { cursor: pointer; padding: .4rem .1rem; font-weight: 600;
          font-size: .9rem; }
details table.cfg { border: none; margin: .4rem 0; }
table.cfg td { border: none; padding: .25rem .5rem; }
table.cfg input { width: 22rem; max-width: 100%; }
.cfg-inline { display: flex; flex-wrap: wrap; gap: .55rem; align-items: end;
        margin: .5rem 0; padding: .7rem .8rem; border: 1px solid var(--border);
        border-radius: 10px; background: var(--th-bg); }
label.inl { display: flex; flex-direction: column; gap: .2rem; font-size: .68rem;
        color: var(--muted); text-transform: uppercase; letter-spacing: .04em;
        font-weight: 600; }
label.inl input, label.inl select { width: 8.5rem; font-size: .85rem;
        padding: .35rem .45rem; text-transform: none; letter-spacing: normal;
        font-weight: 400; }

/* In-app docs */
.doc { line-height: 1.65; max-width: 52rem; }
.doc h1 { display: block; font-size: 1.6rem; margin: .2rem 0 1rem; }
.doc h2 { border-bottom: 1px solid var(--border); padding-bottom: .3rem; }
.doc blockquote { border-left: 3px solid var(--accent); margin: .9rem 0;
        padding: .3rem 0 .3rem 1rem; color: var(--muted); }
.doc li { margin: .25rem 0; } .doc table { margin: 1rem 0; }
.doc pre { background: var(--th-bg); }

@media (max-width: 640px) {
  .bar { padding: .5rem .9rem; }
  main { margin: .9rem .6rem; padding: 1.1rem 1rem; border-radius: 10px; }
  nav { margin-left: 0; width: 100%; }
}
"""

# Brand mark: a git-commit graph (three commits on a spine plus a branch) on
# a teal→blue badge — "versioned backups" for a git-backed backup tool. The
# same artwork is the SVG favicon. Scales cleanly from 16px to any size.
def _logo_mark(size: int = 30) -> str:
    return (
        f"<svg class='mark' viewBox='0 0 32 32' width='{size}' height='{size}' "
        "role='img' aria-label='otitbup' xmlns='http://www.w3.org/2000/svg'>"
        "<defs><linearGradient id='obG' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0' stop-color='#12b8a6'/>"
        "<stop offset='1' stop-color='#1f63d8'/></linearGradient></defs>"
        "<rect x='1' y='1' width='30' height='30' rx='8' fill='url(#obG)'/>"
        "<rect x='1' y='1' width='30' height='15' rx='8' fill='#fff' "
        "opacity='.08'/>"
        "<g fill='none' stroke='#fff' stroke-width='2.2' stroke-linecap='round'>"
        "<path d='M10.5 6.5 V 25.5'/>"
        "<path d='M10.5 16 C 10.5 11 16 9.5 21.5 9.5'/></g>"
        "<g fill='#fff'>"
        "<circle cx='10.5' cy='6.5' r='2.8'/>"
        "<circle cx='10.5' cy='16' r='2.8'/>"
        "<circle cx='10.5' cy='25.5' r='2.8'/>"
        "<circle cx='21.5' cy='9.5' r='2.8'/></g></svg>"
    )


_FAVICON_SVG = _logo_mark(32).encode()

_BRAND = (
    f"<a class='brand' href='/'>{_logo_mark(30)}"
    "<span class='wm'><span class='ot'>ot</span><span class='it'>it</span>"
    "bup</span></a>"
)


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


# Per-request context (each request runs in its own thread) so _page can
# render the signed-in user and the configured theme without threading them
# through every page method.
_CTX = threading.local()


def _who_chip() -> str:
    identity = getattr(_CTX, "identity", None)
    if not identity:
        return ""
    user = html.escape(str(identity.get("username", "")))
    role = html.escape(str(identity.get("role", "")))
    return (f"<span class='who'>signed in as <b>{user}</b>"
            f"<span class='role'>{role}</span></span>")


def _theme_picker() -> str:
    """A menu-bar dropdown that sets the current user's theme preference."""
    cur = getattr(_CTX, "theme", None) or "auto"
    if cur not in _THEMES:
        cur = "auto"
    options = "".join(
        f"<option value='{t}'{' selected' if t == cur else ''}>{t}</option>"
        for t in _THEMES)
    return (
        "<select class='theme-pick' title='Colour theme' aria-label='theme' "
        "onchange=\"location.href='/theme?set='+this.value+'&next='+"
        "encodeURIComponent(location.pathname+location.search)\">"
        f"{options}</select>"
    )


def _page(title: str, body: str) -> bytes:
    theme = getattr(_CTX, "theme", None) or ""
    theme_attr = (f" data-theme='{html.escape(theme)}'"
                  if theme and theme != "auto" else "")
    return (
        f"<!doctype html><html{theme_attr}><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<link rel='icon' type='image/svg+xml' href='/favicon.svg'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><header class='appbar'><div class='bar'><h1>{_BRAND}</h1>"
        f"<nav><a href='/dashboard'>Dashboard</a><a href='/'>Devices</a>"
        f"<a href='/health'>Health</a>"
        f"<a href='/search'>Search</a><a href='/drift'>Drift</a>"
        f"<a href='/policy'>Policy</a>"
        f"<a href='/retention'>Retention</a><a href='/strategy'>Strategy</a>"
        f"<a href='/drivers'>Drivers</a>"
        f"<a href='/activity'>Backup log</a><a href='/audit'>Audit log</a>"
        f"<a href='/users'>Users</a><a href='/config'>Config</a>"
        f"<a href='/help'>Help</a>"
        f"<a href='/logout'>Logout</a>{_theme_picker()}"
        f"{_who_chip()}</nav></div></header>"
        f"<main>{body}</main>"
        "<script>(function(){var p=location.pathname;"
        "document.querySelectorAll('nav a').forEach(function(a){"
        "var h=a.getAttribute('href');if(h!=='/logout'&&"
        "(h==='/'?p==='/':p.indexOf(h)===0))a.classList.add('active');});})();"
        "</script>"
        f"</body></html>"
    ).encode()


# Slug -> docs filename for the in-app manual viewer.
_DOC_FILES = {
    "usage": "USAGE.md",
    "configuration": "CONFIGURATION.md",
    "faq": "FAQ.md",
    "releasenotes": "RELEASENOTES.md",
    "architecture": "ARCHITECTURE.md",
    "sitecollector": "SITECOLLECTOR.md",
}


def _find_doc(slug: str):
    """Locate a bundled Markdown manual by slug. Returns a Path or None.
    Searches the OTITBUP_DOCS_DIR override, then the docs/ directory in the
    source tree relative to this package, then ./docs."""
    import os
    filename = _DOC_FILES.get(slug)
    if filename is None:
        return None
    candidates = []
    env = os.environ.get("OTITBUP_DOCS_DIR")
    if env:
        candidates.append(Path(env) / filename)
    # src/otitbup/webui.py -> repo root is parents[2]; docs/ sits there.
    here = Path(__file__).resolve()
    candidates.append(here.parents[2] / "docs" / filename)
    candidates.append(Path("docs") / filename)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _device_link(device: Device) -> str:
    return f"/device/{quote(device.qualified_name)}"


def _device_link_name(qualified_name: str) -> str:
    return f"/device/{quote(qualified_name)}"


def _csrf(token: str) -> str:
    return f"<input type='hidden' name='csrf' value='{html.escape(token)}'>"


# Admin-editable global config sections. Each field is
# (dotted-path-within-section, label, type). type: str | int | bool.
# Selectable colour themes for the web UI (applied via <html data-theme>).
_THEMES = ("auto", "light", "dark", "sky", "desert", "autumn", "spring")

# Admin-editable global config. Each field is
# (dotted-path, label, type, example) where type is
# str | int | float | bool | csv | "choice:a|b|c", and example is the
# placeholder / default shown in an empty field (or the default choice).
# `id` disambiguates two forms that write the same config section.
_SETTINGS_FORMS = [
    {"section": "offsite", "title": "Offsite copy (external server / cloud)",
     "fields": [
         ("transport", "Transport", "choice:file|sftp|s3", "s3"),
         ("interval_days", "Auto-push every N days (0=off)", "float", "1"),
         ("key_file", "Encryption key file", "str", "/etc/otitbup/offsite.key"),
         ("dir", "file: directory / mount", "str", "/mnt/offsite/otitbup"),
         ("host", "sftp: host", "str", "backup.example.com"),
         ("port", "sftp: port", "int", "22"),
         ("username", "sftp: username", "str", "otitbup"),
         ("ssh_key_file", "sftp: SSH key file", "str",
          "/etc/otitbup/id_ed25519"),
         ("path", "sftp: remote path", "str", "/srv/otitbup"),
         ("bucket", "s3: bucket", "str", "ot-backups"),
         ("prefix", "s3: prefix", "str", "otitbup/"),
         ("region", "s3: region", "str", "eu-central-1"),
         ("endpoint", "s3: endpoint", "str",
          "https://s3.eu-central-1.amazonaws.com"),
         ("access_key", "s3: access key", "str", "AKIA..."),
         ("secret_key_file", "s3: secret key file", "str",
          "/etc/otitbup/s3.secret"),
     ]},
    {"id": "webui", "section": "webui", "title": "Web UI",
     "fields": [
         ("host", "Bind host", "str", "127.0.0.1"),
         ("port", "Bind port", "int", "8080"),
         ("theme", "Default colour theme (per-user overrides)",
          "choice:" + "|".join(_THEMES), "auto"),
         ("tls.cert_file", "TLS certificate file", "str", "webui-cert.pem"),
         ("tls.key_file", "TLS key file", "str", "webui-key.pem"),
     ]},
    {"id": "sso", "section": "webui", "title": "Single sign-on (SSO)",
     "fields": [
         ("trusted_header", "Trusted user header (from auth proxy)", "str",
          "X-Forwarded-User"),
         ("trusted_role_header", "Trusted role header", "str",
          "X-Forwarded-Role"),
         ("trusted_default_role", "Default role for SSO users",
          "choice:viewer|operator|admin", "viewer"),
     ]},
    {"section": "ldap", "title": "LDAP / Active Directory login (SSO)",
     "fields": [
         ("url", "LDAP URL", "str", "ldaps://dc.example.com"),
         ("user_dn_template", "User DN template", "str",
          "uid={username},ou=people,dc=example,dc=com"),
         ("group_base", "Group search base", "str",
          "ou=groups,dc=example,dc=com"),
         ("default_role", "Default role", "choice:viewer|operator|admin",
          "viewer"),
     ]},
    {"section": "events", "title": "Events (syslog / SNMP traps)",
     "fields": [
         ("syslog.address", "syslog address", "str", "10.0.0.1"),
         ("syslog.port", "syslog port", "int", "514"),
         ("syslog.protocol", "syslog transport", "choice:udp|tcp|tls", "udp"),
         ("syslog.facility", "syslog facility", "str", "local0"),
         ("syslog.cafile", "syslog TLS CA bundle (tls only)", "str",
          "/etc/ssl/certs/ca-bundle.crt"),
         ("snmp_trap.address", "SNMP trap address", "str", "10.0.0.2"),
         ("snmp_trap.port", "SNMP trap port", "int", "162"),
         ("snmp_trap.community", "SNMP community", "str", "public"),
         ("snmp_trap.enterprise_oid", "enterprise OID", "str",
          "1.3.6.1.4.1.99999"),
     ]},
    {"section": "logging", "title": "Logging",
     "fields": [
         ("level", "Level", "choice:debug|info|warning|error", "info"),
         ("format", "Format", "choice:text|json", "text"),
         ("file", "Log file (rotates)", "str",
          "/var/log/otitbup/otitbup.log"),
         ("max_bytes", "Max bytes", "int", "10485760"),
         ("backups", "Rotations kept", "int", "5"),
     ]},
    {"section": "netbox", "title": "Integration: NetBox",
     "fields": [("url", "URL", "str", "https://netbox.example.com"),
                ("token", "API token", "str", "0123456789abcdef")]},
    {"section": "tickets", "title": "Integration: ticketing",
     "fields": [
         ("backend", "Backend", "choice:servicenow|jira|rt|generic",
          "servicenow"),
         ("url", "URL", "str", "https://example.service-now.com"),
         ("username", "Username", "str", "svc-otitbup"),
         ("password", "Password / API token", "str", ""),
     ]},
    {"section": "encryption", "title": "Encryption at rest",
     "fields": [
         ("blob_key_file", "Blob-store key file", "str",
          "/etc/otitbup/blob.key"),
         ("compress", "Compress blobs before encrypting", "bool", ""),
     ]},
    {"section": "capture", "title": "Capture-quality guards",
     "fields": [
         ("min_bytes", "Reject captures smaller than (bytes)", "int", "512"),
         ("expect_match", "Required content (regex)", "str", "hostname"),
     ]},
    {"section": "integrity", "title": "Integrity scrubbing",
     "fields": [
         ("interval_days", "Scrub every N days (0=off)", "float", "7"),
         ("all_commits", "Verify whole history", "bool", ""),
         ("fsck", "Run git fsck", "bool", ""),
         ("signatures", "Verify commit signatures", "bool", ""),
     ]},
    {"section": "rehearsal", "title": "Scheduled restore rehearsals",
     "fields": [("interval_days", "Rehearse every N days (0=off)", "float",
                 "30")]},
    {"section": "git", "title": "Git remote & signed history",
     "fields": [
         ("remote", "Push remote", "str",
          "git@gitlab.example.com:ot/backups.git"),
         ("push", "Push after each backup", "bool", ""),
         ("sign.key_file", "Commit signing key (SSH)", "str",
          "/etc/otitbup/commit-signing-key"),
     ]},
    {"section": "secrets", "title": "Secrets backend",
     "fields": [
         ("backend", "Backend",
          "choice:plainfile|encryptedfile|vault|cyberark", "encryptedfile"),
         ("path", "File path", "str", "secrets.yml"),
         ("key_file", "Encryption key file", "str", "otitbup.key"),
         ("url", "Vault/CyberArk URL", "str", "https://vault.example:8200"),
         ("mount", "Vault mount", "str", "secret"),
         ("token_file", "Vault token file", "str", "vault.token"),
     ]},
    {"section": "retention", "title": "Retention (global defaults)",
     "fields": [
         ("keep_versions", "Keep newest N backups", "int", "30"),
         ("keep_days", "Keep backups newer than N days", "int", "365"),
         ("large_file_threshold", "Blob offload threshold (bytes)", "int",
          "1048576"),
         ("lock_days", "Retention lock: keep last N days (WORM)", "int", "90"),
     ]},
    {"section": "alerts", "title": "Alerts",
     "fields": [
         ("stale_days", "Stale after N days (0=off)", "int", "7"),
         ("min_interval", "Rate-limit window (s)", "int", "3600"),
         ("webhooks", "Webhook URLs (comma-separated)", "csv",
          "https://chat.example.com/hook"),
         ("email.smtp_host", "SMTP host", "str", "mail.example.com"),
         ("email.from", "From address", "str", "otitbup@example.com"),
         ("email.to", "Recipients (comma-separated)", "csv",
          "ot-team@example.com"),
     ]},
    {"section": "retry", "title": "Retry on transient failures",
     "fields": [
         ("attempts", "Attempts per device (1=no retry)", "int", "3"),
         ("backoff", "Backoff seconds (doubles each retry)", "float", "2.0"),
     ]},
    {"section": "hooks", "title": "Pre/post hooks (global)",
     "fields": [
         ("pre", "Pre-backup shell command", "str",
          "/usr/local/bin/notify start $OTITBUP_DEVICE"),
         ("post", "Post-backup shell command", "str",
          "/usr/local/bin/notify done $OTITBUP_DEVICE $OTITBUP_OK"),
     ]},
    {"section": "anomaly", "title": "Anomaly detection",
     "fields": [
         ("enabled", "Enabled", "bool", ""),
         ("sigma", "Duration z-score threshold", "float", "3.0"),
         ("duration_floor", "Ignore runs faster than (s)", "float", "5.0"),
         ("change_window", "Change-storm window", "int", "5"),
         ("change_recent", "Recent change-rate trigger", "float", "0.8"),
         ("change_baseline", "Max baseline change-rate", "float", "0.2"),
         ("flap_window", "Flapping window", "int", "6"),
         ("flap_transitions", "Flapping transitions", "int", "3"),
         ("trend_window", "Slow-trend window", "int", "5"),
         ("trend_ratio", "Slow-trend multiplier", "float", "2.0"),
         ("size_drop", "Size-drop fraction of median", "float", "0.5"),
     ]},
    {"section": "housekeeping", "title": "Git housekeeping",
     "fields": [
         ("gc_interval_days", "git gc every N days (0=off)", "int", "7"),
         ("gc_aggressive", "Aggressive gc", "bool", ""),
     ]},
    {"section": "desired", "title": "Config-as-code (desired state)",
     "fields": [
         ("dir", "Desired-config directory", "str", "./desired"),
         ("strip_trailing_ws", "Ignore trailing whitespace", "bool", ""),
     ]},
    {"section": "reports", "title": "Scheduled compliance reports",
     "fields": [
         ("interval", "Interval (e.g. 7d; empty=off)", "str", "7d"),
         ("period_days", "Report period (days)", "int", "30"),
         ("out", "Output path", "str", "compliance-report.html"),
     ]},
    {"section": "strategy", "title": "3-2-1 strategy",
     "fields": [
         ("offsite", "Git remote is genuinely off-site", "bool", ""),
         ("offline.path", "Offline export path", "str",
          "/mnt/usb/otitbup-export.tar.gz"),
         ("offline.max_age_days", "Offline max age (days)", "int", "7"),
     ]},
    {"section": "federation", "title": "Federation (central roll-up)",
     "fields": [("role", "Role", "str", "central")]},
]


def _form_id(spec: dict) -> str:
    return spec.get("id", spec["section"])


def _dig(data: dict, dotted: str):
    """Read a dotted path (a.b.c) out of a nested dict; '' if absent."""
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
        if cur is None:
            return ""
    return cur


def _nest(dotted: str, value, into: dict) -> None:
    """Set into[a][b][c] = value for a dotted path 'a.b.c'."""
    parts = dotted.split(".")
    cur = into
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _labeled(name: str, value, placeholder: str) -> str:
    """A small labelled text input for the inline inventory forms."""
    safe = html.escape(str(value)) if value not in (None, "") else ""
    return (f"<label class='inl'>{html.escape(name)}"
            f"<input name='{name}' value='{safe}' "
            f"placeholder='{html.escape(placeholder)}'></label>")


def _driver_select(name: str, current: str, drivers: list[str]) -> str:
    opts = ["<option value=''>— driver —</option>"]
    for drv in drivers:
        sel = " selected" if drv == current else ""
        opts.append(f"<option value='{html.escape(drv)}'{sel}>"
                    f"{html.escape(drv)}</option>")
    return (f"<label class='inl'>driver<select name='{name}'>"
            + "".join(opts) + "</select></label>")


def _retention_from_form(form: dict) -> dict:
    """Pull retention integer fields out of a submitted form (blank -> unset)."""
    out: dict = {}
    for key in ("keep_versions", "keep_days", "large_file_threshold"):
        val = form.get(key, "")
        if val not in ("", None):
            try:
                out[key] = int(val)
            except ValueError:
                pass
    return out


def _inventory_fields(form: dict, is_device: bool) -> dict:
    """Build the fields dict for a device or zone edit from a submitted form.
    Blank values delete the key; retention is nested."""
    fields: dict = {}
    if is_device:
        keys = ("name", "driver", "address", "schedule", "credentials", "guid")
    else:
        keys = ("maintenance_window", "timezone")
    for key in keys:
        if key in form:
            fields[key] = form.get(key, "")
    if not is_device and form.get("max_concurrent"):
        try:
            fields["max_concurrent"] = int(form["max_concurrent"])
        except ValueError:
            pass
    fields["retention"] = _retention_from_form(form)
    return fields


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
        # Attach an in-process broadcaster so the live-activity page can
        # stream events over SSE. Harmless on a NullEventBus (never emits).
        if getattr(self.events, "broadcaster", None) is None:
            from .events import Broadcaster
            try:
                self.events.broadcaster = Broadcaster()
            except AttributeError:
                pass
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
        for rec in base.values():
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

    # -------------------------------------------------- config editor

    def _config_editable(self) -> bool:
        return bool(self.config_path)

    def config_page(self, csrf: str) -> bytes:
        """Admin config editor: global settings plus per-site/zone/device
        inventory editing. Writes back to the YAML config (validated,
        with a .bak), then reloads."""
        from . import configedit
        if not self._config_editable():
            return _page("otitbup — configuration",
                         "<h2>Configuration</h2><p class='sev-high'>The web "
                         "process was started without <code>-c &lt;config&gt;"
                         "</code>, so it cannot locate the config file to "
                         "edit.</p>")
        try:
            raw = configedit.load_raw(self.config_path)
        except Exception as exc:
            return _page("otitbup — configuration",
                         f"<h2>Configuration</h2><p class='sev-high'>cannot "
                         f"read config: {html.escape(str(exc))}</p>")

        # Global settings sections.
        sections = []
        for spec in _SETTINGS_FORMS:
            current = raw.get(spec["section"]) or {}
            inputs = []
            for field in spec["fields"]:
                dotted, label, ftype = field[0], field[1], field[2]
                example = field[3] if len(field) > 3 else ""
                value = _dig(current, dotted)
                inputs.append(self._config_input(
                    dotted, label, ftype, value, example))
            sections.append(
                f"<details><summary>{html.escape(spec['title'])}</summary>"
                f"<form method='post' action='/config/global'>{_csrf(csrf)}"
                f"<input type='hidden' name='section' "
                f"value='{_form_id(spec)}'>"
                "<table class='cfg'>" + "".join(inputs) + "</table>"
                "<button type='submit'>Save</button></form></details>"
            )

        body = [
            "<h2>Configuration</h2>",
            "<p class='muted'>Admin-only. Changes are validated, written to "
            "the config file (comments preserved; previous kept as "
            "<code>.bak</code>) and reloaded. "
            "<a href='/users'>User management &rarr;</a></p>",
            "<h3>Global settings</h3>",
            *sections,
            self._federation_editor(raw, csrf),
            self._inventory_editor(raw, csrf),
        ]
        return _page("otitbup — configuration", "".join(body))

    def _federation_editor(self, raw: dict, csrf: str) -> str:
        """Add/remove federation collectors (the central roll-up list)."""
        collectors = (raw.get("federation") or {}).get("collectors") or []
        rows = []
        for c in collectors:
            name = c.get("name", "")
            rows.append(
                "<form method='post' action='/config/collector-delete' "
                f"class='cfg-inline'>{_csrf(csrf)}"
                f"<input type='hidden' name='name' value='{html.escape(name)}'>"
                f"<b>{html.escape(name)}</b> "
                f"<span class='muted'>{html.escape(str(c.get('url', '')))}</span>"
                "<button type='submit' class='danger' "
                "onclick=\"return confirm('Remove collector?')\">Remove"
                "</button></form>")
        add = (
            "<form method='post' action='/config/collector-add' "
            f"class='cfg-inline'>{_csrf(csrf)}add collector: "
            + _labeled("name", "", "site name")
            + _labeled("url", "", "https://host:8443")
            + _labeled("token", "", "otb_… (or token_file)")
            + _labeled("token_file", "", "/etc/otitbup/x.token")
            + _labeled("verify_tls", "", "true | /path/ca.pem | false")
            + "<button type='submit'>Add collector</button></form>"
        )
        return ("<h3>Federation collectors</h3>"
                "<p class='muted'>The central appliance polls each "
                "collector's <code>/api/status</code>. See "
                "<a href='/help/sitecollector'>SITECOLLECTOR</a>.</p>"
                + "".join(rows) + add)

    def _config_input(self, name: str, label: str, ftype: str, value,
                      example: str = "") -> str:
        if ftype == "csv" and isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        safe = html.escape(str(value)) if value not in (None, "") else ""
        ph = f" placeholder='{html.escape(example)}'" if example else ""
        if ftype == "bool":
            checked = " checked" if value in (True, "true", "1", 1) else ""
            field = (f"<input type='checkbox' name='{name}' value='1'"
                     f"{checked}>")
        elif ftype.startswith("choice:"):
            opts = ftype.split(":", 1)[1].split("|")
            cur = str(value) if value not in (None, "") else example
            options = "".join(
                f"<option value='{html.escape(o)}'"
                f"{' selected' if o == cur else ''}>{html.escape(o)}</option>"
                for o in opts)
            field = f"<select name='{name}'>{options}</select>"
        elif ftype in ("int", "float"):
            field = (f"<input name='{name}' value='{safe}'{ph} "
                     "inputmode='numeric'>")
        else:
            field = f"<input name='{name}' value='{safe}'{ph}>"
        return (f"<tr><td><label>{html.escape(label)}</label></td>"
                f"<td>{field}</td></tr>")

    def _inventory_editor(self, raw: dict, csrf: str) -> str:
        from .drivers import available_drivers
        drivers = available_drivers()
        out = ["<h3>Inventory</h3>"]
        for site in raw.get("sites", []) or []:
            sname = site.get("name", "")
            out.append(f"<details><summary><b>site: "
                       f"{html.escape(sname)}</b></summary>")
            out.append(self._retention_form(
                "/config/site", {"site": sname}, site.get("retention") or {},
                csrf, extra=[]))
            for zone in site.get("zones", []) or []:
                zname = zone.get("name", "")
                out.append(f"<details><summary>zone: "
                           f"{html.escape(zname)}</summary>")
                out.append(self._zone_form(sname, zname, zone, csrf))
                for dev in zone.get("devices", []) or []:
                    out.append(self._device_form(
                        sname, zname, dev, drivers, csrf))
                out.append(self._device_add_form(sname, zname, drivers, csrf))
                out.append("</details>")
            out.append(self._zone_add_form(sname, csrf))
            out.append("</details>")
        out.append(self._site_add_form(csrf))
        return "".join(out)

    def _zone_form(self, site, zone, z, csrf) -> str:
        r = z.get("retention") or {}
        return (
            "<form method='post' action='/config/zone' class='cfg-inline'>"
            + _csrf(csrf)
            + f"<input type='hidden' name='site' value='{html.escape(site)}'>"
            + f"<input type='hidden' name='zone' value='{html.escape(zone)}'>"
            + _labeled("maintenance_window",
                       z.get("maintenance_window") or "", "18:00-06:00")
            + _labeled("timezone", z.get("timezone") or "", "Europe/Berlin")
            + _labeled("max_concurrent", z.get("max_concurrent") or "", "1")
            + _labeled("keep_versions", r.get("keep_versions") or "", "0")
            + _labeled("keep_days", r.get("keep_days") or "", "0")
            + "<button type='submit'>Save zone</button></form>"
        )

    def _device_form(self, site, zone, d, drivers, csrf) -> str:
        r = d.get("retention") or {}
        name = d.get("name", "")
        return (
            "<form method='post' action='/config/device' class='cfg-inline'>"
            + _csrf(csrf)
            + f"<input type='hidden' name='site' value='{html.escape(site)}'>"
            + f"<input type='hidden' name='zone' value='{html.escape(zone)}'>"
            + f"<input type='hidden' name='orig' value='{html.escape(name)}'>"
            + f"<b>{html.escape(name)}</b> "
            + _labeled("name", name, "name")
            + _driver_select("driver", d.get("driver", ""), drivers)
            + _labeled("address", d.get("address") or "", "10.0.0.1")
            + _labeled("schedule", d.get("schedule") or "", "12h or cron")
            + _labeled("credentials", d.get("credentials") or "", "secret key")
            + _labeled("guid", d.get("guid") or "", "auto")
            + _labeled("keep_versions", r.get("keep_versions") or "", "0")
            + _labeled("keep_days", r.get("keep_days") or "", "0")
            + "<button type='submit'>Save</button>"
            + "<button type='submit' formaction='/config/device-delete' "
            "class='danger' "
            "onclick=\"return confirm('Delete device?')\">Delete</button>"
            "</form>"
        )

    def _device_add_form(self, site, zone, drivers, csrf) -> str:
        return (
            "<form method='post' action='/config/device-add' class='cfg-inline'>"
            + _csrf(csrf)
            + f"<input type='hidden' name='site' value='{html.escape(site)}'>"
            + f"<input type='hidden' name='zone' value='{html.escape(zone)}'>"
            + "add device: " + _labeled("name", "", "name")
            + _driver_select("driver", "", drivers)
            + _labeled("address", "", "address")
            + _labeled("schedule", "", "12h")
            + _labeled("credentials", "", "secret key")
            + "<button type='submit'>Add device</button></form>"
        )

    def _zone_add_form(self, site, csrf) -> str:
        return (
            "<form method='post' action='/config/zone-add' class='cfg-inline'>"
            + _csrf(csrf)
            + f"<input type='hidden' name='site' value='{html.escape(site)}'>"
            + "add zone: " + _labeled("zone", "", "zone name")
            + "<button type='submit'>Add zone</button></form>"
        )

    def _site_add_form(self, csrf) -> str:
        return (
            "<form method='post' action='/config/site-add' class='cfg-inline'>"
            + _csrf(csrf) + "add site: " + _labeled("site", "", "site name")
            + "<button type='submit'>Add site</button></form>"
        )

    def _retention_form(self, action, hidden, r, csrf, extra) -> str:
        h = "".join(
            f"<input type='hidden' name='{k}' value='{html.escape(str(v))}'>"
            for k, v in hidden.items())
        return (
            f"<form method='post' action='{action}' class='cfg-inline'>"
            + _csrf(csrf) + h
            + "retention: " + _labeled("keep_versions", r.get("keep_versions")
                                       or "", "0")
            + _labeled("keep_days", r.get("keep_days") or "", "0")
            + _labeled("large_file_threshold",
                       r.get("large_file_threshold") or "", "1048576")
            + "<button type='submit'>Save</button></form>"
        )

    # ----------------------------------------------- config edit actions

    def _do_config(self, fn, actor: str, back: str = "/config"):
        """Run a configedit mutation, then reload. Returns (ok, msg, back)."""
        from . import configedit
        if not self._config_editable():
            return False, "config path unknown (started without -c)", back
        try:
            detail = fn()
        except configedit.ConfigEditError as exc:
            return False, str(exc), back
        except Exception as exc:
            return False, f"edit failed: {exc}", back
        ok, msg = self.reload_config()
        if self.runstore is not None:
            import time
            try:
                self.runstore.audit(time.time(), "config.edit", actor=actor,
                                    detail=detail or "")
            except Exception:
                pass
        return True, (detail or msg), back

    def action_config_global(self, section: str, form: dict, actor: str):
        from . import configedit
        # `section` is the form id (disambiguates the two webui forms).
        spec = next((s for s in _SETTINGS_FORMS if _form_id(s) == section), None)
        if spec is None:
            return False, "unknown settings section", "/config"
        real_section = spec["section"]
        fields: dict = {}
        for field in spec["fields"]:
            dotted, ftype = field[0], field[2]
            if ftype == "bool":
                _nest(dotted, form.get(dotted) == "1", fields)
                continue
            raw_val = form.get(dotted, "")
            if ftype == "csv":
                raw_val = [p.strip() for p in raw_val.split(",") if p.strip()]
            elif ftype in ("int", "float") and raw_val not in ("", None):
                try:
                    raw_val = int(raw_val) if ftype == "int" else float(raw_val)
                except ValueError:
                    return False, f"{dotted} must be a number", "/config"
            _nest(dotted, raw_val, fields)
        return self._do_config(
            lambda: (configedit.set_global(self.config_path, real_section,
                                           fields)
                     or f"saved {spec['title']} settings"),
            actor)

    def action_config_device(self, form: dict, actor: str):
        from . import configedit
        site, zone = form.get("site", ""), form.get("zone", "")
        orig = form.get("orig", "")
        fields = _inventory_fields(form, is_device=True)
        return self._do_config(
            lambda: "saved device " + configedit.set_device(
                self.config_path, site, zone, orig, fields), actor)

    def action_config_device_add(self, form: dict, actor: str):
        import uuid

        from . import configedit
        site, zone = form.get("site", ""), form.get("zone", "")
        dev = {"name": form.get("name", ""), "driver": form.get("driver", ""),
               "guid": str(uuid.uuid4())}
        for key in ("address", "schedule", "credentials"):
            if form.get(key):
                dev[key] = form[key]
        if not dev["name"] or not dev["driver"]:
            return False, "name and driver are required", "/config"
        return self._do_config(
            lambda: "added device " + configedit.add_device(
                self.config_path, site, zone, dev), actor)

    def action_config_device_delete(self, form: dict, actor: str):
        from . import configedit
        site, zone = form.get("site", ""), form.get("zone", "")
        name = form.get("orig", "")
        return self._do_config(
            lambda: (configedit.delete_device(self.config_path, site, zone,
                                              name)
                     or f"deleted {site}/{zone}/{name}"), actor)

    def action_config_zone(self, form: dict, actor: str):
        from . import configedit
        site, zone = form.get("site", ""), form.get("zone", "")
        fields = _inventory_fields(form, is_device=False)
        return self._do_config(
            lambda: (configedit.set_zone(self.config_path, site, zone, fields)
                     or f"saved zone {site}/{zone}"), actor)

    def action_config_site(self, form: dict, actor: str):
        from . import configedit
        site = form.get("site", "")
        fields = {"retention": _retention_from_form(form)}
        return self._do_config(
            lambda: (configedit.set_site(self.config_path, site, fields)
                     or f"saved site {site}"), actor)

    def action_config_zone_add(self, form: dict, actor: str):
        from . import configedit
        site, zone = form.get("site", ""), form.get("zone", "")
        if not zone:
            return False, "zone name required", "/config"
        return self._do_config(
            lambda: (configedit.add_zone(self.config_path, site, zone)
                     or f"added zone {site}/{zone}"), actor)

    def action_config_site_add(self, form: dict, actor: str):
        from . import configedit
        site = form.get("site", "")
        if not site:
            return False, "site name required", "/config"
        return self._do_config(
            lambda: (configedit.add_site(self.config_path, site)
                     or f"added site {site}"), actor)

    def action_config_collector_add(self, form: dict, actor: str):
        from . import configedit
        collector: dict = {"name": form.get("name", ""),
                           "url": form.get("url", "")}
        if form.get("token"):
            collector["token"] = form["token"]
        if form.get("token_file"):
            collector["token_file"] = form["token_file"]
        vtls = form.get("verify_tls", "").strip()
        if vtls.lower() == "false":
            collector["verify_tls"] = False
        elif vtls and vtls.lower() != "true":
            collector["verify_tls"] = vtls   # CA bundle path
        return self._do_config(
            lambda: "added collector " + configedit.add_collector(
                self.config_path, collector), actor)

    def action_config_collector_delete(self, form: dict, actor: str):
        from . import configedit
        name = form.get("name", "")
        return self._do_config(
            lambda: (configedit.delete_collector(self.config_path, name)
                     or f"removed collector {name}"), actor)

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
        # Live panel: fed by the /events/stream SSE endpoint. Degrades
        # gracefully — with JS off (or no event bus) the panel stays empty
        # and the commit table below is the full record.
        live = (
            "<h2>Live activity</h2>"
            "<p class='muted' id='live-status'>connecting to event stream…</p>"
            "<ul id='live-events' class='live-events'></ul>"
            "<script>(function(){\n"
            "  var status=document.getElementById('live-status');\n"
            "  var list=document.getElementById('live-events');\n"
            "  if(!window.EventSource){status.textContent="
            "'live updates need EventSource support';return;}\n"
            "  var es=new EventSource('/events/stream');\n"
            "  es.onopen=function(){status.textContent='live — streaming events';};\n"
            "  es.onerror=function(){status.textContent="
            "'stream disconnected, retrying…';};\n"
            "  es.onmessage=function(e){\n"
            "    var d; try{d=JSON.parse(e.data);}catch(_){return;}\n"
            "    var li=document.createElement('li');\n"
            "    li.className='sev-'+(d.severity||'info');\n"
            "    var t=new Date().toLocaleTimeString();\n"
            "    li.textContent='['+t+'] '+d.type+': '+d.message;\n"
            "    list.insertBefore(li,list.firstChild);\n"
            "    while(list.childNodes.length>50){list.removeChild(list.lastChild);}\n"
            "  };\n"
            "})();</script>"
        )
        body = (
            live
            + "<h2>Recent backups</h2>"
            "<table><tr><th>When</th><th>Device</th><th>Commit</th></tr>"
            + ("".join(rows) or "<tr><td colspan='3'>no backups yet</td></tr>")
            + "</table>"
            f"<p class='muted'>last {limit} commits</p>"
        )
        return _page("otitbup — activity", body)

    def _day_buckets(self, runs: list[dict], days: int = 30,
                     now: float | None = None):
        """Bucket runs into the last `days` calendar days -> list of
        (label, total, changed, failed) oldest first."""
        import datetime as _dt
        import time
        now = now if now is not None else time.time()
        today = _dt.datetime.fromtimestamp(now, _dt.UTC).date()
        buckets = {}
        for i in range(days):
            day = today - _dt.timedelta(days=days - 1 - i)
            buckets[day] = [0, 0, 0]
        for run in runs:
            day = _dt.datetime.fromtimestamp(
                run["started_at"], _dt.UTC).date()
            if day in buckets:
                buckets[day][0] += 1
                if run["changed"]:
                    buckets[day][1] += 1
                if not run["ok"]:
                    buckets[day][2] += 1
        return [
            (day.strftime("%m-%d"), c[0], c[1], c[2])
            for day, c in sorted(buckets.items())
        ]

    def dashboard(self) -> bytes:
        import time

        from . import charts
        now = time.time()
        devices = self.config.all_devices()
        # Classify each device into exactly one bucket (priority order) so
        # the stacked bar sums to the device count.
        covered = never = stale = failing = healthy = 0
        if self.runstore is not None:
            for device in devices:
                st = self.runstore.status(device.qualified_name)
                if st.last_success is not None:
                    covered += 1
                if st.last_success is None:
                    never += 1
                elif st.consecutive_failures > 0:
                    failing += 1
                elif now - st.last_success > 7 * 86400:
                    stale += 1
                else:
                    healthy += 1

        # Coverage donut + status stacked bar.
        cov = charts.donut(covered, len(devices), "covered", charts.OK)
        status_bar = charts.stacked_hbar([
            ("healthy", healthy, charts.OK),
            ("stale", stale, charts.WARN),
            ("failing", failing, charts.FAIL),
            ("never", never, charts.MUTED),
        ])
        status_legend = charts.legend([
            ("healthy", charts.OK), ("stale >7d", charts.WARN),
            ("failing", charts.FAIL), ("never", charts.MUTED),
        ])

        # Backups over the last 30 days (all devices).
        activity_svg = "<p class='muted'>no run history</p>"
        change_svg = ""
        if self.runstore is not None:
            runs = self.runstore.recent_runs(None, limit=5000)
            buckets = self._day_buckets(runs, days=30, now=now)
            activity_svg = charts.vbars(
                [(lbl, total) for lbl, total, _c, _f in buckets],
                colors=[charts.CHANGE] * len(buckets), unit=" runs")
            change_svg = charts.vbars(
                [(lbl, changed) for lbl, _t, changed, _f in buckets],
                colors=[charts.OK] * len(buckets), unit=" changes")

        # Policy findings by severity.
        from .policy import check_all
        findings = [f for g in check_all(self.config, self.store).values()
                    for f in g]
        sev_counts = {}
        for f in findings:
            sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1
        order = ["critical", "high", "medium", "low"]
        sev_colors = {"critical": charts.FAIL, "high": charts.FAIL,
                      "medium": charts.WARN, "low": charts.MUTED}
        policy_svg = charts.vbars(
            [(s, sev_counts.get(s, 0)) for s in order],
            colors=[sev_colors[s] for s in order], height=130)

        blob_mib = (self.blobstore.total_size() / 1048576
                    if self.blobstore else 0)

        def card(title, inner):
            return (
                "<div style='border:1px solid #dde3e8;border-radius:8px;"
                "padding:1rem;flex:1 1 22rem;min-width:20rem' class='card'>"
                f"<h3 style='margin-top:0'>{title}</h3>{inner}</div>"
            )

        body = (
            "<h2>Overview dashboard</h2>"
            "<div style='display:flex;gap:1rem;flex-wrap:wrap'>"
            + card("Coverage",
                   f"<div style='display:flex;align-items:center;gap:1rem'>"
                   f"{cov}<div>{len(devices)} devices<br>"
                   f"<span class='muted'>{covered} covered · {never} never</span>"
                   "</div></div>")
            + card("Device status", status_bar + status_legend)
            + card("Backups / day (30d)", activity_svg)
            + card("Changes / day (30d)", change_svg or
                   "<p class='muted'>no data</p>")
            + card("Policy findings by severity", policy_svg)
            + card("Storage",
                   f"<p style='font-size:1.6rem;font-weight:bold;margin:.2rem 0'>"
                   f"{blob_mib:.1f} MiB</p><span class='muted'>offloaded to "
                   "the blob store</span>")
            + "</div>"
            + "<p class='muted' style='margin-top:1rem'>Charts are inline "
              "SVG — no external scripts. See per-device pages for device "
              "history graphs.</p>"
        )
        return _page("otitbup — dashboard", body)

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
                r["at"], _dt.UTC
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
        # Link to the full manuals, rendered in-app when the docs directory
        # is reachable (source deployments); otherwise the links are hidden.
        doc_links = []
        for slug, label in (("usage", "Usage guide"),
                            ("configuration", "Configuration reference"),
                            ("faq", "FAQ"),
                            ("sitecollector", "Site collectors"),
                            ("releasenotes", "Release notes")):
            if _find_doc(slug) is not None:
                doc_links.append(f"<a href='/help/{slug}'>{label}</a>")
        manuals = (
            "<p class='muted'>Full manuals: " + " · ".join(doc_links) + "</p>"
            if doc_links else
            "<p class='muted'>Full guides: USAGE.md, CONFIGURATION.md and "
            "FAQ.md in the docs directory.</p>"
        )
        body = "<h2>Help</h2>" + manuals + blocks
        return _page("otitbup — help", body)

    def doc_page(self, slug: str) -> bytes | None:
        """Render a bundled Markdown manual (USAGE/FAQ/CONFIGURATION/…) to
        HTML for in-app viewing. Returns None if the doc isn't found."""
        path = _find_doc(slug)
        if path is None:
            return None
        from . import mdrender
        rendered = mdrender.render(path.read_text(encoding="utf-8"))
        body = (
            "<p class='muted'><a href='/help'>&larr; Help</a></p>"
            f"<article class='doc'>{rendered}</article>"
        )
        return _page(f"otitbup — {slug}", body)

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
            runs = self.runstore.recent_runs(device.qualified_name, limit=200)
            if runs:
                from . import charts
                cells = ""
                for run in reversed(runs[:60]):   # oldest -> newest
                    when = _dt.datetime.fromtimestamp(
                        run["started_at"], _dt.UTC
                    ).strftime("%Y-%m-%d %H:%M")
                    if not run["ok"]:
                        color, sym = charts.FAIL, "fail"
                    elif run["changed"]:
                        color, sym = charts.CHANGE, "change"
                    else:
                        color, sym = charts.OK, "ok"
                    cells += (
                        f"<span title='{when}: {sym}' style='display:inline-"
                        f"block;width:10px;height:18px;margin:1px;background:"
                        f"{color};border-radius:2px'></span>"
                    )
                # Runs-per-day bar chart for this device (last 30 days).
                buckets = self._day_buckets(runs, days=30)
                runs_chart = charts.vbars(
                    [(lbl, total) for lbl, total, _c, _f in buckets],
                    colors=[charts.CHANGE] * len(buckets),
                    width=460, height=120, unit=" runs")
                timeline_block = (
                    "<h3>Health timeline "
                    "<span class='muted' style='font-weight:normal'>"
                    "(oldest → newest; green ok, blue change, red fail)"
                    "</span></h3><p>" + cells + "</p>"
                    + "<p class='muted' style='margin:.2rem 0'>runs per day "
                      "(30d)</p>" + runs_chart
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
                        r["at"], _dt.UTC
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
            from .gitstore import manifest_artifacts
            return manifest_artifacts(yaml.safe_load(raw))
        except Exception:
            return {}


class _Handler(BaseHTTPRequestHandler):
    def __init__(self, ui: WebUI, *args, **kwargs):
        self.ui = ui
        super().__init__(*args, **kwargs)

    def handle(self):
        """A client that closes its connection abruptly (a closed browser
        tab, a dropped SSE stream, a load-balancer health probe) makes the
        stdlib server raise ConnectionResetError/BrokenPipeError from deep
        inside request parsing and dump a full traceback. That is benign
        network noise, not a fault — swallow it and log a single debug line
        instead of a scary stack trace."""
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError,
                ConnectionAbortedError, TimeoutError) as exc:
            log.debug("client connection dropped: %s", exc)

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

    def _sse_stream(self) -> None:
        """Stream live events as Server-Sent Events. Each connected browser
        gets its own broadcaster subscription; a periodic heartbeat keeps
        proxies from timing the connection out and detects a dead client."""
        import json
        broadcaster = getattr(self.ui.events, "broadcaster", None)
        if broadcaster is None:
            return self._send(503, b'{"error":"no event stream"}\n',
                              "application/json")
        queue = broadcaster.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            import queue as _q
            while True:
                try:
                    event = queue.get(timeout=15)
                except _q.Empty:
                    self.wfile.write(b": ping\n\n")   # heartbeat
                    self.wfile.flush()
                    continue
                payload = json.dumps({
                    "type": event.type, "message": event.message,
                    "severity": event.severity, "actor": event.actor,
                    "detail": event.detail,
                })
                self.wfile.write(
                    f"event: {event.type}\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client went away
        finally:
            broadcaster.unsubscribe(queue)

    def _cookie_token(self) -> str | None:
        return self._cookie("otitbup_session")

    def _cookie(self, name: str) -> str | None:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return None

    def _resolve_theme(self, identity: dict | None) -> str | None:
        """The effective colour theme: the signed-in user's saved preference
        (per-account), else this browser's cookie (per-browser), else the
        configured global default. Only known themes are honoured."""
        if identity and self.ui.runstore is not None:
            try:
                pref = self.ui.runstore.get_meta(
                    f"theme:{identity['username']}")
            except Exception:
                pref = None
            if pref and pref.get("value") in _THEMES:
                return pref["value"]
        ck = self._cookie("otitbup_theme")
        if ck in _THEMES:
            return ck
        return self.ui.config.webui.get("theme")

    def _handle_theme(self, query: dict) -> None:
        """Set the current user's theme preference (menu-bar picker)."""
        import time
        theme = (query.get("set", [""])[0] or "").strip()
        if theme not in _THEMES:
            theme = "auto"
        nxt = query.get("next", ["/"])[0]
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        identity = self._identify()
        if identity and self.ui.runstore is not None:
            try:
                self.ui.runstore.set_meta(
                    f"theme:{identity['username']}", theme, time.time())
            except Exception:
                pass
        if theme == "auto":
            cookie = "otitbup_theme=; Path=/; Max-Age=0; SameSite=Lax"
        else:
            cookie = (f"otitbup_theme={theme}; Path=/; Max-Age=31536000; "
                      "SameSite=Lax")
        return self._redirect(nxt, headers={"Set-Cookie": cookie})

    def _identify(self) -> dict | None:
        """Resolve the request identity, in order: cookie session (browser),
        Bearer API token (automation), trusted SSO header (behind an auth
        proxy), then HTTP Basic (CLI/scrapers)."""
        session = self.ui.sessions.get(self._cookie_token())
        if session:
            return {
                "username": session.username, "role": session.role,
                "scopes": getattr(session, "scopes", "*"),
                "token": session.token, "via": "session",
            }
        # Bearer API token.
        authz = self.headers.get("Authorization", "")
        if authz.startswith("Bearer ") and self.ui.runstore is not None:
            from . import apitoken
            ident = apitoken.authenticate(self.ui.runstore, authz[7:])
            if ident:
                ident["via"] = "token"
                return ident
        # Trusted SSO header (an upstream proxy did OIDC/SAML and set it).
        header_name = self.ui.config.webui.get("trusted_header")
        if header_name:
            user = self.headers.get(header_name)
            if user:
                role = self.ui.config.webui.get("trusted_default_role",
                                                "viewer")
                # Optional role from a second header.
                role_header = self.ui.config.webui.get("trusted_role_header")
                if role_header and self.headers.get(role_header):
                    role = self.headers.get(role_header)
                return {"username": user, "role": role, "scopes": "*",
                        "via": "sso"}
        from .auth import authenticate
        ident = authenticate(authz, self.ui.users)
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
        # Per-request render context (theme + who is signed in). Theme is
        # re-resolved after identify() so a signed-in user's saved preference
        # can override the cookie/global default.
        _CTX.identity = None
        _CTX.theme = self._resolve_theme(None)
        if path == "/healthz":
            return self._send(200, b'{"status":"ok"}\n', "application/json")
        if path in ("/favicon.svg", "/favicon.ico"):
            # Public (browsers fetch it before sign-in); SVG serves both.
            return self._send(
                200, _FAVICON_SVG, "image/svg+xml",
                headers={"Cache-Control": "public, max-age=86400"})
        if path == "/logout":
            # Logout is idempotent and safe over GET (a menu link) — clears
            # the session cookie and returns to the login page.
            return self._handle_logout()
        if path == "/theme":
            # Cosmetic per-user preference; allowed pre-auth so the picker
            # works on the login page too.
            return self._handle_theme(query)
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
        _CTX.identity = identity
        _CTX.theme = self._resolve_theme(identity)
        role = identity["role"] if identity else "admin"
        csrf = identity["token"] if identity and identity["via"] == "session" else ""

        # Audit page views (not assets/API/metrics/liveness) for compliance.
        if (
            self.ui.runstore is not None
            and not path.startswith(("/api/", "/metrics", "/events/"))
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

        # Live event stream (Server-Sent Events) for the activity page.
        if path == "/events/stream":
            return self._sse_stream()

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
        if path in ("/audit", "/users", "/config"):
            if self.ui.users and role_rank(role) < role_rank("admin"):
                return self._send(
                    403, _page("forbidden", "<p>admin role required</p>")
                )
            if path == "/audit":
                page = self.ui.audit()
            elif path == "/config":
                page = self.ui.config_page(csrf)
            else:
                page = self.ui.users_page(identity or {}, csrf)
            return self._send(200, page)

        content: bytes | None = None
        if path in ("/", "/index.html"):
            content = self.ui.index()
        elif path == "/dashboard":
            content = self.ui.dashboard()
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
        elif path.startswith("/help/"):
            content = self.ui.doc_page(path[len("/help/"):].strip("/"))
            # None -> fall through to the 404 handling below.
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
        _CTX.identity = None
        _CTX.theme = self._resolve_theme(None)

        # Login/logout are their own auth flow.
        if path == "/login":
            return self._handle_login(form)
        if path == "/logout":
            return self._handle_logout()

        identity = self._identify()
        _CTX.identity = identity
        _CTX.theme = self._resolve_theme(identity)

        # Write API (JSON): Bearer token or session; no CSRF for token auth.
        if path.startswith("/api/"):
            return self._api_post(path, identity)

        if self.ui.users and identity is None:
            return self._send(401, _page("unauthorized", "<p>sign in</p>"))
        actor = identity["username"] if identity else "anonymous"
        role = identity["role"] if identity else "admin"
        scopes = identity.get("scopes", "*") if identity else "*"

        # CSRF: cookie-session POSTs must echo the session token.
        if identity and identity["via"] == "session":
            if form.get("csrf") != identity["token"]:
                return self._send(403, _page("forbidden", "<p>bad CSRF token</p>"))

        from .auth import role_rank
        ok, message, back = self._dispatch_post(
            path, form, actor, role, role_rank, scopes)
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
        if ident is None and self.ui.config.ldap:
            # Fall back to LDAP/AD if configured.
            from .auth import ldap_authenticate
            ident = ldap_authenticate(
                self.ui.config.ldap, form.get("username", ""),
                form.get("password", ""))
        if ident is None:
            return self._send(200, self.ui.login_page(
                error="invalid username or password", next_url=next_url))
        session = self.ui.sessions.create(
            ident["username"], ident["role"], ident.get("scopes", "*"))
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

    def _api_post(self, path: str, identity: dict | None) -> None:
        """JSON write API. Auth by Bearer token or session; enforces role
        (operator+) and scope. POST /api/device/<qn>/{backup,verify}."""
        import json

        from .auth import role_rank, scope_allows
        if self.ui.users and identity is None:
            return self._send(
                401, b'{"error":"unauthorized"}\n', "application/json",
                headers={"WWW-Authenticate": 'Bearer'})
        role = identity["role"] if identity else "admin"
        scopes = identity.get("scopes", "*") if identity else "*"
        if role_rank(role) < role_rank("operator"):
            return self._send(403, b'{"error":"operator role required"}\n',
                              "application/json")
        if path.startswith("/api/device/") and path.count("/") >= 5:
            rest = path[len("/api/device/"):]
            qn, _, verb = rest.rpartition("/")
            if not scope_allows(scopes, qn):
                return self._send(403, b'{"error":"out of scope"}\n',
                                  "application/json")
            actor = identity["username"] if identity else "api"
            if verb == "backup":
                ok, msg = self.ui.action_backup(qn, actor)
            elif verb == "verify":
                ok, msg = self.ui.action_verify(qn)
            else:
                return self._send(404, b'{"error":"not found"}\n',
                                  "application/json")
            body = json.dumps({"ok": ok, "message": msg}).encode() + b"\n"
            return self._send(200 if ok else 500, body, "application/json")
        return self._send(404, b'{"error":"not found"}\n', "application/json")

    def _dispatch_post(self, path, form, actor, role, role_rank, scopes="*"):
        """Returns (ok|None, message, back_url). ok is None for 404."""
        from .auth import scope_allows

        def need(level):
            return not self.ui.users or role_rank(role) >= role_rank(level)

        if path == "/reload":
            if not need("admin"):
                return False, "admin role required", "/"
            ok, msg = self.ui.reload_config()
            return ok, msg, "/"
        if path.startswith("/config/"):
            if not need("admin"):
                return False, "admin role required", "/config"
            handlers = {
                "/config/global": lambda: self.ui.action_config_global(
                    form.get("section", ""), form, actor),
                "/config/device": lambda: self.ui.action_config_device(
                    form, actor),
                "/config/device-add": lambda: self.ui.action_config_device_add(
                    form, actor),
                "/config/device-delete":
                    lambda: self.ui.action_config_device_delete(form, actor),
                "/config/zone": lambda: self.ui.action_config_zone(form, actor),
                "/config/site": lambda: self.ui.action_config_site(form, actor),
                "/config/zone-add": lambda: self.ui.action_config_zone_add(
                    form, actor),
                "/config/site-add": lambda: self.ui.action_config_site_add(
                    form, actor),
                "/config/collector-add":
                    lambda: self.ui.action_config_collector_add(form, actor),
                "/config/collector-delete":
                    lambda: self.ui.action_config_collector_delete(form, actor),
            }
            handler = handlers.get(path)
            if handler is None:
                return None, "", "/config"
            ok, msg, back = handler()
            return ok, msg, back
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
                if not scope_allows(scopes, qn):
                    return False, "device out of your scope", back
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
