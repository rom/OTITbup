# Site collectors & Purdue-model federation

Rolling up a segmented, multi-plant OT estate into one pane of glass with
per-site **collector** appliances and a **central** appliance.

This document covers concept, setup (both sides), day-to-day use,
security, supervision and the honest limits of the feature. It is grounded
in `src/otitbup/federation.py`, the `federation` and `token` CLI
subcommands (`src/otitbup/cli.py`), the read-only API in
`src/otitbup/webui.py` / `src/otitbup/metrics.py`, and the worked example
in [`examples/multi-site-federation.yml`](../examples/multi-site-federation.yml).

See also: [CONFIGURATION.md](CONFIGURATION.md) (`federation:` and `git:`
sections), [USAGE.md](USAGE.md) (*API tokens & the write API*,
*Federation / site collectors*).

## Table of contents

1. [Concept & architecture](#1-concept--architecture)
2. [Setup — collector side](#2-setup--collector-side)
3. [Setup — central side](#3-setup--central-side)
4. [Usage: `otitbup federation`](#4-usage-otitbup-federation)
5. [Security](#5-security)
6. [Supervision & operations](#6-supervision--operations)
7. [Scaling notes & limitations](#7-scaling-notes--limitations)

---

## 1. Concept & architecture

Large OT estates are network-segmented (the Purdue model): a plant floor
cannot be polled directly from a corporate data centre, and often not even
from a neighbouring plant. OTITbup fits that reality by running an
autonomous appliance *inside* each segment.

* A **collector** is an ordinary OTITbup install that backs up the devices
  it can reach in its own plant / cell / Purdue level 3.5 DMZ. It has its
  own `otitbup.yml`, its own git repo, its own run store. Nothing about a
  collector is special — it is a normal deployment that happens to also
  expose its health API and push its bytes to a shared remote.
* A **central** appliance does *not* poll field devices. It rolls up the
  collectors: their health for a single view, and (separately) their backup
  bytes for a single archive.

The two planes are deliberately split:

* **Data plane (the backup bytes)** federates over **plain git**. Each
  collector already pushes its repo to a shared bare git remote
  (`git.push: true` + `git.remote`). No new protocol, no new daemon — the
  proven, boring path carries the payload.
* **Control / visibility plane (health)** is the central appliance polling
  each collector's read-only `GET /api/status` over HTTPS with a scoped
  API token, and aggregating the totals. This is what
  `src/otitbup/federation.py` and `otitbup federation` implement.

```
        Purdue L4 / IT (data centre)
   ┌──────────────────────────────────────────────┐
   │             CENTRAL appliance                 │
   │                                               │
   │   otitbup federation  ──── control plane ───┐ │
   │   (polls GET /api/status over HTTPS,        │ │
   │    Bearer viewer token, aggregates health)  │ │
   │                                             │ │
   │   git pull / serve  ──── data plane ──┐     │ │
   │   (single-pane backup archive)        │     │ │
   └───────────────────────────────────────┼─────┼─┘
                    ▲   ▲                   │     │
        git push    │   │  git push         │     │  HTTPS /api/status
     (backup bytes) │   │ (backup bytes)    ▼     ▼  (health JSON)
   ┌────────────────┴─┐ │        ┌──────────────────────┐
   │ shared bare git  │◄┘        │ (poll each collector) │
   │ remote           │          └───────────┬──────────┘
   │ git.corp:ot/     │                       │
   │   backups.git    │              ┌────────┴────────┐
   └──────────────────┘              │                 │
        ▲          ▲             GET /api/status   GET /api/status
        │ git push │ git push        │                 │
  ┌─────┴─────┐ ┌──┴────────┐  ┌─────┴──────┐  ┌───────┴─────┐
  │ COLLECTOR │ │ COLLECTOR │  │ collector  │  │ collector   │
  │  plant-a  │ │  plant-b  │  │  serve     │  │  serve      │
  │ (L3/L3.5) │ │ (L3/L3.5) │  │ (HTTPS API)│  │ (HTTPS API) │
  └─────┬─────┘ └─────┬─────┘  └────────────┘  └─────────────┘
        │             │
   backs up      backs up
   plant-a       plant-b
   PLCs/RTUs/    PLCs/RTUs/
   switches      switches
```

Key property: the planes fail independently. A collector whose API is
unreachable degrades to a single `DOWN` row in the roll-up (see
`poll_collector`, which never raises) — it does not blind the whole view,
and it does not stop that collector from still pushing bytes to the git
remote.

---

## 2. Setup — collector side

A collector is a standard OTITbup appliance. The federation-specific work
is: (a) serve its health API over TLS, (b) mint a scoped read-only token
for the central appliance, and (c) push its backup bytes to the shared git
remote.

### 2.1 Install and back up the plant's own devices

Install OTITbup and write a normal `otitbup.yml` inventorying the devices
this collector can reach. Nothing federation-specific here — this is just
its own site:

```yaml
# otitbup.yml on collector-a.plant.local
data_dir: ./data

sites:
  - name: plant-a
    zones:
      - name: cell-1
        devices:
          - name: plc-01
            driver: siemens_s7
            address: 10.10.1.21
            schedule: 12h
            credentials: plc-01
```

Validate and run a backup as usual:

```bash
otitbup -c otitbup.yml validate
otitbup -c otitbup.yml backup --all
```

### 2.2 Push the backup bytes to the shared git remote (data plane)

Add a `git:` block so every backup is pushed to the shared bare repository
that the whole estate writes into:

```yaml
git:
  push: true
  remote: git@git.corp.example:ot/backups.git   # the SHARED remote
```

All collectors push to the **same** remote; that remote is the federated
backup archive. (`git.push` / `git.remote` are documented in
[CONFIGURATION.md](CONFIGURATION.md#git); they are ordinary git-store
settings, not something the federation module drives.)

### 2.3 Generate a TLS certificate for the API

The central appliance polls `GET /api/status` over HTTPS across a segment
boundary, so serve with TLS. Generate a self-signed pair (or use your own
PKI):

```bash
otitbup certgen --host collector-a.plant.local --ip 10.10.0.10
```

This writes `webui-cert.pem` and `webui-key.pem` (key mode 0600) and prints
the `webui.tls` snippet to paste in. Wire it into the config:

```yaml
webui:
  host: 0.0.0.0          # reachable by the central appliance
  port: 8080
  tls:
    cert_file: webui-cert.pem
    key_file: webui-key.pem
  auth:
    username: admin
    password_hash: ...   # from `otitbup passwd`
```

`webui.tls` requires both `cert_file` and `key_file` (cert/key paths are
resolved relative to the config file's directory). Configure at least one
credential (`webui.auth`/`users`, or a token) — when any users are
configured, the API refuses unauthenticated requests; when none are, a
non-loopback bind logs a warning that the UI is open.

### 2.4 Mint a scoped viewer token for the central appliance

On the collector, create a **viewer**-role API token that the central box
will present as a Bearer credential:

```bash
otitbup token create central --role viewer --scopes "*"
```

Output:

```
token 'central' created (role=viewer, scopes=*):

  otb_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

store it now — it is not shown again.
```

Notes on the token model (`src/otitbup/apitoken.py`):

* The secret is `otb_` + 32 random URL-safe bytes, shown **once**; only its
  sha256 hash is stored (like a password).
* `--role` is one of `viewer` / `operator` / `admin` (default `operator`);
  for polling `/api/status` choose **viewer** — the read API needs only a
  valid identity, not a privileged role.
* `--scopes` is space-separated `site/zone/name` globs (default `*`). Scopes
  gate the *write* API only; the read endpoints do not enforce them, so `*`
  is fine for a poll-only token — the `viewer` role is what makes it
  least-privilege.
* `--days N` sets an expiry (N days from creation); omit for a
  non-expiring token.

Copy the printed secret to the central appliance (see §3). Inspect or revoke
tokens later with:

```bash
otitbup token list        # name, role, scopes, expiry
otitbup token delete central
```

### 2.5 Run the API

Serve the read-only UI + API (foreground; run under systemd/service manager
in production):

```bash
otitbup -c otitbup.yml serve
```

`serve` binds `webui.host`/`webui.port` (overridable with `--host`/`--port`,
default `127.0.0.1:8080`), wraps the socket in TLS when `webui.tls` is set,
and exposes — among the read-only UI pages — `GET /api/status` (JSON) and
`GET /metrics` (Prometheus). The device scheduler itself runs separately via
`otitbup daemon`; `serve` is just the viewer/API.

---

## 3. Setup — central side

The central appliance adds a `federation:` block naming each collector. It
still has its own `otitbup.yml` (which may inventory a small local set — a
DMZ jump host / historian — or none at all); the fleet lives on the
collectors.

### 3.1 The `federation:` block

```yaml
federation:
  role: central                 # informational label only
  collectors:
    - name: plant-a
      url: https://collector-a.plant.local:8080
      token_file: /etc/otitbup/plant-a.token   # scoped viewer token
      verify_tls: true
    - name: plant-b
      url: https://collector-b.plant.local:8080
      token_file: /etc/otitbup/plant-b.token
      verify_tls: true
```

This mirrors [`examples/multi-site-federation.yml`](../examples/multi-site-federation.yml),
which is a complete, runnable central config (federation block + a shared
`git.remote` + a small local `sites:` inventory). Per-collector keys:

| key          | required | meaning |
|--------------|----------|---------|
| `name`       | recommended | label shown in the roll-up; defaults to the `url` (then `?`) if absent |
| `url`        | yes | base URL of the collector's `serve` endpoint; `/api/status` is appended (a trailing `/` is trimmed). A collector with no `url` becomes a `DOWN` row reading `no url configured` |
| `token`      | one of | the Bearer secret inline |
| `token_file` | one of | path to a file holding the secret (read and stripped at poll time) — preferred, keeps the secret out of the config |
| `verify_tls` | no (default `true`) | TLS verification mode (below) |

`role` is a free-text informational label (`central`) and does not change
behaviour.

**`verify_tls` values** (`_ssl_context` in `federation.py`):

* `true` (default) — verify against the system trust store.
* `"/path/to/ca-bundle.crt"` (a string) — verify against that CA bundle
  file; use this for a private/self-signed PKI where you trust the CA.
* `false` — **disable** verification (hostname check off, `CERT_NONE`).
  Only for a self-signed cert you cannot otherwise trust; it exposes the
  poll to interception/MITM. Prefer pinning a CA bundle instead.

If neither `token` nor `token_file` is set, the poll is sent without an
`Authorization` header — which a collector with auth configured will reject
(a `DOWN` row).

### 3.2 The aggregated backup archive (data plane)

Health federation gives you a live status view; the *bytes* are federated by
git. Point the central appliance's git at the same shared remote the
collectors push to, and pull/serve it as a single-pane backup archive:

```yaml
git:
  push: true
  remote: git@git.corp.example:ot/backups.git   # same remote the collectors push to
```

The collectors write into that bare repo; the central appliance reads from
it. That repository — not the federation API — is where the recoverable
configuration data lives.

---

## 4. Usage: `otitbup federation`

Run the roll-up:

```bash
otitbup -c otitbup.yml federation
```

For each configured collector it polls `GET <url>/api/status` (10 s
timeout), sending `Authorization: Bearer <token>` when a token is
configured, and prints one row:

```
OK   plant-a              devices=  42 covered=  40 stale=  2 failing=  1
OK   plant-b              devices=  17 covered=  17 stale=  0 failing=  0
DOWN plant-c              <urlopen error timed out>

2/3 collectors reachable; total devices=59 covered=57 stale=2 failing=1
```

* `OK` rows show the collector's own totals pulled from its `/api/status`:
  `devices` (configured), `covered` (at least one successful backup),
  `stale` (no success in 7 days), `failing` (last backup attempt failed).
* A `DOWN` row shows the error string instead of counts. The collector was
  unreachable — network, TLS, auth, timeout, or a malformed response —
  captured by `poll_collector`, which never raises. One down site therefore
  never blinds the whole roll-up; it just contributes a `DOWN` line and is
  excluded from the aggregate sums.
* The final line is the aggregate: `reachable/total collectors reachable`
  followed by the summed totals **across reachable collectors only**.

**Exit codes:**

| condition | exit |
|-----------|------|
| every collector reachable | `0` |
| one or more collectors `DOWN` (`unreachable > 0`) | `1` |
| no collectors configured (`federation.collectors` empty/absent) | `0`, with a note to stderr |

The nonzero exit on any unreachable collector is what makes the command
usable as a health probe. Schedule it (cron, or a systemd timer) for
continuous monitoring — a failing exit or a `DOWN` row is your alert that a
site's API (and therefore visibility into that site) has gone dark:

```cron
*/5 * * * * cd /etc/otitbup && otitbup federation || echo "federation degraded" | logger -t otitbup
```

The totals come straight from each collector's `metrics.status_json`, i.e.
the same numbers as that collector's own `otitbup status`, `/metrics` and
dashboard — the roll-up adds nothing the collector doesn't already report.

---

## 5. Security

* **Least-privilege tokens.** Poll tokens are **viewer** role: they can read
  the status/metrics API but cannot drive backups or change anything. The
  write API (`POST /api/device/<qn>/backup|verify`) requires the
  **operator** role and an in-scope device — a viewer token is refused
  there. Give the central appliance a viewer token only.
* **Tokens are hashed at rest.** The secret is shown once at creation and
  stored only as its sha256 hash (`apitoken.hash_token`); a leaked run store
  does not reveal usable tokens. Rotate by creating a new token and deleting
  the old (§6). Use `--days` to force expiry.
* **Read-only by contract.** A collector's `/api/status`, `/api/devices`,
  `/api/policy`, `/api/device/<qn>` (GET) and `/metrics` are read-only
  views. The API cannot edit inventory or push device configs; the only
  mutating surface is the operator-scoped write API, which the viewer token
  cannot reach. This matches OTITbup's estate-wide "read-only collector"
  posture.
* **TLS.** Always serve the API over HTTPS across a segment boundary
  (`webui.tls` + `otitbup certgen`). On the central side set `verify_tls` to
  `true` (public/enterprise CA) or to a **CA bundle path** for a private
  PKI. Reserve `verify_tls: false` for a self-signed cert you truly cannot
  pin — it disables hostname and chain verification and invites MITM.
* **Network segmentation.** The central appliance needs to reach each
  collector on **only** the API port (e.g. `tcp/8443` or `tcp/8080`), in one
  direction (central → collector). No field-device access crosses the
  boundary; the collector remains the only thing talking to the plant. Keep
  the firewall rule that narrow.
* **The git remote holds the backup bytes.** Health federation carries no
  configuration data, but the shared git remote carries *all* of it. Treat
  that repository as sensitive: restrict who can read/write it, and consider
  encryption at rest (`encryption.blob_key_file` for the blob store; commit
  signing via `git.sign`). For an encrypted, access-controlled copy of the
  archive off the estate, use the **offsite** feature (`otitbup offsite
  genkey|push|list|pull|restore`), which stores AES-encrypted snapshots on an
  external server / cloud with the key kept off the remote.

---

## 6. Supervision & operations

### Continuous monitoring

* **`otitbup federation` on a timer** (§4) — the primary health probe.
  Nonzero exit ⇒ at least one collector is `DOWN`; alert on it.
* **Prometheus `/metrics` per collector** — scrape each collector's
  `GET /metrics` directly for time-series and alerting rules. It exposes
  `otitbup_devices_total`, `otitbup_devices_covered`,
  `otitbup_devices_never_backed_up`, `otitbup_devices_stale`,
  `otitbup_devices_failing`, `otitbup_blob_bytes`, plus per-device
  `otitbup_device_last_success_timestamp_seconds` and
  `otitbup_device_consecutive_failures`. `/metrics` needs the same
  authentication as `/api/status`.
* **syslog / SNMP traps & staleness alerts** — each collector can fan
  structured events (and staleness alerts) out over syslog/SNMPv2c
  (`events:` / `alerts:` config; see [CONFIGURATION.md](CONFIGURATION.md)).
  Point them at the same SOC/NMS that watches the federation probe.

### What the states mean

| state | scope | meaning |
|-------|-------|---------|
| `DOWN` | collector | the central appliance could not poll the collector's API (network / TLS / auth / timeout / bad response). Visibility into that whole site is lost; the collector may still be backing up and pushing bytes |
| `stale` | device | a device on the collector has had **no successful backup in 7 days** |
| `failing` | device | a device's **last backup attempt failed** (`consecutive_failures > 0`) |

`DOWN` is a control-plane problem (can't see the site); `stale`/`failing`
are data-plane problems reported *by* a site you can see.

### Token rotation

1. On the collector: `otitbup token create central-2 --role viewer --scopes "*"`.
2. On the central appliance: write the new secret to the collector's
   `token_file` (or update `token:`).
3. Verify: `otitbup federation` shows that collector `OK`.
4. On the collector: `otitbup token delete central` (the old one).

Do the same when a `--days` expiry approaches — an expired token starts
returning `DOWN` (401) rows.

### Adding / removing a collector

* **Add:** stand up the collector (§2), mint its viewer token, then append a
  `collectors:` entry (name, url, token/token_file, verify_tls) on the
  central appliance. Re-run `otitbup federation` to confirm it comes up `OK`.
  If it should join the byte archive too, set `git.push`/`git.remote` on it.
* **Remove:** delete its `collectors:` entry centrally and
  `otitbup token delete <name>` on the collector.

### Troubleshooting a `DOWN` row

The error string on the `DOWN` line tells you which layer failed:

| symptom | likely cause / fix |
|---------|--------------------|
| `no url configured` | the `collectors:` entry is missing `url` |
| `timed out` / `Connection refused` | collector `serve` not running, wrong port, or firewall blocking central→collector on the API port |
| `certificate verify failed` | `verify_tls: true` but the collector uses a private/self-signed cert — point `verify_tls` at the CA bundle, or fix the cert's SAN (`certgen --host/--ip`) |
| `401` / `{"error":"unauthorized"}` | token missing, wrong, **expired** (`--days` elapsed), or the collector has auth configured but the central entry has no `token`/`token_file` |
| JSON / value error | the `url` points at something that isn't an OTITbup `/api/status` (wrong host/port) |
| intermittent expiry / TLS-validity oddities | **clock skew** — token expiry and cert validity are time-based; keep collector and central clocks in sync (NTP) |

Reproduce a single poll by hand from the central appliance:

```bash
curl -H "Authorization: Bearer $(cat /etc/otitbup/plant-a.token)" \
     https://collector-a.plant.local:8080/api/status
```

---

## 7. Scaling notes & limitations

Be clear about what this is and is not:

* **It is a visibility roll-up plus git-federated bytes — not a distributed
  database.** There is no shared state machine, no consensus, no
  cross-site query engine. `otitbup federation` fans out independent HTTP
  polls and sums the results in memory.
* **Each collector is fully autonomous.** It backs up its own plant on its
  own schedule with its own config, run store and git repo, whether or not
  the central appliance is up. The central appliance failing loses the
  *view*, not the backups; a collector failing loses one site, not the
  estate.
* **The central appliance is a read/aggregate view.** Over the control
  plane it only *reads* `/api/status` — it cannot push config, change
  inventory, or trigger backups on a collector through the federation
  feature (that would require an operator-scoped token against the write
  API, deliberately not part of the roll-up).
* **Aggregation is over *reachable* collectors.** The totals line sums only
  `OK` collectors; a `DOWN` site is counted in `unreachable` and excluded
  from the sums, so the aggregate understates the estate while a site is
  dark. The per-row and `reachable/total` counts make that explicit.
* **Polling is sequential and synchronous** (one collector after another,
  10 s timeout each). Fine for tens of sites; for very large fleets prefer
  scraping each collector's `/metrics` into a real TSDB and alerting there,
  using `otitbup federation` as the quick operator check.
* **No auto-discovery of collectors.** The `collectors:` list is explicit;
  adding/removing a site is a config edit on the central appliance (§6).

---

*Related:* [CONFIGURATION.md](CONFIGURATION.md) ·
[USAGE.md](USAGE.md) ·
[`examples/multi-site-federation.yml`](../examples/multi-site-federation.yml)
