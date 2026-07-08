# OTITbup — Configuration reference

OTITbup is configured by two files, both plain text and both meant to be
managed like code:

| File | Purpose | Version in git? |
|---|---|---|
| `otitbup.yml` | Inventory + application settings (this document) | **Yes** — it is the source of truth |
| `secrets.yml` / `secrets.enc` | Device credentials | No — plaintext protected by OS permissions, or Fernet-encrypted |

Every command takes `-c/--config` (default: `./otitbup.yml`). Validate
after any edit with `otitbup validate`. A complete annotated example
lives in [`examples/otitbup.yml`](../examples/otitbup.yml).

## otitbup.yml — top level

```yaml
data_dir: ./data        # backup git repository; relative paths resolve
                        # against the config file's directory
secrets:   { ... }      # credential backend        (section below)
git:       { ... }      # remote mirroring          (section below)
webui:     { ... }      # web UI: host/port/auth/TLS (section below)
alerts:    { ... }      # change/failure/staleness alerts (section below)
retention: { ... }      # global retention defaults (section below)
policy:    { ... }      # config policy checks      (section below)
reports:   { ... }      # scheduled compliance reports (section below)
events:    { ... }      # syslog + SNMP trap event sinks (section below)
tickets:   { ... }      # open tickets on events    (section below)
sites:     [ ... ]      # the inventory             (section below)
```

## The inventory: sites → zones → devices

```yaml
sites:
  - name: plant-a                    # required, unique
    zones:
      - name: cell-1                 # required, unique within the site
        maintenance_window: "18:00-06:00"   # optional, HH:MM-HH:MM;
                                            # overnight ranges allowed;
                                            # devices are only polled
                                            # inside the window
        max_concurrent: 1            # simultaneous connections into this
                                     # zone (default 1 = strictly
                                     # sequential; OT-safe)
        devices:
          - name: plc-01             # required; unique as site/zone/name
            driver: siemens_s7       # required; see `otitbup drivers`
            address: 10.20.0.10      # IP/hostname (driver-dependent)
            schedule: 12h            # poll interval: Ns / Nm / Nh / Nd
                                     # (default 1d); used by `daemon`
            credentials: plc-01      # optional: key into the secrets file
            options: { ... }         # driver-specific, see below
```

Device names can be used bare on the CLI (`otitbup backup plc-01`) when
unambiguous, or fully qualified (`plant-a/cell-1/plc-01`).

### Driver options

Options are driver-specific; each driver's Python docstring is the
authoritative reference, and `otitbup drivers` lists everything with a
one-line description. The recurring ones:

| Option | Used by | Meaning |
|---|---|---|
| `port` | most network drivers | TCP port override |
| `commands` | SSH drivers/profiles | CLI commands to capture (one artifact each) |
| `device_type` | SSH drivers/profiles | netmiko device type override |
| `scrub` | SSH drivers/profiles | regexes for volatile lines to drop before diffing |
| `paths` | `generic_sftp` family | remote files/dirs/globs to fetch |
| `urls` | `generic_http` family | export endpoints; `{address}` is substituted |
| `verify_tls` | `generic_http` family | set `false` for self-signed device certs |
| `rack`, `slot` | `siemens_s7` | CPU position (300/400: slot 2; 1200/1500: usually 1) |
| `slot` | `rockwell_enip` | controller backplane slot |
| `unit_id` | `schneider_modbus` | Modbus unit (255 = direct addressing) |
| `outstation`, `master` | `generic_dnp3` | DNP3 link addresses |
| `ams_net_id`, `ams_port` | `beckhoff_ads` | ADS target (default `<address>.1.1`, 851) |
| `endpoint`, `nodes` | `generic_opcua` | endpoint URL override; extra nodes to read |
| `max_file_size` | file-fetch drivers | skip files larger than this (default 100 MiB) |

Vendor SSH profiles (e.g. `cisco_ios`, `hirschmann_hios`) are presets —
any of `device_type`, `commands`, `port`, `scrub` set in `options`
overrides the preset for that device.

## retention

Retention controls repo growth. Git keeps the full history of small/text
artifacts forever (cheap, and desirable for configs); artifacts at or
above `large_file_threshold` are **offloaded** to a content-addressed
blob store (`<data_dir>/../blobs`, deduplicated across devices and
versions) with a small pointer file committed to git instead. Retention
then expires old blob content as plain file deletions — **git history is
never rewritten**.

```yaml
retention:                        # global defaults
  keep_versions: 30               # keep blobs of the newest N backups
  keep_days: 365                  # ...and of any backup newer than this
  large_file_threshold: 1048576   # offload artifacts >= this many bytes
                                  # (0 disables offloading)

sites:
  - name: plant-a
    retention: {keep_days: 730}          # site override
    zones:
      - name: cell-1
        retention: {keep_versions: 10}   # zone override
        devices:
          - name: plc-01
            retention: {keep_versions: 5}  # device override
```

Rules:

- Resolution is per field: device > zone > site > global > built-in
  defaults (`keep_versions: 0`, `keep_days: 0`, threshold 1 MiB).
- `0` means unlimited. `keep_versions` and `keep_days` are OR'd — a
  backup's content survives if it is recent enough by either rule.
- A blob shared by several devices survives while ANY device retains it.
- `otitbup retention` shows every device's effective policy (with which
  level set each field) and what would be pruned — it is a dry run;
  `otitbup retention --apply` deletes. Run it from cron for automatic
  enforcement.
- The web UI's **Retention** page shows the same: effective policy per
  device with per-field sources, plus blob-store usage.
- `otitbup restore` resolves pointers back to full content; a bundle
  whose content was expired by retention is flagged, never silently
  empty.

## secrets

```yaml
secrets:
  backend: plainfile     # plainfile | encryptedfile | vault | cyberark
  path: secrets.yml      # file backends: relative to the config file's dir
  # key_file: otitbup.key   # encryptedfile only; or set OTITBUP_KEY
```

The secrets file maps credential names (referenced by `credentials:` on
devices) to per-device values:

```yaml
plc-01:
  username: backup
  password: s3cret
  # enable_secret: ...   # cisco_ios/cisco_asa privileged mode
```

Encrypted workflow:

```bash
otitbup secrets genkey --out otitbup.key            # key file, mode 0600
otitbup secrets encrypt secrets.yml secrets.enc --key-file otitbup.key
otitbup secrets decrypt secrets.enc --key-file otitbup.key   # view/edit
```

Then set `backend: encryptedfile`, `path: secrets.enc`, and either
`key_file` or the `OTITBUP_KEY` environment variable on the service.

### HashiCorp Vault (KV v2)

```yaml
secrets:
  backend: vault
  url: https://vault.plant.local:8200
  mount: secret            # KV v2 mount point (default: secret)
  path_prefix: otitbup     # secret path: <mount>/data/<prefix>/<name>
  # token_file: vault.token   # or set VAULT_TOKEN on the service
  # verify_tls: true
  # ca_cert: internal-ca.pem
```

Each device credential is one KV v2 secret whose keys are the credential
fields — e.g. `vault kv put secret/otitbup/plc-01 username=backup
password=...`. Stdlib HTTP client, no hvac dependency; requests bypass
any proxy environment.

### CyberArk Central Credential Provider (CCP)

```yaml
secrets:
  backend: cyberark
  url: https://ccp.plant.local
  app_id: otitbup
  safe: OT-Backup
  # folder: Root
  # object_prefix: otitbup-      # object looked up: <prefix><name>
  # client_cert: appcert.pem     # CCP client-certificate authentication
  # client_key: appkey.pem
  # verify_tls: true / ca_cert: internal-ca.pem
```

The CCP account's `UserName` becomes `username`, its `Content` becomes
`password`; `Address`/`Port` properties pass through lowercased.

## git (remote mirroring)

```yaml
git:
  push: true
  remote: git@gitlab.plant.local:ot/backups.git
```

The local repository in `data_dir` is always the primary store; when
`push` is enabled the repo is mirrored to `remote` after each backup run.
Push failures are logged, never fatal.

## webui

```yaml
webui:
  host: 127.0.0.1        # bind address (default loopback)
  port: 8080
  auth:                  # HTTP Basic; generate with `otitbup passwd`
    username: admin
    password_hash: pbkdf2_sha256$600000$<salt>$<hash>
  tls:                   # HTTPS; generate a self-signed pair with
    cert_file: webui-cert.pem       # `otitbup certgen`, or use
    key_file: webui-key.pem         # CA-issued PEM files
```

`cert_file`/`key_file` resolve relative to the config file's directory.
Binding beyond loopback without auth logs a loud warning. The UI is
strictly read-only.

**Multiple users with roles.** Instead of (or alongside) the single
`auth` block, list users with roles — `viewer` < `operator` < `admin`:

```yaml
webui:
  users:
    - {username: alice, password_hash: pbkdf2_sha256$..., role: admin}
    - {username: bob,   password_hash: pbkdf2_sha256$..., role: viewer}
```

Generate each hash with `otitbup passwd --username <name>`. Every
authenticated page view is recorded in the audit log (visible at `/audit`,
**admin only**). A single `auth` user is treated as `admin`.

**Roles gate write actions.** The web UI is read/write when signed in:

| Action | Minimum role |
|---|---|
| View pages, search, drift, diffs, API, metrics | viewer |
| Back up a device, verify, add note, set baseline, generate report | operator |
| Re-read config, create/delete users, change passwords, view audit log | admin |

Users can be **managed at runtime** from the `/users` page (admin) —
these are stored in the run store (`runstore.db`) and can be created,
deleted and have passwords changed via the GUI, which is what emits the
`user.create`/`user.delete`/`user.passwd` events. Config-declared users
(in `auth`/`users`) are static and cannot be edited from the UI. Sessions
are cookie-based (real login/logout); HTTP Basic is still accepted for the
API, metrics scraping and the CLI.

## alerts

```yaml
alerts:
  stale_days: 7          # alert if no successful backup in N days (0 = off)
  min_interval: 3600     # suppress a repeat of the SAME alert within N s
                         # (rate-limiting; persists across runs; 0 = off)
  webhooks:
    - https://hooks.example.com/otitbup   # JSON POST per alert
  syslog:
    address: 10.0.0.1
    port: 514
  email:
    smtp_host: mail.plant.local
    smtp_port: 25
    from: otitbup@plant.local
    to: [ot-team@example.com]
```

Alerts fire when a run detects **unexpected** changes (a change outside a
maintenance window — the unauthorized-change signal), separately for
**expected** changes (during maintenance), when backups **fail**, and
when a device has had **no successful backup in `stale_days`** — the last
being the catch-all against silent failure. Delivery failures are logged
and never block backups.

### Maintenance mode (expected vs. unexpected changes)

A change is *expected* if the device is under active maintenance when it
is detected, *unexpected* otherwise. Maintenance is a runtime state set
with the CLI, not config:

```bash
otitbup maintenance plant-a/cell-1/plc-01 --hours 8 --reason "PLC upgrade"
otitbup maintenance "plant-a/cell-1/*" --hours 8      # whole zone
otitbup maintenance "plant-a/*"                       # whole site, until cleared
otitbup maintenance plant-a/cell-1/plc-01 --off       # clear early
```

## policy

Config policy checks lint captured text configs and flag insecure or
non-conformant settings (`otitbup policy`, the web UI Policy page, and the
compliance report). Built-in rules (telnet, SNMP `public`, unencrypted
HTTP admin, weak/plaintext enable passwords) are vendor-neutral. Add or
suppress rules:

```yaml
policy:
  disable: [no-snmpv1v2]           # silence built-in rule ids
  rules:
    - id: no-http-server
      description: HTTP server (unencrypted) enabled
      severity: medium             # low | medium | high | critical
      match: "^ip http server$"    # regex; PRESENCE is a finding
    - id: require-ntp
      description: no NTP server configured
      severity: low
      absent: "ntp server"         # regex; ABSENCE is a finding
```

## reports

Scheduled compliance reports (the daemon writes/emails one every
`interval`); on demand use `otitbup report`.

```yaml
reports:
  interval: 7d           # 7d/24h/... ; omit to disable scheduling
  period_days: 30        # window the report summarises
  out: /var/otitbup/compliance-report.html   # optional file output
```

## events

Structured operational events are always written to the audit log and
Python logging; configure `events` to also fan them out to syslog and
SNMPv2c traps (both stdlib, no extra dependencies):

```yaml
events:
  syslog:
    address: 10.0.0.1
    port: 514
    facility: local0     # kern/user/daemon/local0..local7
  snmp_trap:
    address: 10.0.0.2
    port: 162
    community: public
    enterprise_oid: 1.3.6.1.4.1.99999   # YOUR private enterprise OID base
```

Event types emitted: `process.start`/`process.stop`,
`webui.start`/`webui.stop`, `backup.start`/`backup.stop`/`backup.error`,
`config.read`/`config.reload`, `auth.login`/`auth.logout`,
`user.create`/`user.delete`/`user.passwd`. The SNMP trap OID is
`<enterprise_oid>.0.<event-number>`; the trap carries the event type,
message and severity as string varbinds under `<enterprise_oid>.1.{1,2,3}`.
Replace the placeholder `enterprise_oid` with your organisation's IANA
enterprise number. Sink failures are logged, never fatal. A formal MIB is
provided in `mibs/OTITBUP-MIB.txt`.

## tickets

Open a ticket when specific events fire — typically backup failures and
unexpected changes. Backends: `servicenow`, `jira`, or `generic` (a plain
JSON webhook). Stdlib HTTP, no SDK.

```yaml
tickets:
  backend: servicenow          # servicenow | jira | generic
  url: https://example.service-now.com
  username: svc-otitbup        # basic auth (servicenow / generic)
  password: ...
  on: [backup.error, change.unexpected]   # event types that open a ticket
  # servicenow: { table: incident }
  # jira: { project: OT, issue_type: Task, email: <email>, token: <PAT> }
```

Only the event types listed in `on` create tickets, so routine events
don't flood the queue. Delivery failures are logged, never fatal.

**Request Tracker (RT):** `backend: rt`, `url`, `queue`, and either
`token` (RT 4.4+ REST 2.0 token) or `username`/`password`.

## netbox

Reconcile the inventory against NetBox (your CMDB/IPAM source of truth) or
import devices from it. Used by `otitbup netbox reconcile|import`.

```yaml
netbox:
  url: https://netbox.example.com
  token: <api-token>
  # verify_tls: true
  # filters: { status: active, role: network }   # NetBox query params
```

`reconcile` reports devices in NetBox that otitbup does not back up
(coverage gaps) and inventory devices absent from NetBox. `import` writes
an inventory-shaped YAML proposal (driver guessed from platform) for
human review — nothing is added automatically.

## strategy

Declares facts about your 3-2-1 / 3-2-1-1-0 posture that can't be inferred,
for the Strategy page and `otitbup strategy`.

```yaml
strategy:
  offsite: true            # the git remote (git.remote) is genuinely off-site
  offline:
    path: /mnt/usb/otitbup-export.tar.gz   # the offline/air-gapped copy
    max_age_days: 7                        # how fresh it must be to count
```

otitbup maps the copies to: the local repo (copy 1), a git remote mirror
(copy 2 / offsite), and a portable `otitbup export` archive (the offline
copy). `strategy` evaluates all five conditions (3 copies, 2 media, 1
offsite, 1 offline, 0 errors) and reports what's missing.

## Files next to the config

| File | Created by | Notes |
|---|---|---|
| `data/` | `otitbup backup` | the backup git repository (`data_dir`) |
| `blobs/` | `otitbup backup` | content-addressed store for offloaded large artifacts; pruned by `otitbup retention --apply` |
| `runstore.db` | `otitbup backup` | SQLite: run results, rehearsals, maintenance state (feeds status/metrics/reports); safe to delete (loses history) |
| `state.json` | `otitbup daemon` | per-device last-run times; safe to delete (forces a run) |
| `otitbup.key` | `otitbup secrets genkey` | Fernet key, mode 0600 |
| `webui-cert.pem`, `webui-key.pem` | `otitbup certgen` | TLS pair, key mode 0600 |
| `discovered.yml` | `otitbup discover` | inventory proposal for human review |
