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
appliance_id: plant-a-01  # name recorded in commit provenance trailers
                          # (section below; defaults to the hostname)
secrets:    { ... }     # credential backend        (section below)
git:        { ... }     # remote mirroring + commit signing (section below)
encryption: { ... }     # blob-store encryption at rest (section below)
offsite:    { ... }     # encrypted offsite snapshot copy (section below)
webui:      { ... }     # web UI: host/port/auth/TLS/RBAC (section below)
alerts:     { ... }     # change/failure/staleness alerts (section below)
retention:  { ... }     # global retention defaults + lock/holds (section below)
capture:    { ... }     # capture-quality guards    (section below)
integrity:  { ... }     # scheduled continuous-integrity scrub (section below)
rehearsal:  { ... }     # scheduled restore rehearsals (section below)
policy:     { ... }     # config policy checks      (section below)
reports:    { ... }     # scheduled compliance reports (section below)
events:     { ... }     # syslog + SNMP trap event sinks (section below)
tickets:    { ... }     # open tickets on events    (section below)
logging:    { ... }     # log level/format/rotation (section below)
retry:      { ... }     # per-device collection retries (section below)
hooks:      { ... }     # global pre/post backup shell hooks (section below)
anomaly:    { ... }     # statistical anomaly detection (section below)
housekeeping: { ... }   # periodic git gc from the daemon (section below)
desired:    { ... }     # config-as-code intended-state dir (section below)
federation: { ... }     # site-collector health roll-up (section below)
ldap:       { ... }     # optional LDAP/AD web UI login (section below)
sites:      [ ... ]     # the inventory             (section below)
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
        timezone: Europe/Berlin      # optional IANA name; the zone's
                                     # maintenance_window is interpreted in
                                     # this timezone (falls back to the
                                     # site timezone, then system local)
        max_concurrent: 1            # simultaneous connections into this
                                     # zone (default 1 = strictly
                                     # sequential; OT-safe)
        devices:
          - name: plc-01             # required; unique as site/zone/name
            driver: siemens_s7       # required; see `otitbup drivers`
            guid: 7f3a...            # optional stable device GUID; if absent
                                     # a UUIDv5 is derived from the qualified
                                     # name (so every device always has one).
                                     # `otitbup guids --assign` pins a random
                                     # UUIDv4 that survives a rename
            address: 10.20.0.10      # IP/hostname (driver-dependent)
            schedule: 12h            # poll interval: Ns / Nm / Nh / Nd
                                     # (default 1d); OR a 5-field cron
                                     # expression, e.g. "0 2 * * *";
                                     # used by `daemon`
            credentials: plc-01      # optional: key into the secrets file
            hooks:                   # optional; override the global hooks
              pre:  ./notify.sh start
              post: ./notify.sh done
            options: { ... }         # driver-specific, see below
```

Device names can be used bare on the CLI (`otitbup backup plc-01`) when
unambiguous, or fully qualified (`plant-a/cell-1/plc-01`).

**Schedules** accept either the interval form (`30m`, `12h`, `1d`, `Ns`)
or a 5-field cron expression (`"0 2 * * *"` = 02:00 daily). Cron fields
support `*`, `*/n`, ranges (`1-5`), and lists (`1,3,5`); day-of-week
Sunday is `0` or `7`. Cron firing is evaluated in the device's zone
`timezone`.

**Per-device hooks** (`device.hooks: {pre:, post:}`) override the global
`hooks` block for that device; see [hooks](#hooks).

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
| `min_bytes` | any driver | reject a capture below this many bytes (per-device capture guard; see [capture](#capture)) |
| `expect_match` | any driver | regex that must appear in the captured content, else the backup fails (per-device; see [capture](#capture)) |

Vendor SSH profiles (e.g. `cisco_ios`, `hirschmann_hios`, plus enterprise
and OT presets such as `juniper_junos`, `arista_eos`, `fortinet_fortigate`,
`paloalto_panos`, `checkpoint_gaia`, `aruba_osswitch`, `nokia_sros`,
`stormshield`, `phoenix_mguard`) are presets — any of `device_type`,
`commands`, `port`, `scrub` set in `options` overrides the preset for that
device. Web/SFTP/DNP3 appliance drivers exist alongside them (e.g.
`yokogawa_web`, `honeywell_web`, `fanuc_cnc`, `bachmann_m1`,
`br_automation`, `emerson_roc`); `otitbup drivers` lists all 124.

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
  lock_days: 90                   # WORM/minimum-retention window: never prune
                                  # blobs captured within the last N days,
                                  # regardless of keep_versions/keep_days
                                  # (0/unset = off)

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

### Retention lock & legal holds

Two independent controls exempt content from pruning:

- **Retention lock** (`retention.lock_days: N`, above) is a config-level
  minimum-retention / WORM window: any blob captured within the last `N`
  days survives `retention --apply` regardless of `keep_versions` /
  `keep_days`. Set it site/zone/device-wide like the other retention
  fields.
- **Legal holds** are a runtime state (not config), set with the CLI. A
  held scope's entire offloaded history is exempt from pruning until the
  hold is cleared — shown as `[HELD]` in `otitbup retention`:
  ```bash
  otitbup hold set plant-a/cell-1/plc-01 --reason "litigation 2026-14"
  otitbup hold set "plant-a/cell-1/*"        # a whole zone
  otitbup hold set "plant-a/*"               # a whole site
  otitbup hold set "*"                       # everything
  otitbup hold list                          # active holds
  otitbup hold clear plant-a/cell-1/plc-01   # release
  ```
  The scope is a device qualified name, `site/zone/*`, `site/*`, or `*`.

For true **off-appliance immutability**, point the git remote or the
`offsite` target at an append-only / object-locked store (WORM — e.g. S3
Object Lock), so even the appliance cannot rewrite or delete shipped
copies.

## capture

Capture-quality guards reject a silently truncated, empty or wrong capture
**before** it enters the archive — nothing is committed on failure.

```yaml
capture:                          # global defaults (per-device overrides
                                  # via device options.min_bytes /
                                  # options.expect_match)
  min_bytes: 256                  # reject a capture below this many bytes
  expect_match: "hostname"        # regex that must appear in the content
```

A capture is rejected if it produced no artifacts, is below `min_bytes`,
or is missing the `expect_match` content — the backup fails with a clear
message (`capture too small…`, `capture failed content check…`) and
nothing is committed. Set the same keys per device under `options`
(`options.min_bytes`, `options.expect_match`), which override the global
`capture` defaults for that device. The complementary statistical guard is
the [`size_drop`](#anomaly) anomaly detector, which flags a capture far
below a device's trailing median size after the fact.

## integrity

Continuous-integrity scrubbing driven by the daemon (on demand via
`otitbup integrity`). One pass combines content verification (re-hash
artifacts vs. manifests + blob presence), repository health (`git fsck`),
and optional signed-commit signature verification.

```yaml
integrity:
  interval_days: 7       # run a scheduled scrub this often (0/unset = off)
  all_commits: false     # walk full history, not just the latest per device
  fsck: true             # run `git fsck` on the repo (default true)
  signatures: false      # re-verify signed-commit signatures
```

The scheduled scrub persists its result, emits an `integrity.ok` /
`integrity.error` event (fanned out to syslog/SNMP/tickets like other
events), and alerts on failure. The last result is exposed on the JSON
`/api/status` (an `integrity` object) and Prometheus `/metrics`
(`otitbup_integrity_ok`, `otitbup_integrity_last_check_timestamp_seconds`).

## rehearsal

Scheduled restore rehearsals driven by the daemon: export + verify a
restore bundle for every device on a cadence, alerting on any failure.

```yaml
rehearsal:
  interval_days: 30      # rehearse every device this often (0/unset = off)
```

Complements the daemon's scheduled [`integrity`](#integrity) scrub and
scheduled `offsite.interval_days` push (see [offsite](#offsite)).

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
  sign:
    key_file: /etc/otitbup/commit-signing-ed25519   # SSH private key
```

The local repository in `data_dir` is always the primary store; when
`push` is enabled the repo is mirrored to `remote` after each backup run.
Push failures are logged, never fatal.

**Signed commits.** When `git.sign.key_file` points at an SSH private
key, every backup commit is SSH-signed (`git -c gpg.format=ssh -c
user.signingkey=<key> -S`), giving a tamper-evident, verifiable authorship
chain over the whole history. Verify with `git log --show-signature` or
the gitstore's `verify_commit_signature`.

## Provenance & manifest (chain of custody)

Every device carries a **GUID** — pinned with `device.guid`, or otherwise a
stable UUIDv5 derived from the device's qualified name (so every device
always has one). `otitbup guids` lists each device's GUID and whether it is
*pinned* (config) or *derived*; `otitbup guids --assign` writes a
persistent random UUIDv4 into the config for devices lacking one, so the
identity survives a rename. Devices added in the web Config editor get a
random UUIDv4.

Provenance is recorded per backup in two places:

- **The manifest.** `manifest.yml` is now
  `{provenance: {device_guid, driver}, artifacts: {name: {sha256, kind,
  size, offloaded?}}}`. The `provenance` fields are stable, so re-captures
  don't produce spurious commits; older flat manifests are still read via a
  compatibility helper.
- **Commit trailers.** Each backup commit carries `Device-GUID`, `Driver`,
  `Tool-Version`, `Appliance` and `Captured-At` trailers — tamper-evident
  under signed commits and queryable via `git log`. The top-level
  `appliance_id:` names the appliance in the `Appliance` trailer (defaults
  to the hostname).

```yaml
appliance_id: plant-a-01   # optional; recorded in the Appliance trailer
```

## encryption

Encrypt the **blob store** (the content-addressed store for offloaded
large artifacts) at rest with Fernet. Needs `otitbup[crypto]`.

```yaml
encryption:
  blob_key: <fernet-key>            # or:
  # blob_key_file: blob.key         # a file holding the key; or set
  #                                 # OTITBUP_BLOB_KEY in the environment
  compress: true                    # gzip new blobs before encryption
                                    # (default false; backward compatible
                                    # with existing raw/encrypted blobs)
```

Generate a key:

```bash
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
```

Blobs stay content-addressed by the **plaintext** sha256, so dedup and
manifest hashes are unchanged — only the on-disk bytes become ciphertext.
This encrypts **only** the blob store; the git repo, `runstore.db` and the
secrets file are **not** covered — use full-disk encryption (LUKS) for
those.

**Compression at rest.** `encryption.compress: true` gzip-compresses each
new blob before it is encrypted. It is backward compatible: pre-existing
raw or encrypted blobs are read unchanged, and only newly written blobs
are compressed.

**Key rotation.** `otitbup blobkey rotate` re-encrypts every blob from the
old key to a new one (or enables/disables encryption entirely):

```bash
otitbup blobkey genkey                                   # print a new Fernet key
otitbup blobkey rotate --new-key-file new.key --old-key-file old.key
otitbup blobkey rotate --new-key-file new.key            # encrypt plaintext blobs
otitbup blobkey rotate --old-key-file old.key --decrypt  # remove encryption
```

Blobs stay content-addressed by their plaintext sha256, so names never
change; each blob's plaintext hash is re-verified during rotation as a
guard. Rotate the **offsite** key separately by re-pushing with the new
`offsite.key_file` (old snapshots stay readable under their old key).
**KEEP A KEY BACKUP/ESCROW** — a lost key makes the encrypted blobs (and
offsite snapshots) unrecoverable.

## webui

```yaml
webui:
  host: 127.0.0.1        # bind address (default loopback)
  port: 8080
  theme: auto            # DEFAULT theme; each user overrides it from the
                         # menu-bar picker (saved per account + browser cookie).
                         # auto | light | dark | sky | desert | autumn | spring
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

### Scopes (RBAC)

Beyond the role, a user (config `users:` entry or DB user), session and
API token carries `scopes` — a space-separated list of globs matched
against a device's qualified name (`site/zone/name`):

```yaml
webui:
  users:
    - {username: alice, password_hash: pbkdf2_sha256$..., role: admin}
    - {username: pa-op, password_hash: pbkdf2_sha256$..., role: operator,
       scopes: "plant-a/*"}          # may only act on plant-a devices
    - {username: cell1, password_hash: pbkdf2_sha256$..., role: operator,
       scopes: "plant-a/cell-1/*"}   # only cell-1
```

Globs: `*` (everything, the default), `plant-a/*`, `plant-a/cell-1/*`.
Device **write** actions (backup, verify) are denied — in the UI, the
write API and the CLI-issued tokens — when the device's qualified name is
out of scope. Read access is unaffected.

### Enterprise SSO via a trusted header

An upstream reverse proxy can terminate OIDC/SAML and pass the
authenticated identity in a header; the web UI trusts it and creates a
session:

```yaml
webui:
  trusted_header: X-Forwarded-User        # header carrying the username
  trusted_role_header: X-Forwarded-Role   # optional; else the default
  trusted_default_role: viewer            # role when no role header (default viewer)
```

**Only enable this behind such a proxy** — the header is trivially
spoofable if the UI is reachable directly. A trusted-header user's role
maps into the same viewer/operator/admin ladder; scopes default to `*`.

### Scoped write API

The web server also exposes a small write API for machine callers:

| Endpoint | Effect |
|---|---|
| `POST /api/device/<qualified-name>/backup` | back up one device |
| `POST /api/device/<qualified-name>/verify` | verify one device |

Authenticate with **either** an `Authorization: Bearer <token>` API token
(see `otitbup token`, [USAGE.md](USAGE.md)) **or** an active cookie
session. Requires **operator+** and the device in scope. No CSRF is needed
for token auth (the token is a header, not a cookie). Returns JSON
`{"ok": bool, "message": str}`; `401` if unauthenticated, `403` for wrong
role (`operator role required`) or `out of scope`. Nothing is required in
the config — tokens are managed entirely via the CLI.

## ldap

Optional LDAP/AD bind login as a **fallback** for the web UI: login tries
local users first, then LDAP. Needs `otitbup[ldap]` (ldap3).

```yaml
ldap:
  url: ldaps://dc.plant.local
  user_dn_template: "uid={username},ou=people,dc=plant,dc=local"
  group_base: ou=groups,dc=plant,dc=local
  default_role: viewer               # role for a user in no mapped group
  role_map:                          # LDAP group -> otitbup role
    ot-admins: admin
    ot-ops: operator
```

The `{username}` placeholder in `user_dn_template` is filled at login;
group membership under `group_base` is mapped to a role via `role_map`.

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
    protocol: udp        # udp (default) | tcp | tls
    facility: local0     # kern/user/daemon/local0..local7
    # cafile: /etc/ssl/certs/ca-bundle.crt   # for protocol: tls (RFC 5425)
    # verify: true                            # tls certificate verification
  snmp_trap:
    address: 10.0.0.2
    port: 162
    community: public
    enterprise_oid: 1.3.6.1.4.1.99999   # YOUR private enterprise OID base
```

`syslog.protocol` picks the transport: `udp` (classic RFC 3164), `tcp`
(reliable stream), or `tls` (RFC 5425 syslog-over-TLS with RFC 6587 octet
framing — set `cafile` for a private CA, or `verify: false` to skip
verification).

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

## offsite

An **encrypted** snapshot of the *whole* backup — git repo + blob store +
run store — shipped to an external server or cloud object store: the "1
offsite" leg of 3-2-1, hardened. The snapshot is a single `tar.gz`
encrypted with a Fernet key **on the appliance before upload**, so the
remote only ever holds ciphertext and the key never leaves the appliance.
Driven by `otitbup offsite`; needs `otitbup[crypto]`.

```yaml
offsite:
  key_file: /etc/otitbup/offsite.key   # Fernet key; or key:, or the env
                                       # var OTITBUP_OFFSITE_KEY. Generate
                                       # with `otitbup offsite genkey` and
                                       # keep it OFF the remote — it is the
                                       # only thing that can decrypt.
  transport: s3                        # file | sftp | s3
  interval_days: 1                     # daemon pushes a snapshot this often
                                       # (0/unset = off; on-demand via
                                       # `otitbup offsite push`)
```

**file** — a directory: local disk, an NFS/SMB mount, or removable media.

```yaml
offsite:
  transport: file
  dir: /mnt/offsite/otitbup            # required
```

**sftp** — any SSH server (needs `otitbup[sftp]` / paramiko).

```yaml
offsite:
  transport: sftp
  host: backup.example.com
  port: 22                             # default 22
  username: otitbup
  ssh_key_file: /etc/otitbup/id_ed25519   # or password:
  path: /srv/otitbup                   # remote directory
```

**s3** — AWS S3 or any S3-compatible store (MinIO, Backblaze B2, Wasabi,
Ceph RGW), signed with SigV4 over the standard library (no boto3).

```yaml
offsite:
  transport: s3
  bucket: ot-backups                   # required
  prefix: otitbup/                     # object-name prefix
  region: eu-central-1                 # default us-east-1
  endpoint: https://s3.eu-central-1.amazonaws.com   # default:
                                       # https://s3.<region>.amazonaws.com
  access_key: AKIA...                  # or env OTITBUP_OFFSITE_S3_KEY
  secret_key: ...                      # or secret_key_file:, or env
                                       # OTITBUP_OFFSITE_S3_SECRET
  verify_tls: true                     # false for a self-signed endpoint
```

Commands:

- `otitbup offsite genkey` — print a new Fernet key (store it as `key_file`
  and keep it off the remote).
- `otitbup offsite push` — build, encrypt and upload a snapshot, named
  `otitbup-YYYYmmdd-HHMMSS.tar.gz.enc`.
- `otitbup offsite list` — list the snapshots on the remote.
- `otitbup offsite pull [--name N] [--out DIR]` — download, decrypt and
  extract a snapshot (default: newest) into `DIR`, which then holds
  `data/`, `blobs/` and `runstore.db`.
- `otitbup offsite restore DEVICE [--name N] [--commit H] [--out DIR]` —
  pull a snapshot and produce a hash-verified restore bundle for one device
  straight from the offsite copy, with **no device writes**. Uses
  `encryption.blob_key` (if configured) to decrypt offloaded blobs during
  the restore.

**Security model.** The remote holds **ciphertext only** — losing the
remote credentials exposes no configuration; only the appliance-held
offsite key can decrypt, and a wrong key fails loudly. Extraction is
path-traversal-safe (any member that would escape the target directory is
refused). This is distinct from `export` (see [strategy](#strategy)),
which writes a *plaintext* local tarball for removable/offline media:
offsite is **encrypted and remote**, and can restore a device directly
from the remote copy.

## logging

Configured via `logsetup.configure` right after the config loads.

```yaml
logging:
  level: info            # debug | info | warning | error
  format: text           # text | json
  file: /var/log/otitbup/otitbup.log   # omit to log to stderr only
  max_bytes: 10485760    # rotate at this size (default 10 MiB)
  backups: 5             # rotated files to keep (default 5)
```

`json` format emits one structured record per line for log shippers.

## retry

Retries transient collection failures per device.

```yaml
retry:
  attempts: 1            # total tries per device (default 1 = no retry)
  backoff: 2.0           # seconds; doubles each retry: backoff * 2**(attempt-1)
```

With `attempts: 3, backoff: 2.0` a device is tried up to three times,
sleeping 2s then 4s between tries. Applies only to transient failures.

## hooks

Global shell commands run before and after **each** device backup.
Overridable per device (`device.hooks`, which wins over these).

```yaml
hooks:
  pre:  /etc/otitbup/pre-hook.sh
  post: /etc/otitbup/post-hook.sh
```

Each hook runs with `shell=True` and a 120s timeout; a failing hook is
logged, never fatal to the backup. Environment passed to the hook:

| Variable | Value |
|---|---|
| `OTITBUP_DEVICE` | device name |
| `OTITBUP_SITE` / `OTITBUP_ZONE` | site / zone |
| `OTITBUP_DRIVER` | driver id |
| `OTITBUP_ADDRESS` | device address |
| `OTITBUP_PHASE` | `pre` or `post` |
| `OTITBUP_OK` | `1`/`0` — backup succeeded (post only) |

## anomaly

Statistical anomaly detection over run history. Five detectors: **slow
backup** (a single run whose duration is a z-score outlier), **change
storm** (a spike in the change rate), **flapping** (a device oscillating
between success and failure — intermittent, caught by neither the failure
nor recovery alert), **slow trend** (a gradual, sustained slowdown a
single-point z-score misses), and **size drop** (the latest capture far
below the device's trailing median size — a truncated download or error
page that still "succeeded"). Runs automatically after each backup —
emitting an `anomaly.detected` event and alert — and on demand via
`otitbup anomalies`.

```yaml
anomaly:
  enabled: true          # default true
  sigma: 3.0             # duration z-score threshold (slow outlier)
  duration_floor: 5.0    # ignore runs faster than N seconds
  min_history: 8         # runs needed before judging a device
  change_window: 5       # recent-run window for change storms
  change_recent: 0.8     # recent change-rate that trips
  change_baseline: 0.2   # max long-run change-rate for it to fire
  flap_window: 6         # recent-run window for flapping
  flap_transitions: 3    # ok/fail transitions in that window that trip it
  trend_window: 5        # recent-run window for the slow-trend average
  trend_ratio: 2.0       # recent/baseline mean-duration multiplier that trips
  size_drop: 0.5         # flag if the latest capture size is below this
                         # fraction of the trailing median (0.5 = < 50%);
                         # 0 disables the detector
  history: 50            # runs to load per device
```

A change storm fires only when the recent change-rate exceeds
`change_recent` **and** the long-run baseline stays below
`change_baseline` (i.e. a genuine spike, not a chronically churny device).
Flapping fires when the last `flap_window` runs contain at least
`flap_transitions` success↔failure transitions (and at least one of each).
Slow trend fires when the mean duration over the last `trend_window` runs
is at least `trend_ratio`× the older baseline mean — a creep no single
z-score would catch. Size drop fires when the latest captured size is
below `size_drop`× the device's trailing median size — catching a
truncated download or an error page that the collect step still reported
as success (captured size is recorded per run).

## housekeeping

Periodic `git gc` driven by the daemon to keep the backup repo small
(equivalent to `otitbup gc`).

```yaml
housekeeping:
  gc_interval_days: 7    # run git gc this often (omit/0 to disable)
  gc_aggressive: false   # git gc --aggressive when true
```

## desired

Config-as-code: a directory of **intended** device configs to compare live
backups against (`otitbup desired`). Resolved relative to the config file,
like `data_dir`.

```yaml
desired:
  dir: ./desired               # intended-config tree
  strip_trailing_ws: true      # ignore trailing whitespace when diffing
```

Layout mirrors the inventory: `<dir>/<site>/<zone>/<device>/<artifact-name>`.
Unlike a golden **baseline** (an approved *past* backup), a desired config
is what you declare the device *should* be, versioned alongside the
inventory.

## federation

Roll up health from federated **site collectors** (the Purdue-model /
site-collector pattern). A central appliance polls each collector's
read-only `GET /api/status` over HTTPS with a scoped Bearer token and
aggregates health; used by `otitbup federation`.

```yaml
federation:
  role: central                # informational label, e.g. "central"
  collectors:
    - name: plant-a
      url: https://collector-a.plant.local:8080
      token: <api-token>       # or token_file: plant-a.token
      verify_tls: true
    - name: plant-b
      url: https://collector-b.plant.local:8080
      token_file: plant-b.token
```

The federation link is a **control plane** only — it carries health, not
backups. Backup **bytes** federate separately over plain git: each
collector pushes to a shared remote via `git.push` / `git.remote`. Issue
each collector a scoped, read-only token with `otitbup token create`.

## Files next to the config

| File | Created by | Notes |
|---|---|---|
| `data/` | `otitbup backup` | the backup git repository (`data_dir`) |
| `blobs/` | `otitbup backup` | content-addressed store for offloaded large artifacts; pruned by `otitbup retention --apply` |
| `runstore.db` | `otitbup backup` | SQLite: run results, rehearsals, maintenance state, legal holds, last integrity result (feeds status/metrics/reports); safe to delete (loses history) |
| `state.json` | `otitbup daemon` | per-device last-run times; safe to delete (forces a run) |
| `reports/` | `otitbup report`, web UI, daemon | archived compliance reports, timestamped (`compliance-YYYYMMDD-HHMMSS.<ext>`) and versioned; served by the web UI's Reports page; safe to delete (loses report history) |
| `user-prefs.json` | web UI | per-account UI preferences (e.g. colour theme) for every signed-in identity, incl. config/SSO users; safe to delete (resets to defaults) |
| `otitbup.key` | `otitbup secrets genkey` | Fernet key, mode 0600 |
| `webui-cert.pem`, `webui-key.pem` | `otitbup certgen` | TLS pair, key mode 0600 |
| `discovered.yml` | `otitbup discover` | inventory proposal for human review |
