"""Static CSS, brand mark/favicon and the client-side filter script for the
web UI. Pure presentation data extracted from webui.py to keep that module
focused on request handling.
"""
from __future__ import annotations

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
/* High contrast: black/white/yellow, maximal separation, no soft shadows. */
:root[data-theme="high-contrast"] {
  --bg:#000; --surface:#000; --text:#fff; --muted:#e6e6e6;
  --border:#fff; --line:#767676; --hover:#1f1f1f; --th-bg:#0a0a0a;
  --field:#000; --accent:#ffd500; --accent-ink:#ffd500;
  --accent-soft:#3d3300; --teal:#00e5e5; --ok:#00e676; --ok-soft:#003317;
  --warn:#ffb000; --warn-soft:#332200; --danger:#ff5252;
  --danger-soft:#330a0a; --danger-border:#ff5252;
  --shadow:none; --shadow-sm:none;
}
:root[data-theme="high-contrast"] a { text-decoration: underline; }
:root[data-theme="solarized"] {
  --bg:#fdf6e3; --surface:#fefbf0; --text:#073642; --muted:#657b83;
  --border:#e6dfc8; --line:#eee8d5; --hover:#f5efdc; --th-bg:#eee8d5;
  --field:#fefbf0; --accent:#268bd2; --accent-ink:#1a6ba3;
  --accent-soft:#dcebf5; --teal:#2aa198; --ok:#617900; --ok-soft:#eef0d5;
  --warn:#8f6c00; --warn-soft:#f3ead0; --danger:#dc322f;
  --danger-soft:#f9e0dd; --danger-border:#efc0ba;
  --shadow:0 1px 2px rgba(101,123,131,.1),0 14px 32px -18px rgba(101,123,131,.3);
  --shadow-sm:0 1px 2px rgba(101,123,131,.12);
}
:root[data-theme="nord"] {
  --bg:#2e3440; --surface:#3b4252; --text:#eceff4; --muted:#aeb6c5;
  --border:#4c566a; --line:#434c5e; --hover:#434c5e; --th-bg:#3f4759;
  --field:#333947; --accent:#88c0d0; --accent-ink:#9fd2e0;
  --accent-soft:#39505b; --teal:#8fbcbb; --ok:#a3be8c; --ok-soft:#384233;
  --warn:#ebcb8b; --warn-soft:#464030; --danger:#e08790;
  --danger-soft:#46333a; --danger-border:#7a4a52;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 14px 34px -20px rgba(0,0,0,.7);
  --shadow-sm:0 1px 2px rgba(0,0,0,.35);
}
:root[data-theme="dracula"] {
  --bg:#22232e; --surface:#282a36; --text:#f8f8f2; --muted:#a8b1d1;
  --border:#44475a; --line:#3a3d4d; --hover:#343747; --th-bg:#2f3140;
  --field:#22232e; --accent:#bd93f9; --accent-ink:#cfaefc;
  --accent-soft:#3b3355; --teal:#8be9fd; --ok:#69e788; --ok-soft:#24382b;
  --warn:#f5c169; --warn-soft:#3d3423; --danger:#ff7b7b;
  --danger-soft:#3d2626; --danger-border:#6d3a3a;
  --shadow:0 1px 2px rgba(0,0,0,.35),0 14px 34px -20px rgba(0,0,0,.75);
  --shadow-sm:0 1px 2px rgba(0,0,0,.4);
}
:root[data-theme="gruvbox"] {
  --bg:#282828; --surface:#32302f; --text:#ebdbb2; --muted:#b3a488;
  --border:#504945; --line:#3c3836; --hover:#3c3836; --th-bg:#373432;
  --field:#2b2928; --accent:#83a598; --accent-ink:#9dbaad;
  --accent-soft:#37413d; --teal:#8ec07c; --ok:#b8bb26; --ok-soft:#37391a;
  --warn:#fabd2f; --warn-soft:#403513; --danger:#fb6a5a;
  --danger-soft:#402420; --danger-border:#6d3a32;
  --shadow:0 1px 2px rgba(0,0,0,.35),0 14px 34px -20px rgba(0,0,0,.7);
  --shadow-sm:0 1px 2px rgba(0,0,0,.4);
}
/* WCAG: light theme with every text/background pair at or above the
   WCAG 2.1 AA 4.5:1 contrast ratio, always-underlined links, and strong
   visible focus (see the focus-visible rule below). */
:root[data-theme="wcag"] {
  --bg:#ffffff; --surface:#ffffff; --text:#1a1a1a; --muted:#595959;
  --border:#767676; --line:#c8c8c8; --hover:#eef2f7; --th-bg:#f2f2f2;
  --field:#ffffff; --accent:#005a9c; --accent-ink:#00457a;
  --accent-soft:#d9e8f5; --teal:#00615e; --ok:#1e6f30; --ok-soft:#e2f2e6;
  --warn:#8a5300; --warn-soft:#f7ecd9; --danger:#a11326;
  --danger-soft:#f9e2e5; --danger-border:#a11326;
  --shadow:none; --shadow-sm:none;
}
:root[data-theme="wcag"] a { text-decoration: underline; }
/* Text-size preference (web UI settings): scales every rem-based size. */
:root[data-size="small"] { font-size: 87.5%; }
:root[data-size="large"] { font-size: 115%; }
:root[data-size="x-large"] { font-size: 132%; }
/* WCAG support mode (web UI settings): visible focus, underlined links,
   independent of the chosen colour theme. */
:root[data-wcag] a { text-decoration: underline; }
:root[data-wcag] *:focus-visible,
:root[data-theme="wcag"] *:focus-visible,
:root[data-theme="high-contrast"] *:focus-visible {
  outline: 3px solid var(--accent); outline-offset: 2px;
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
/* Dropdown menu group (Logs) — CSS-only, opens on hover or keyboard focus */
.nav-group { position: relative; }
.nav-top { display: inline-block; padding: .34rem .6rem; border-radius: 8px;
       color: var(--muted); font-size: .855rem; font-weight: 500;
       white-space: nowrap; cursor: default; user-select: none; }
.nav-top::after { content: ''; }
.nav-group:hover .nav-top, .nav-group:focus-within .nav-top {
       background: var(--hover); color: var(--text); }
.nav-top.active { background: var(--accent-soft); color: var(--accent-ink);
       font-weight: 600; }
.nav-drop { position: absolute; top: 100%; left: 0; min-width: 11rem;
       margin-top: .2rem; padding: .3rem; background: var(--surface);
       border: 1px solid var(--border); border-radius: 10px;
       box-shadow: var(--shadow); z-index: 40; display: none;
       flex-direction: column; gap: .08rem; }
.nav-group:hover .nav-drop, .nav-group:focus-within .nav-drop {
       display: flex; }
.nav-drop a { display: block; }
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
/* Event-log (syslog-style) severities */
.sev-emergency, .sev-alert, .sev-error { color: var(--danger);
       font-weight: 600; }
.sev-warning { color: var(--warn); font-weight: 600; }
.sev-notice { color: var(--accent-ink); }
.sev-info, .sev-debug { color: var(--muted); }
tr.evt-error > td, tr.evt-critical > td, tr.evt-alert > td,
tr.evt-emergency > td { background: var(--danger-soft); }
tr.evt-warning > td { background: var(--warn-soft, var(--accent-soft)); }
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
/* Expandable per-setting help in the config editor */
button.help-btn { width: 18px; height: 18px; padding: 0; border: none;
        border-radius: 50%; background: var(--border); color: var(--muted);
        font-size: 11px; font-weight: 700; line-height: 1; cursor: pointer;
        margin-left: .35rem; vertical-align: middle; }
button.help-btn:hover, button.help-btn[aria-expanded="true"] {
        background: var(--accent-soft); color: var(--accent-ink); }
tr.cfg-help td { color: var(--muted); font-size: .8rem;
        padding: 0 .5rem .5rem; }

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

/* Login page (no app chrome) */
.login-wrap { min-height: 100vh; display: flex; align-items: center;
        justify-content: center; padding: 1.5rem; }
.login-card { width: 100%; max-width: 22rem; background: var(--surface);
        border: 1px solid var(--border); border-radius: 16px;
        box-shadow: var(--shadow); padding: 2rem 1.9rem; }
.login-card .brand { justify-content: center; margin-bottom: 1.1rem; }
.login-card h2 { text-align: center; margin: 0 0 1.2rem; font-size: 1.3rem; }
.login-card form { display: flex; flex-direction: column; gap: .7rem; }
.login-card input { width: 100%; padding: .6rem .7rem; font-size: .95rem; }
.login-card button { width: 100%; padding: .6rem; font-size: .95rem;
        margin-top: .3rem; }
.login-card p { margin: 0; }

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
