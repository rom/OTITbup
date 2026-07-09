# OTITbup — Usage guide

A task-oriented walkthrough of the tool. For the exhaustive config-file
reference see [CONFIGURATION.md](CONFIGURATION.md); for common questions
see [FAQ.md](FAQ.md); for deployment see [../packaging/](../packaging/);
for design see [ARCHITECTURE.md](ARCHITECTURE.md).

## Contents

1. [Install](#1-install)
2. [First run](#2-first-run)
3. [Defining the inventory](#3-defining-the-inventory)
4. [Running backups](#4-running-backups)
5. [Scheduling (the daemon)](#5-scheduling-the-daemon)
6. [Inspecting backups](#6-inspecting-backups)
7. [Change management](#7-change-management)
8. [Health, status & metrics](#8-health-status--metrics)
9. [Policy & compliance](#9-policy--compliance)
10. [Retention](#10-retention)
11. [Restore & disaster recovery](#11-restore--disaster-recovery)
12. [Discovery & reconciliation](#12-discovery--reconciliation)
13. [The web UI](#13-the-web-ui)
14. [Events: syslog & SNMP traps](#14-events-syslog--snmp-traps)
15. [Secrets](#15-secrets)
16. [Command reference](#16-command-reference)

---

## 1. Install

```bash
pip install -e .            # core (PyYAML only)
pip install -e .[ssh]       # network gear (netmiko)
pip install -e .[sftp]      # WAGO/PLCnext/Codesys (paramiko)
pip install -e .[siemens]   # S7 (python-snap7)
pip install -e .[rockwell]  # Logix (pycomm3)
pip install -e .[opcua]     # OPC UA (asyncua)
pip install -e .[beckhoff]  # TwinCAT (pyads)
pip install -e .[crypto]    # encrypted secrets + TLS certgen (cryptography)
pip install -e .[dev]       # tests (pytest)
```

Extras are optional and lazy: a driver only needs its extra when actually
used, so a minimal appliance carries only what it runs.

## 2. First run

```bash
otitbup init                 # writes otitbup.yml + secrets.yml (0600)
$EDITOR otitbup.yml          # add your sites/zones/devices
$EDITOR secrets.yml          # add credentials
otitbup validate             # sanity-check the config
otitbup drivers              # list every driver + description
otitbup backup --force       # back up everything now (ignore windows)
```

Every command takes `-c/--config` (default `./otitbup.yml`) and `-v` for
debug logging.

## 3. Defining the inventory

The inventory is `otitbup.yml` — the source of truth, meant to live in
git. It nests **sites → zones → devices**:

```yaml
sites:
  - name: plant-a
    zones:
      - name: cell-1
        maintenance_window: "18:00-06:00"   # only poll outside production
        max_concurrent: 1                    # sequential in this zone
        devices:
          - name: plc-01
            driver: siemens_s7
            address: 10.20.0.10
            schedule: 12h
            credentials: plc-01              # -> key in secrets.yml
            options: { rack: 0, slot: 2 }
```

`otitbup list` shows the resulting inventory; `otitbup drivers` lists the
drivers and which pip extra each needs.

Coverage spans 124 drivers: OT controllers, RTUs and HMIs, plus enterprise
and OT **network gear** (Cisco, Juniper, Arista, Fortinet, Palo Alto,
Aruba/HPE, Huawei, MikroTik, Nokia, Check Point, Stormshield, Phoenix
Contact mGuard) and vendor **appliances** (Yokogawa, Honeywell, Fanuc,
Bachmann, B&R, Emerson ROC/FloBoss) — each capturing what the device's open
protocol exposes (SSH CLI, HTTP export, SFTP project files, or DNP3
attributes).

**Device GUIDs & provenance.** Every device has a stable **GUID** for
chain-of-custody: pin it with `guid:` on the device, or leave it out and a
UUIDv5 is derived from the qualified name (so every device always has one).

```bash
otitbup guids            # list each device's GUID, pinned vs. derived
otitbup guids --assign   # pin a random UUIDv4 for devices lacking one
```

`--assign` writes a persistent UUIDv4 into the config so the identity
survives a device rename. Each backup records provenance in the
`manifest.yml` (`provenance: {device_guid, driver}`) and in the commit's
`Device-GUID` / `Driver` / `Tool-Version` / `Appliance` / `Captured-At`
trailers — tamper-evident under signed commits and queryable with
`git log`. Set the top-level `appliance_id` to name this appliance in the
trailers. See CONFIGURATION.md.

## 4. Running backups

```bash
otitbup backup                      # everything due/eligible
otitbup backup plc-01 core-sw-01    # named devices
otitbup backup --site plant-a       # a whole site
otitbup backup --zone cell-1        # a whole zone (any site)
otitbup backup --site plant-a --zone cell-1   # intersection
otitbup backup --force              # ignore maintenance windows
```

Each device is committed individually. A backup that detects no change
makes no commit. Backups are single-writer across processes (a file lock),
so a manual run and the daemon can't collide.

**Dry run / connectivity test** — collect but never commit, to check
credentials and reachability:

```bash
otitbup test                        # dry-run every device
otitbup test plc-01 --site plant-a  # subset (same selectors as backup)
otitbup backup --dry-run            # equivalent on the backup command
```

**Reliability.** Set `retry.attempts` (with `retry.backoff`) to retry
transient collection failures per device. Define `hooks.pre` / `hooks.post`
(global, or per device under `device.hooks`) to run a shell command before
and after each device backup — e.g. flip a maintenance flag in another
system or notify a chat channel. Hooks get the device context in
`OTITBUP_*` env vars, run with a 120s timeout, and a failing hook is logged
but never fails the backup. See CONFIGURATION.md.

**Capture quality.** A capture that produced no artifacts, is smaller than
`capture.min_bytes`, or is missing the `capture.expect_match` content is
**rejected before it enters the archive** — the backup fails with a clear
message (`capture too small…`, `capture failed content check…`) and nothing
is committed, so a truncated download or an error page never masquerades as
a good backup. Set the guards globally under `capture` or per device via
`options.min_bytes` / `options.expect_match`. The statistical companion is
the `size_drop` anomaly detector (§8), which flags a capture far below a
device's trailing median size.

## 5. Scheduling (the daemon)

```bash
otitbup daemon           # poll forever, backing up devices as they come due
otitbup daemon --once    # a single scheduler tick (for cron)
```

The daemon honours each device's `schedule` and each zone's
`maintenance_window`. Edit the config and it **reloads automatically** on
the next tick (or send `SIGHUP` to force it); a broken edit is rejected and
the previous config kept. It also emits scheduled compliance reports when
`reports.interval` is set.

A device's `schedule` is either an interval (`30m`, `12h`, `1d`, `Ns`) or
a 5-field **cron expression** for wall-clock scheduling:

```yaml
schedule: "0 2 * * *"     # 02:00 daily
schedule: "0 */6 * * 1-5" # every 6h, Mon-Fri
```

Cron fields support `*`, `*/n`, ranges (`1-5`) and lists (`1,3,5`);
day-of-week Sunday is `0` or `7`. Firing uses the zone's `timezone` (IANA
name) if set, else the site timezone, else system local — the same
timezone that interprets `maintenance_window`.

The daemon also runs light **housekeeping** (`git gc`, per
`housekeeping.gc_interval_days`) and, after each backup, **anomaly
detection** (see §8). On their own intervals it runs several scheduled
maintenance jobs — each off unless its interval is set:

- **integrity scrub** (`integrity.interval_days`) — verify + `git fsck`
  (+ optional signature check); persists the result, emits an event, and
  alerts on failure (see §8).
- **offsite push** (`offsite.interval_days`) — build/encrypt/upload a
  snapshot (see §11).
- **restore rehearsals** (`rehearsal.interval_days`) — export + verify a
  bundle per device across all devices, alerting on any failure.

It still emits scheduled compliance reports when `reports.interval` is set.

## 6. Inspecting backups

```bash
otitbup log plc-01                  # commit history for a device
otitbup diff plc-01                 # latest change
otitbup diff plc-01 --from <a> --to <b>   # any two commits
otitbup search "vlan 30"            # grep the latest config of every device
otitbup search "10.0.0.1" --case-sensitive
```

`search` is the fast way to answer "which devices reference this VLAN / IP
/ tag / user?" across the whole estate.

## 7. Change management

- **Annotate a change** with a work order / MOC number (git note):
  ```bash
  otitbup annotate plc-01 "MOC-1234: firmware upgrade"
  ```
- **Maintenance mode** marks a device/zone/site so changes detected during
  it are classified *expected*; changes outside it are *unexpected* and
  alert harder:
  ```bash
  otitbup maintenance plant-a/cell-1/plc-01 --hours 8 --reason "upgrade"
  otitbup maintenance "plant-a/cell-1/*"            # a whole zone
  otitbup maintenance "plant-a/*"                   # a whole site
  otitbup maintenance plant-a/cell-1/plc-01 --off   # clear
  ```
- **Golden-config baseline & drift** — approve a known-good config and
  measure drift against it:
  ```bash
  otitbup baseline set plc-01        # approve the latest (or --commit <hash>)
  otitbup baseline drift             # which devices differ from baseline
  otitbup baseline clear plc-01
  ```
- **Config-as-code (desired state)** — compare live backups against
  configs you *declare* in a directory (`desired.dir`), versioned next to
  the inventory:
  ```bash
  otitbup desired                    # which devices drift from intended
  otitbup desired --diff             # unified diffs; exit nonzero if drifted
  ```
  A **baseline** approves an actual past backup; **desired** state is what
  you author as the intended config. The layout is
  `<desired.dir>/<site>/<zone>/<device>/<artifact-name>`. See
  CONFIGURATION.md.

## 8. Health, status & metrics

```bash
otitbup status                      # per-device: last success, staleness, fails
```

Because run *attempts* are persisted (not just committed changes), the
tool distinguishes "unchanged" from "unreachable". Configure
`alerts.stale_days` to alert when a device has had no successful backup in
N days — the catch-all against silent failure. The web UI `/metrics`
endpoint exposes Prometheus gauges, and `/api/status` the same data as
JSON.

### Anomaly detection

```bash
otitbup anomalies                   # statistical anomalies across devices
```

Over each device's run history the tool flags four kinds of anomaly:
**slow backups** (a single run's duration a z-score past `anomaly.sigma`),
**change storms** (a spike in the change rate above the device's own
baseline), **flapping** (a device oscillating between success and failure —
intermittent, missed by both the failure and recovery alerts), and **slow
trends** (a gradual, sustained slowdown a single-point z-score misses). It
runs automatically after each backup — emitting an `anomaly.detected` event
and alert — and on demand with `otitbup anomalies`. Tune the thresholds
under `anomaly` (sigma, history depth, change windows, flap, trend and
`size_drop` windows); see CONFIGURATION.md.

### Integrity & scrubbing

```bash
otitbup integrity                   # one-pass scrub: verify + git fsck
otitbup integrity --all-commits     # walk full history, not just latest
otitbup integrity --signatures      # + re-verify signed-commit signatures
otitbup integrity --no-fsck         # skip the git fsck leg
otitbup integrity --alert           # persist result, emit event, alert on failure
```

`integrity` is a one-pass continuous-integrity scrub that combines content
verification (`verify`: re-hash artifacts vs. manifests + blob presence),
repository health (`git fsck`), and — with `--signatures` — signed-commit
signature verification. `--alert` behaves like the daemon: it persists the
result, emits an `integrity.ok` / `integrity.error` event (fanned out to
syslog/SNMP/tickets), and alerts on failure.

Schedule it from the daemon with `integrity.interval_days` (0/unset = off),
tuning `all_commits`, `fsck` and `signatures` under the `integrity` config.
The last result surfaces on `/api/status` (an `integrity` object) and
`/metrics` (`otitbup_integrity_ok`,
`otitbup_integrity_last_check_timestamp_seconds`). See CONFIGURATION.md.

## 9. Policy & compliance

```bash
otitbup verify                      # re-hash latest backups vs. manifests
otitbup verify --all-commits        # walk full history + report orphan blobs
otitbup policy                      # config policy findings across devices
otitbup report                      # HTML compliance report
otitbup report --format pdf         # or csv / docx (all stdlib)
otitbup report --format pdf --sign  # + detached Ed25519 signature
otitbup report-verify report.pdf    # verify a signed report
```

Policy rules (built-in + custom `match`/`absent` regex rules) flag
insecure configuration — telnet, SNMP `public`, weak passwords — turning
the backup archive into a compliance scan. The compliance report covers
coverage, unexpected/unannotated changes, policy findings and rehearsal
status; it renders to HTML, CSV, PDF and DOCX, and `--sign` adds an
Ed25519 signature (with a companion `.pubkey`) so auditors can confirm it
wasn't altered.

Every generated report is **archived** to a dedicated `reports/`
subdirectory next to the data directory, under a timestamped name
(`compliance-YYYYMMDD-HHMMSS.<ext>`), so runs accumulate as a versioned
history rather than overwriting each other. `--out <path>` writes an extra
copy to a fixed location. The web UI's **Reports** page lists the archive
newest-first and lets operators view, download and generate reports in the
browser.

### Integrity & at-rest protection

```bash
otitbup verify-audit                # verify the tamper-evident audit chain
```

- **Tamper-evident audit log.** The audit log (SQLite `audit` table) is
  **hash-chained**: each entry stores the previous entry's hash plus its
  own (sha256 over `prev_hash|at|actor|role|action|detail`), so any edit or
  deletion breaks the chain. `otitbup verify-audit` exits 0 when the chain
  is intact, nonzero (reporting the first bad id) when it isn't.
- **Signed commits.** Set `git.sign.key_file` to SSH-sign every backup
  commit, giving a verifiable authorship chain over the whole history
  (`git log --show-signature`).
- **Encryption at rest.** Set `encryption.blob_key` (Fernet,
  `otitbup[crypto]`) to encrypt the large-artifact **blob store** on disk;
  blobs stay content-addressed by the plaintext hash so dedup and manifests
  are unchanged. The git repo, `runstore.db` and secrets file are **not**
  covered by this — use full-disk encryption (LUKS). See CONFIGURATION.md.
- The runstore uses **versioned migrations** (`PRAGMA user_version`) so
  upgrades never lose history.

## 10. Retention

```bash
otitbup retention                   # show effective policies + prune plan (dry run)
otitbup retention --apply           # delete expired large-artifact blobs
```

Text configs stay in git forever; artifacts ≥ `large_file_threshold` are
offloaded to a deduplicated blob store and expire by `keep_versions` /
`keep_days`, set globally and overridable per site/zone/device. Git history
is never rewritten. Run `--apply` from cron.

**Legal holds & retention lock.** Two controls keep content out of the
prune:

```bash
otitbup hold set plant-a/cell-1/plc-01 --reason "litigation 2026-14"
otitbup hold set "plant-a/*"            # a whole site (or site/zone/*, or *)
otitbup hold list                       # active holds
otitbup hold clear plant-a/cell-1/plc-01
```

A **legal hold** exempts a held scope's entire offloaded history from
pruning until cleared — shown as `[HELD]` in `otitbup retention`. The
config-level **retention lock** (`retention.lock_days: N`) keeps everything
captured within the last `N` days regardless of `keep_versions` /
`keep_days` — a minimum-retention / WORM window. For true off-appliance
immutability, point the git remote or `offsite` target at an append-only /
object-locked store (WORM, e.g. S3 Object Lock). See CONFIGURATION.md.

### Backup strategy (3-2-1 / 3-2-1-1-0)

```bash
otitbup strategy                    # evaluate the strategy, show what's missing
otitbup export --out /mnt/usb/otitbup-export.tar.gz   # the offline/offsite copy
```

The **Strategy** page and `otitbup strategy` grade your deployment against
the 3-2-1 rule (3 copies, 2 media, 1 offsite) and 3-2-1-1-0 (+1 offline,
0 errors). otitbup maps the copies to the local repo, a git remote mirror
(`git.push`+`git.remote`), and a portable `otitbup export` archive; declare
the offline copy under `strategy.offline` so it counts.

## 11. Restore & disaster recovery

```bash
otitbup restore plc-01 --out ./bundle       # hash-verified restore bundle
otitbup net-restore core-sw-01              # DRY RUN: show candidate vs. running
otitbup net-restore core-sw-01 --apply      # push config, verify after
otitbup dr-plan plant-a --out dr.html       # per-site DR runbook
otitbup rehearse plc-01 --by alice --notes "quarterly test"
```

PLC/RTU restore stays **guided** — `restore` exports the exact versioned
artifacts plus a `RESTORE.md` checklist and vendor-tool instructions; a
human performs the write. **Network gear** can be restored automatically
(`net-restore`), dry-run by default, capturing the pre-change config and
verifying the post-change config. `rehearse` records restore-test results
that surface in the UI, DR runbooks and reports.

### Offsite copy & cloud restore

Keep an **encrypted** copy of the whole backup (git repo + blob store + run
store) on an external server or cloud object store — the "1 offsite" leg of
3-2-1, hardened. The snapshot is encrypted with a Fernet key on the
appliance *before* upload, so the remote only ever holds ciphertext.

```bash
otitbup offsite genkey                 # -> store as offsite.key_file (keep it off the remote)
otitbup offsite push                   # build + encrypt + upload a snapshot
otitbup offsite list                   # snapshots on the remote
otitbup offsite pull --out ./restore   # download + decrypt + extract the newest
otitbup offsite restore plc-01 --out ./bundle   # restore one device straight from the remote
```

Set the transport under `offsite`: `file` (a directory / NFS-SMB mount /
removable media), `sftp` (any SSH server, needs `otitbup[sftp]`), or `s3`
(AWS S3 or any S3-compatible store — MinIO, Backblaze B2, Wasabi, Ceph RGW).
`offsite restore` pulls a snapshot and produces a hash-verified bundle for
one device **with no device writes**, decrypting offloaded blobs with
`encryption.blob_key` if it is set. Unlike `otitbup export` (a *plaintext*
local tarball for offline media), the offsite copy is encrypted, remote,
and restores directly from the remote; losing the remote credentials
exposes nothing — only the appliance-held offsite key can decrypt. See
CONFIGURATION.md.

## 12. Discovery & reconciliation

```bash
otitbup discover 10.20.0.0/24 --out found.yml    # propose new devices
otitbup reconcile 10.20.0.0/24                   # inventory vs. network
```

Both are opt-in, strictly sequential TCP-connect scans (OT-safe). Discovery
proposes an inventory-shaped YAML for review; reconciliation reports
**unmanaged** hosts (on the network, not backed up) and **unreachable**
devices (in inventory, no response) — coverage you can prove. Add
`--enrich` to discovery to probe each finding's identity (vendor/model via
SNMP or the matching identity driver) and annotate the proposal.

**NetBox** — if NetBox is your source of truth, reconcile against it or
import from it:

```bash
otitbup netbox reconcile    # devices in NetBox not backed up, and vice versa
otitbup netbox import       # write an inventory proposal from NetBox
```

## 13. The web UI

```bash
otitbup passwd --username admin        # -> webui.auth or a users: entry
otitbup certgen --host backup.plant.local --ip 10.0.0.5   # -> webui.tls
otitbup serve                          # http(s)://<host>:<port>
```

Pages: **Devices** (dashboard, per-zone grouping, live filter), **Health**
(coverage/staleness/failures, generate signed reports, reload config),
**Search** (config search with site/zone filters), **Drift**,
**Strategy** (3-2-1 / 3-2-1-1-0 evaluation), **Activity**, **Policy**,
**Retention**, **Drivers**, **Users** (admin), **Audit** (admin), and a
built-in **Help** page. Rich per-device pages carry artifacts, history,
per-commit and any-two-commit diffs, a run-health timeline, notes,
baseline drift and rehearsals. Hover the small **?** icons for inline
popover help.

**In-app manuals.** The **Help** page links to the full manuals — Usage,
Configuration, FAQ and Release notes — rendered in-app from the shipped
Markdown at `/help/usage`, `/help/configuration`, `/help/faq` and
`/help/releasenotes`. The docs directory is located relative to the package
(source deployments) or via the `OTITBUP_DOCS_DIR` environment override.

**Read/write, by role** — sign in (cookie session, CSRF-protected):

| Role | Can do |
|---|---|
| viewer | view everything, search, diffs, API, metrics |
| operator | + back up now, verify, add notes, set baseline, generate report |
| admin | + re-read config, manage users, edit config, view audit log |

Users created in the UI persist in the run store; config-declared users
are static. HTTP Basic is still accepted for the API, `/metrics` scraping
and the CLI. `/healthz` is an unauthenticated liveness probe.

**Configuration from the web UI (admin).** The **Config** page lets an admin
change the whole configuration without hand-editing YAML:

- *Global settings* — offsite copy (transport + URIs/paths/keys), SSO
  (trusted-header and LDAP), events (syslog/SNMP), logging, integrations
  (NetBox, ticketing), encryption/signing/TLS key & certificate paths, the
  secrets backend, global retention defaults, alerts (staleness,
  rate-limit, email, webhooks), retry/backoff, pre/post hooks, anomaly
  thresholds, git housekeeping, config-as-code (desired) directory,
  scheduled reports, and 3-2-1 strategy flags.
- *Federation* — add and remove **collectors** (name, URL, token/token_file,
  TLS verification) for the central roll-up (see
  [SITECOLLECTOR.md](SITECOLLECTOR.md)).
- *Inventory* — per **site**, **zone** and **device**: name, driver
  (dropdown of all installed drivers), address, schedule (interval or
  cron), credentials, maintenance window, timezone, per-zone concurrency,
  and retention lengths. Add or delete devices, zones and sites inline.
- *Users* — the existing **Users** page (create/delete/reset, with roles
  and scopes).

Every save is **validated through the real config loader before it is
written**: an edit that would produce an invalid config is refused and the
file on disk is left untouched. The previous version is kept as
`<config>.bak`, and the running process reloads immediately (the scheduler
daemon picks the change up on its next tick via mtime). Editing requires the
web process to have been started with `-c <config>`. **Comments and key
order are preserved** on save (writes go through `ruamel.yaml`), so a
hand-commented, git-tracked config survives GUI edits. The whole page is
admin-only, CSRF-protected and audited.

Machine-readable: `/metrics` (Prometheus), `/api/status`, `/api/devices`,
`/api/policy`, `/api/device/<name>` (JSON).

**Live activity (SSE).** The **Activity** page has a live panel that
streams operational events over Server-Sent Events (`GET /events/stream`,
per-connection subscription with heartbeats). It uses `EventSource` and
degrades gracefully with JavaScript off (the static feed still renders).

**Scopes & SSO.** A user can be restricted to part of the estate with
`scopes` (globs over `site/zone/name`) so their write actions only touch
in-scope devices, and login can be delegated to an upstream OIDC/SAML proxy
(`webui.trusted_header`) or LDAP/AD. See CONFIGURATION.md.

### API tokens & the write API

Machine callers (CI jobs, orchestrators, a central federation appliance)
drive backups over a small scoped write API using bearer tokens:

```bash
otitbup token create ci-runner --role operator --scopes "plant-a/*" --days 90
otitbup token list                  # names, roles, scopes, expiry
otitbup token delete ci-runner
```

The token secret is shown **once** at creation and stored only sha256-
hashed. Then:

```bash
curl -X POST -H "Authorization: Bearer <token>" \
     https://backup.plant.local:8080/api/device/plant-a/cell-1/plc-01/backup
```

`POST /api/device/<qualified-name>/backup` and `.../verify` require the
**operator** role and the device in scope, and return JSON
`{"ok": ..., "message": ...}`. An active cookie session works too; token
auth needs no CSRF (the token is a header, not a cookie).

### Federation / site collectors

For the Purdue-model / multi-site pattern, a **central** appliance rolls up
health from per-site **collectors**:

```bash
otitbup federation                  # aggregate collector health
```

Configure `federation.collectors` (each with a `url` and a scoped read-only
token); the central appliance polls each collector's `GET /api/status` over
HTTPS. That link carries **health only** — backup **bytes** federate
separately over plain git, each collector pushing to a shared remote
(`git.push` / `git.remote`). See CONFIGURATION.md.

## 14. Events: syslog & SNMP traps

Configure `events` (see CONFIGURATION.md) to fan structured events out to
syslog and SNMPv2c traps. Emitted events:

`process.start` · `process.stop` · `webui.start` · `webui.stop` ·
`backup.start` · `backup.stop` · `backup.error` · `config.read` ·
`config.reload` · `auth.login` · `auth.logout` · `user.create` ·
`user.delete` · `user.passwd`

All events are always written to the audit log (visible at `/audit`) and
Python logging; syslog/SNMP are additional sinks. Both are stdlib — no
pysnmp or external agent required. A formal MIB defining the trap OIDs is
in `mibs/OTITBUP-MIB.txt`.

**Ticketing** — set `tickets` (see CONFIGURATION.md) to open a
ServiceNow/Jira/generic-webhook ticket when selected events fire (by
default `backup.error` and `change.unexpected`), so failures and
unauthorized changes land in your queue automatically.

## Deploying as a service

Run the scheduler and web UI as services with the systemd units or Docker
files in [`packaging/`](../packaging/). The web service exposes an
unauthenticated `/healthz` for liveness, and the daemon reloads its config
on `SIGHUP` (`systemctl reload otitbup-daemon`) or when the file changes.

## 15. Secrets

```bash
otitbup secrets genkey --out otitbup.key
otitbup secrets encrypt secrets.yml secrets.enc --key-file otitbup.key
otitbup secrets decrypt secrets.enc --key-file otitbup.key   # view/edit
```

Backends: `plainfile`, `encryptedfile` (Fernet), `vault` (HashiCorp KV v2)
and `cyberark` (Central Credential Provider) — the last two are stdlib
HTTP clients needing no extra dependencies. See CONFIGURATION.md.

### Blob-store key rotation & compression

Separate from the *secrets* key, the **blob store** (offloaded large
artifacts) can be encrypted at rest with `encryption.blob_key`. Rotate that
key, or enable/disable encryption, with `blobkey`:

```bash
otitbup blobkey genkey                                   # print a new Fernet key
otitbup blobkey rotate --new-key-file new.key --old-key-file old.key
otitbup blobkey rotate --new-key-file new.key            # encrypt plaintext blobs
otitbup blobkey rotate --old-key-file old.key --decrypt  # remove encryption
```

Every blob is re-encrypted from the old key to the new one; blobs stay
content-addressed by their plaintext sha256, so names never change and each
blob's plaintext hash is re-verified as a guard. Rotate the **offsite** key
separately by re-pushing with the new `offsite.key_file` (old snapshots
stay under their old key). **Keep a key backup/escrow** — a lost key makes
those blobs and snapshots unrecoverable. Set `encryption.compress: true` to
gzip new blobs before encryption (backward compatible with existing blobs).
See CONFIGURATION.md.

## 16. Command reference

Two built-in guides: **`otitbup help`** prints every command grouped by
theme with a one-line summary, and **`otitbup explain <command>`** gives a
full description (arguments, examples, related commands) of one command.

| Command | Purpose |
|---|---|
| `help` / `explain <cmd>` | list commands; describe one at length |
| `init` | scaffold a starter config |
| `validate` / `list` / `drivers` | check config; list devices; list drivers |
| `guids [--assign]` | list device GUIDs (pinned vs. derived); `--assign` pins a UUIDv4 |
| `backup [devices] [--site --zone --force --dry-run]` | run a backup |
| `test [devices] [--site --zone]` | dry-run connectivity/credential test (no commit) |
| `daemon [--once]` | scheduler |
| `serve [--host --port]` | web UI |
| `log` / `diff [--from --to]` / `search` | inspect history and configs |
| `status` / `verify [--all-commits]` / `policy` | health, integrity, compliance |
| `integrity [--all-commits --no-fsck --signatures --alert]` | one-pass scrub: verify + git fsck (+ signatures) |
| `anomalies` | statistical anomalies (slow backups, change storms) |
| `annotate` / `maintenance` | change management |
| `baseline set|clear|drift` | golden-config drift |
| `desired [--diff]` | drift vs. declared config-as-code (`desired.dir`) |
| `retention [--apply]` | prune large-artifact blobs |
| `hold set|clear|list <scope>` | legal holds exempting a scope from retention |
| `gc [--aggressive]` | repack/prune the backup git repo |
| `restore` / `net-restore [--apply]` / `dr-plan` / `rehearse` | recovery |
| `discover [--enrich]` / `reconcile` / `netbox` | find devices; prove coverage |
| `report [--format --sign]` / `report-verify` | signed HTML/CSV/PDF/DOCX reports |
| `export` / `strategy` | offline archive; 3-2-1 evaluation |
| `offsite genkey|push|list|pull|restore` | encrypted offsite/cloud snapshot + restore-from-remote |
| `federation` | roll up health from federated site collectors |
| `verify-audit` | verify the tamper-evident audit hash chain |
| `token create|list|delete` | manage scoped API tokens |
| `passwd` / `certgen` | web UI credentials and TLS |
| `secrets genkey|encrypt|decrypt` | secrets management |
| `blobkey genkey|rotate` | blob-store encryption key rotation / (de)encryption |
