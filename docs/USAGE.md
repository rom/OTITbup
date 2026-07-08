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

## 10. Retention

```bash
otitbup retention                   # show effective policies + prune plan (dry run)
otitbup retention --apply           # delete expired large-artifact blobs
```

Text configs stay in git forever; artifacts ≥ `large_file_threshold` are
offloaded to a deduplicated blob store and expire by `keep_versions` /
`keep_days`, set globally and overridable per site/zone/device. Git history
is never rewritten. Run `--apply` from cron.

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

**Read/write, by role** — sign in (cookie session, CSRF-protected):

| Role | Can do |
|---|---|
| viewer | view everything, search, diffs, API, metrics |
| operator | + back up now, verify, add notes, set baseline, generate report |
| admin | + re-read config, manage users, view audit log |

Users created in the UI persist in the run store; config-declared users
are static. HTTP Basic is still accepted for the API, `/metrics` scraping
and the CLI. `/healthz` is an unauthenticated liveness probe.

Machine-readable: `/metrics` (Prometheus), `/api/status`, `/api/devices`,
`/api/policy`, `/api/device/<name>` (JSON).

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

## 16. Command reference

| Command | Purpose |
|---|---|
| `init` | scaffold a starter config |
| `validate` / `list` / `drivers` | check config; list devices; list drivers |
| `backup [devices] [--site --zone --force]` | run a backup |
| `daemon [--once]` | scheduler |
| `serve [--host --port]` | web UI |
| `log` / `diff [--from --to]` / `search` | inspect history and configs |
| `status` / `verify [--all-commits]` / `policy` | health, integrity, compliance |
| `annotate` / `maintenance` | change management |
| `baseline set|clear|drift` | golden-config drift |
| `retention [--apply]` | prune large-artifact blobs |
| `restore` / `net-restore [--apply]` / `dr-plan` / `rehearse` | recovery |
| `discover [--enrich]` / `reconcile` / `netbox` | find devices; prove coverage |
| `report [--format --sign]` / `report-verify` | signed HTML/CSV/PDF/DOCX reports |
| `export` / `strategy` | offline archive; 3-2-1 evaluation |
| `passwd` / `certgen` | web UI credentials and TLS |
| `secrets genkey|encrypt|decrypt` | secrets management |
