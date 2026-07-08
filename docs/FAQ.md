# OTITbup — FAQ

## General

### What is OTITbup?
An on-prem tool that backs up configurations, settings, logic and code
from OT equipment (PLCs, RTUs, HMIs/SCADA, gateways) and IT network gear
into a versioned git repository, then helps you detect change, prove
coverage, stay compliant, and recover.

### Is it safe to run against live OT equipment?
That is the central design constraint, and it shows up in four independent
mechanisms. (1) Every collector is **read-only by contract** — a driver's
job is to *read* configuration, never write; there is no write method on the
driver interface at all. (2) The one write path, automated network restore,
defaults to a dry run and refuses PLC/RTU drivers (see *Will it write to or
change my PLC?* below). (3) Polling respects **maintenance windows**
(`zone.maintenance_window`, evaluated in `zone.timezone`) and **per-zone
concurrency caps** (`zone.max_concurrent`, default `1` = strictly
sequential), so backup traffic can't fan out and disturb control traffic —
see [CONFIGURATION.md](CONFIGURATION.md#the-inventory-sites--zones--devices).
(4) Network discovery (`otitbup discover`) is opt-in, sequential, and
TCP-connect only, and produces a YAML proposal for human review rather than
editing your inventory. Before your first real poll, run `otitbup test
<device>` (or `backup --dry-run`) to confirm reachability and credentials
without writing a commit.

### Will it write to or change my PLC?
No. PLC/RTU restore is *guided*: `otitbup restore <device> --out ./bundle`
exports the exact versioned artifacts plus a hash-verified manifest and a
`RESTORE.md` checklist, and a human performs the write with the vendor's
engineering tool. Only **network gear** can be restored automatically
(`otitbup net-restore`), it is eligible only for a short allowlist of
CLI drivers (e.g. `cisco_ios`, `cisco_asa`, `generic_ssh`), it **defaults to
a dry run** (prints the diff it would push), captures a pre-change config and
verifies afterwards, and it touches the device only when you explicitly pass
`--apply`. If you point it at a PLC/RTU driver it refuses with "driver isn't
eligible". The offsite copy is just as safe: `otitbup offsite restore`
produces a bundle from the encrypted remote with **no device writes**.

### How do maintenance windows and concurrency caps actually protect the process?
A zone's `maintenance_window` (`"HH:MM-HH:MM"`, overnight ranges like
`"22:00-06:00"` allowed) is the *only* time the daemon will poll devices in
that zone, interpreted in the zone's `timezone` (falling back to the site
timezone, then system local). `max_concurrent` bounds how many connections
open into that zone at once — the default `1` means one device is polled at
a time, end to end, so a cell never sees a burst of simultaneous sessions.
Set both per zone: a busy control cell gets `max_concurrent: 1` and a tight
overnight window; a management VLAN of switches can afford
`max_concurrent: 2` and an hourly cadence. See
[CONFIGURATION.md](CONFIGURATION.md#alerts) for how a change detected inside
a window is classed *expected* and one outside it *unexpected*.

### What does it cost / what are the dependencies?
The core needs only Python 3.11+ and PyYAML. Everything else is an optional
extra installed only when a driver or feature needs it: `otitbup[ssh]`
(netmiko, for the CLI/network profiles), `otitbup[sftp]` (paramiko, for
file-fetch drivers and the offsite sftp transport), `otitbup[siemens]`
(python-snap7), `otitbup[rockwell]` (pycomm3), `otitbup[opcua]` (asyncua),
`otitbup[beckhoff]` (pyads), `otitbup[crypto]` (cryptography, for encrypted
secrets, blob-store encryption and offsite), and `otitbup[ldap]` (ldap3).
Syslog, SNMP traps, DNP3, Modbus, FINS, MC protocol, SNMP fingerprinting,
the S3 offsite transport (SigV4 over stdlib, no boto3), the Vault/CyberArk
secret clients, all four report formats (HTML/CSV/PDF/DOCX), and the web UI
are **all stdlib** — no extra dependency. The lazy driver registry means an
optional import is only attempted when a device actually uses that driver,
so a Modbus-and-network-only site installs almost nothing.

### How do I run it — one-shot, scheduled, or as a service?
Three ways, same config. `otitbup backup [name…]` runs one pass and exits
(good for cron and CI). `otitbup daemon` runs the built-in scheduler: it
honours each device's `schedule` (interval like `12h`/`1d` **or** a 5-field
cron expression, evaluated in the zone timezone) and each zone's maintenance
window, and it also drives scheduled reports, retention hints, periodic
`git gc` (`housekeeping.gc_interval_days`) and anomaly checks. `otitbup
serve` runs the web UI. In production, install the systemd units from
`packaging/` (see *How do I run it as a service?* under Operations).

### Can I configure the tool from the web UI, or only by editing YAML?
Both. `otitbup.yml` is the source of truth and can be hand-edited (and kept
in git) as always. But the web UI's **Config** page (admin-only) also lets
you change everything without touching YAML: global settings — offsite
targets and URIs, SSO/LDAP, events (syslog/SNMP), logging, integrations
(NetBox, ticketing), and encryption/signing/TLS key and certificate paths —
and the full inventory per **site / zone / device**: name, driver,
address, schedule, credentials, maintenance window, timezone, concurrency
and retention, including adding and deleting devices, zones and sites.
Every save is validated through the real config loader before it is
written; an invalid edit is refused and the file is left untouched, the
previous version is kept as `<config>.bak`, and the process reloads
immediately. Saving normalises the file, so **comments are not preserved**
unless `ruamel.yaml` is installed (the commented original stays in `.bak`).
The page requires `serve` to have been started with `-c <config>`, and is
CSRF-protected and audited. See USAGE §13.

## Devices & drivers

### What equipment is supported?
120+ driver types. PLCs/controllers: Siemens, Rockwell, Schneider,
Mitsubishi, Omron, Beckhoff, WAGO, Phoenix, GE/Emerson, Yokogawa,
Honeywell, Bachmann, B&R, Fanuc. HMI/SCADA: Ignition, WinCC (incl.
Unified), FactoryTalk View ME/SE, Wonderware/AVEVA, iFIX, Citect, WinCC OA,
Kepware, AVEVA Edge, zenon, Movicon, VTScada, ClearSCADA/Geo SCADA,
Reliance. Substation IEDs / protection relays: IEC 61850 MMS, SEL, Siemens
SIPROTEC, ABB Relion, GE Multilin, Schneider MiCOM, NR Electric, NARI. RTUs:
DNP3, SEL RTAC, SICAM, ABB RTU500-series, Netcontrol, Emerson ROC/FloBoss,
Kingfisher, Motorola ACE, SATEC, Survalent. Network: Cisco (IOS/ASA/NX-OS),
SCALANCE, RUGGEDCOM, Hirschmann/Belden, Moxa, Westermo, Advantech, Netgear,
Juniper, Arista, Fortinet, Palo Alto, Aruba/HPE, Huawei, MikroTik, Nokia,
Check Point, Stormshield, Phoenix mGuard. Plus generic OPC UA, EtherNet/IP,
DNP3, SNMP, SSH, SFTP and HTTP drivers. Run `otitbup drivers` for the live
list.

### My device isn't listed. Can I still back it up?
Almost certainly, and usually without writing any code. Run `otitbup
drivers` for the live catalog (124 drivers with one-line descriptions), then
pick a generic transport that matches how the device exposes its config:

- `generic_ssh` — any CLI device netmiko can reach. Set `options.device_type`
  (a netmiko type) and `options.commands` (each command becomes one
  artifact); optional `options.scrub` regexes drop volatile lines so diffs
  stay clean. Needs `otitbup[ssh]`.
- `generic_sftp` — any Linux-based device: set `options.paths` to the files,
  directories or globs to fetch. Needs `otitbup[sftp]`.
- `generic_http` — web-managed devices with an export URL: set `options.urls`
  (`{address}` is substituted) and `options.verify_tls: false` for a
  self-signed device cert. Stdlib.
- `snmp_fingerprint` / `generic_opcua` / `generic_enip` / `generic_dnp3` —
  protocol-level identity plus a change fingerprint when there's no file to
  pull.
- `generic_file` — drop engineering-tool exports (TIA, Studio 5000,
  EcoStruxure, GX Works, Sysmac …) into a watch folder (`options.path` +
  `options.patterns`) and version the *real* project files. Stdlib.

If a device is close to an existing profile, you often don't even need a
generic driver — every vendor SSH profile lets you override `device_type`,
`commands`, `port` and `scrub` per device. See
[CONFIGURATION.md](CONFIGURATION.md#driver-options).

### What's the difference between "full content" and "fingerprint"?
Where a protocol lets us pull the actual configuration or program files,
those land in the repo (**full content**). Where the upload protocol is
closed, we capture device identity plus a sha256 **fingerprint** so change
is still detected — and you version the real project via engineering-tool
exports with `generic_file`.

### Why is the IEC 61850 driver "experimental"?
MMS runs over a full OSI stack and vendor implementations vary. The driver
does the handshake and reads the Identify strings, but it hasn't been
validated against every IED. If the handshake is rejected, capture the
device's SCL/CID export via `generic_file` or its web interface via
`generic_http` instead.

### How do I back up an HMI/SCADA system?
Ignition has a clean HTTP gateway-backup endpoint — use `ignition_gateway`
(captures the full `.gwbk` over HTTP, stdlib). WinCC/PCS7, WinCC OA,
FactoryTalk View, Wonderware/System Platform, iFIX, Citect/Plant SCADA and
Kepware projects are file trees on a host — fetch them over SFTP with
`wincc` / `wincc_oa` / `factorytalk_view` / `wonderware` / `ifix` / `citect`
/ `kepware` (all need `otitbup[sftp]`), use `generic_scada` for anything
else that's a project directory, or drop exports into a `generic_file` watch
folder if the box isn't reachable by SFTP.

### How do I list every driver and what it captures?
`otitbup drivers` prints the whole registry with a one-line description of
what each captures and which optional extra it needs; the web UI has the
same catalog page. Fidelity is either **full content** (config/program files
land in the repo) or **identity + fingerprint** (change detection only) —
the README "Supported equipment" tables call out which per driver.

## Storage & retention

### Where are backups stored?
In a local git repository under `data_dir` on the appliance — one commit per
device per change, with a sha256 manifest recorded at capture time. Devices
are laid out as `sites/<site>/<zone>/<name>/…`. Alongside `data_dir` sit
`blobs/` (the content-addressed store for offloaded large artifacts) and
`runstore.db` (SQLite: run results, rehearsals, maintenance state). The
local repo is always the primary store; enable `git.push` + `git.remote` to
mirror it to a remote git server after each run (push failures are logged,
never fatal). See the file table at the end of
[CONFIGURATION.md](CONFIGURATION.md#files-next-to-the-config).

### Is the backup data encrypted at rest? What is and isn't covered?
The large-artifact **blob store** can be, with Fernet: set
`encryption.blob_key` (or `blob_key_file` / `OTITBUP_BLOB_KEY`); needs
`otitbup[crypto]`. Generate a key with `python -c "from cryptography.fernet
import Fernet;print(Fernet.generate_key().decode())"`. Blobs stay
content-addressed by the *plaintext* sha256, so dedup and manifest hashes are
unchanged — only the on-disk bytes become ciphertext. What this **does not**
cover: the git repo (your text configs), `runstore.db`, and the secrets
file. For those, two complementary controls: **full-disk encryption (LUKS)**
on the appliance protects everything at rest, and **encrypted secrets**
(`secrets.backend: encryptedfile` or Vault/CyberArk) protect credentials
specifically. The offsite copy is separately encrypted end to end (see the
3-2-1 section). See
[CONFIGURATION.md](CONFIGURATION.md#encryption).

### How is the backup history made tamper-evident?
Two independent mechanisms. **Signed commits**: point `git.sign.key_file` at
an SSH private key and every backup commit is SSH-signed, giving a
verifiable authorship chain over the whole history — verify with `git log
--show-signature`. **The audit log** (every login, backup, user change,
config reload, page view) is sha256 **hash-chained**, so any edited or
deleted row is detectable; run `otitbup verify-audit` (exit 0 = intact,
nonzero = the id of the first bad row). Add `otitbup verify` on a schedule to
re-hash stored artifacts against their capture-time manifests. See
[CONFIGURATION.md](CONFIGURATION.md#git-remote-mirroring) and the *Web UI &
security* section below.

### Won't the git repo grow forever with big PLC files?
Text configs stay in git (cheap, and worth keeping full history of).
Artifacts at or above `large_file_threshold` (default 1 MiB, `0` disables
offloading) are moved to a content-addressed, deduplicated **blob store**
with a small pointer committed to git instead. `otitbup retention` shows
every device's effective policy and what *would* be pruned (a dry run);
`otitbup retention --apply` deletes expired blob content by `keep_versions` /
`keep_days`. Crucially, **git history is never rewritten** — pruning is plain
file deletion in the blob store, and a restore whose content was expired is
flagged, never silently empty. Run `retention --apply` from cron, or let the
daemon repack the repo with `housekeeping.gc_interval_days`. See
[CONFIGURATION.md](CONFIGURATION.md#retention).

### Can I keep different history for different devices?
Yes — retention resolves **per field** with device > zone > site > global >
built-in defaults. So a critical firewall can carry `retention:
{keep_versions: 100}` at the device level while its zone keeps 10, and a
`keep_days` set at the site level still applies to both (fields are merged,
and `keep_versions`/`keep_days` are OR'd — content survives if it's recent
enough by *either*). A blob shared by several devices survives as long as
*any* one of them still retains it. `otitbup retention` prints which level
set each field, so the effective policy is never a mystery.

## Change detection & compliance

### How do I know if someone changed a device without authorization?
Change classification keys off **maintenance state**. A change detected while
the device is under active maintenance is *expected*; any other detected
change is *unexpected* — the unauthorized-change signal. Unexpected changes
alert separately from expected ones and can open a ticket (`tickets.on:
[change.unexpected]`) or fire an SNMP trap. Maintenance is a runtime state,
not config: before planned work run e.g. `otitbup maintenance
plant-a/cell-1/plc-01 --hours 8 --reason "MOC-1234"`, or
`otitbup maintenance "plant-a/cell-1/*" --hours 8` for a whole zone, and
`--off` to clear early. See
[CONFIGURATION.md](CONFIGURATION.md#maintenance-mode-expected-vs-unexpected-changes).

### What does the anomaly detection actually detect?
Four statistical detectors run over each device's persisted run history,
automatically after every backup (emitting an `anomaly.detected` event and
alert) and on demand via `otitbup anomalies`: **slow backup** (a single run
whose duration is a z-score outlier past `anomaly.sigma`), **change storm**
(the recent change-rate exceeds `change_recent` while the long-run baseline
stays below `change_baseline` — a genuine spike, not a chronically churny
device), **flapping** (a device oscillating between success and failure —
intermittent, so caught by neither the failure nor the recovery alert), and
**slow trend** (the recent mean duration is `trend_ratio`× the older
baseline — a gradual creep a single z-score would miss). Tune every
threshold under `anomaly:`; see
[CONFIGURATION.md](CONFIGURATION.md#anomaly).

### What is baseline / golden-config drift?
`otitbup baseline set <device>` marks an approved config. Drift is the diff
between the latest backup and that baseline — shown per device and on the
`/drift` page. Change detection tells you it differs from *last time*;
drift tells you it differs from *approved*.

### How do I detect drift from a golden / intended config?
Two complementary ways. A **baseline** approves an actual *past* backup
(`otitbup baseline set`); **desired state** is config-as-code you author —
put intended files under `desired.dir` as
`<dir>/<site>/<zone>/<device>/<artifact>` and run `otitbup desired`
(`--diff` for unified diffs; nonzero exit if anything drifted). Use a
baseline to freeze "known good" from the field, and desired state to
enforce a config you maintain in git.

### Can I record why a change happened?
Yes — `otitbup annotate <device> "MOC-1234: reason"` attaches a note (git
note) to a commit, or use the note form on the device page. Unannotated
changes are counted in the compliance report.

### What do the config policy checks do?
They lint captured text configs against rules and report findings per device
(`otitbup policy`, the web UI Policy page, `/api/policy`, and the compliance
report) — turning the backup archive into a standing compliance scan.
Built-in rules are vendor-neutral (telnet enabled, SNMP `public`,
unencrypted HTTP admin, weak/plaintext enable passwords). Silence a built-in
by id with `policy.disable: [no-snmpv1v2]`, and add your own rules with a
regex: `match:` makes **presence** a finding, `absent:` makes **absence** a
finding (e.g. require NTP), each with a `severity` of low/medium/high/
critical. See
[CONFIGURATION.md](CONFIGURATION.md#policy) for the rule schema.

## Operations

### How do I know a backup silently stopped working?
Run *attempts* are persisted to `runstore.db`, so the tool distinguishes
"unchanged" (a successful run that found no diff) from "unreachable" (a
failed run) — a device that hasn't changed still proves it was reached.
`alerts.stale_days` alerts when a device has had no *successful* backup in N
days (the catch-all against silent failure); a **recovery** notification
fires when a previously failing device succeeds again; the Health page and
`otitbup status` show last-success and consecutive-failure counts; and
`/metrics` exposes all of it to Prometheus. Layer anomaly detection on top
for slow/flapping devices that are technically "succeeding".

### How do I run it as a service?
Use the systemd units (or Dockerfile) in `packaging/` — typically one unit
for `otitbup daemon` (the scheduler) and one for `otitbup serve` (the web
UI). The web service answers `GET /healthz` (no auth) for liveness probes.
The daemon **reloads its config without a restart**: it detects a changed
config file on the next tick, or reload it explicitly on `SIGHUP`
(`systemctl reload otitbup-daemon`); a broken edit is rejected and the
previous config kept. Provide secrets/keys via the unit's `Environment=`
(`OTITBUP_KEY`, `OTITBUP_BLOB_KEY`, `OTITBUP_OFFSITE_KEY`, `VAULT_TOKEN`)
rather than on disk where you can.

### How do I run the web UI behind a reverse proxy with TLS?
Bind `otitbup serve` to loopback (`webui.host: 127.0.0.1`) and terminate TLS
at an upstream proxy (nginx/Apache/HAProxy) that forwards to it. You can also
enable TLS directly on the service with `webui.tls.cert_file`/`key_file`
(`otitbup certgen` makes a self-signed pair, or use CA-issued PEMs), which is
worth doing even behind a proxy. The proxy is also where you terminate SSO:
have it authenticate the user and set a trusted header the UI consumes (see
*How do I integrate SSO / OIDC / SAML?*). Only enable trusted-header auth
when the UI is reachable **solely** through that proxy — the header is
spoofable on a directly reachable port, which is why `host` stays on
loopback.

### Can I integrate with my monitoring / SIEM / ticketing?
Yes: `/metrics` (Prometheus) and `/api/*` (JSON) for dashboards; `events`
for syslog and **SNMP traps** (MIB in `mibs/`); and `tickets` to open a
ServiceNow/Jira/generic ticket on backup errors or unexpected changes.

### Does config reload need a restart?
No. The daemon detects a changed config file (or `SIGHUP`) and reloads on
the next tick; the web UI has a "Re-read config" button (admin). A broken
edit is rejected and the previous config kept.

## Web UI & security

### Is the web UI read-only?
No longer — it's read/write with roles. Signed-in **operators** can start
backups, verify, add notes, set baselines and generate reports;
**admins** can reload config and manage users. **viewers** see everything
read-only. Sessions are cookie-based with CSRF protection.

### How do I add users?
Either declare them in the config (`webui.users`, static) or create them at
runtime from the `/users` page (admin) — the latter persist in the run
store and can have passwords changed or be deleted from the UI. Generate a
password hash with `otitbup passwd`.

### Does it support HTTPS?
Yes. `otitbup certgen` makes a self-signed pair, or point `webui.tls` at
your CA-issued PEM files. HTTP Basic (for the API/CLI/scrapers) and cookie
sessions (for browsers) both work.

### Who can see what? Is there an audit trail?
Roles gate write actions and the audit log. Every authenticated page view
and every operational event (logins, backups, user changes, config
reloads) is recorded and visible at `/audit` (admin only). The audit log is
**hash-chained** (each row commits to the previous one), so any edit or
deletion is detectable — verify it with `otitbup verify-audit` (exit 0 =
intact, nonzero = tampered, with the first bad id).

### How do I restrict a user to one site (or zone)?
Give the user a `scopes` glob over the device's qualified name
(`site/zone/name`). A `users:` entry (or DB user, session or API token)
with `scopes: "plant-a/*"` may only run write actions (backup/verify) on
plant-a devices; `"plant-a/cell-1/*"` narrows to one zone; `*` (the
default) is everything. Read access is unaffected; out-of-scope writes are
denied in the UI, the write API and via tokens.

### How do I integrate SSO / OIDC / SAML?
Put the web UI behind a reverse proxy that authenticates the user (OIDC or
SAML) and sets a trusted header, then set `webui.trusted_header` (e.g.
`X-Forwarded-User`), optionally `webui.trusted_role_header` and
`webui.trusted_default_role`. **Only** enable this when the UI is reachable
solely through that proxy — the header is spoofable otherwise. For direct
directory login, configure `ldap` (LDAP/AD bind, needs `otitbup[ldap]`);
login tries local users first, then LDAP, mapping groups to roles.

### How do I trigger a backup from CI or another system?
Mint a scoped token — `otitbup token create ci --role operator --scopes
"plant-a/*" [--days N]` (shown once, stored sha256-hashed) — then
`POST /api/device/<site/zone/name>/backup` (or `.../verify`) with an
`Authorization: Bearer <token>` header. It needs operator+ and the device
in scope, takes no CSRF (the token is a header, not a cookie), and returns
`{"ok": ..., "message": ...}`. A live cookie session works too.

## Secrets

### Where do device credentials live? What are the four backends?
In a **separate** file/store, never in `otitbup.yml` and never written into
the git backup. A device references a credential by name (`credentials:
plc-01`), and `secrets.backend` decides where that name resolves:

- `plainfile` — a `secrets.yml` mapping names to `{username, password,
  enable_secret, …}`, protected only by OS file permissions. Fine for a lab.
- `encryptedfile` — the same file Fernet-encrypted (`secrets.enc`), unlocked
  by a key from `secrets.key_file` or the `OTITBUP_KEY` environment variable.
  Create it with `otitbup secrets genkey` then `otitbup secrets encrypt`.
  Needs `otitbup[crypto]`.
- `vault` — HashiCorp Vault KV v2: each device credential is one secret at
  `<mount>/data/<path_prefix>/<name>`. Token via `token_file` or
  `VAULT_TOKEN`. Stdlib HTTP client, no hvac.
- `cyberark` — CyberArk Central Credential Provider (CCP): looks up
  `<object_prefix><name>` in a safe, with app-id or client-certificate auth.

See [CONFIGURATION.md](CONFIGURATION.md#secrets). Keep the key/token off the
appliance disk where you can (env var in the systemd unit), and out of any
offsite copy.

### Can I rotate the encryption key or move to Vault later?
Yes — the secrets backend is pluggable and drivers never see it. Start with
`encryptedfile` and later switch `secrets.backend` to `vault`/`cyberark`
without touching a single device entry. To rotate a file key, `otitbup
secrets decrypt` with the old key and `otitbup secrets encrypt` with a new
one; to rotate device passwords, change them in the store — the reference
name in the inventory stays the same.

## Troubleshooting

### A backup fails with "credentials are required".
The device references a `credentials:` name that isn't in your secrets file
(or no secrets backend is configured). Check `secrets.path`/backend and that
the named key exists — for `encryptedfile`, `otitbup secrets decrypt … | less`
to confirm; for Vault/CyberArk, that the object path resolves. Then re-test
without committing: `otitbup test <device>`.

### A device shows STALE, FAILING or NEVER — what do they mean and what next?
They're health states derived from `runstore.db`. **NEVER** = no successful
backup has ever been recorded (usually a brand-new device, wrong
address/driver, or a credential that has never worked — run `otitbup test
<device>` to see the actual connection error). **FAILING** = recent runs are
erroring (reachability, auth, or the device refusing the protocol);
`otitbup status` shows the consecutive-failure count and the last error, and
retries with backoff (`retry.attempts`) may already be masking a flaky link.
**STALE** = it *did* work but hasn't succeeded within `alerts.stale_days` —
the poll may be blocked by a maintenance window that never opens, a daemon
that isn't running, or a schedule that never fires. Start with `otitbup test`
for a commit-free reachability/credential check, then check the daemon is up
and the zone window is actually open now.

### The logs show benign-looking connection resets — is that a problem?
Often not. Some OT devices and firewalls abruptly drop a TCP session after
they've handed over what they were going to (or when an idle probe closes),
and the driver logs the reset while still recording a **successful** capture.
The signal that matters is the device's health state and the stored artifact,
not an individual reset line — if `otitbup status` shows recent success and
`otitbup verify` passes, the reset is cosmetic. If a device is genuinely
FAILING, the reset will coincide with an error run and no new artifact.

### How do I check a device is reachable without writing a commit?
`otitbup test <device>` (or `otitbup backup <device> --dry-run`) collects
from the device exactly as a real backup would — proving reachability,
credentials and driver options — but **does not commit** anything to the
repo. Use it when onboarding a device, after a credential rotation, or when
diagnosing a NEVER/FAILING state. It's read-only like every other collect.

### Discovery/`reconcile` finds nothing.
The scan is TCP-connect only to known ports. Ensure the appliance can
reach the subnet, widen the port list if needed, and remember UDP-only
services (SNMP) are probed only during `--enrich`.

### The SNMP trap OIDs show as numbers in my NMS.
Load `mibs/OTITBUP-MIB.txt` into your NMS, and set the same enterprise OID
in both the MIB and `events.snmp_trap.enterprise_oid` (replace the
placeholder `99999` with your IANA enterprise number).

### `net-restore` says the driver isn't eligible.
Automated restore is network-gear only (`cisco_ios`, `cisco_asa`,
`generic_ssh`). For PLCs/RTUs use `otitbup restore` to export a guided
bundle.

## Reports & audit

### What report formats are supported?
HTML, CSV, PDF and DOCX — all generated with the standard library (no
reportlab or python-docx). `otitbup report --format pdf` (or `csv`/`docx`).

### Can reports be signed for auditors?
Yes. `otitbup report --format pdf --sign` writes a detached Ed25519
signature (`.sig`) and the public key (`.pubkey`) beside the report.
Anyone verifies with `otitbup report-verify <report>`. Tampering with even
one byte makes verification fail. The signing key is generated on first
use (mode 0600).

### Can I schedule reports?
Yes — set `reports.interval` and the daemon emits one on a cadence; it can
also email it via the alert channels.

## Backup strategy (3-2-1)

### Does otitbup implement the 3-2-1 rule?
It helps you meet and *prove* it. The Strategy page (and `otitbup
strategy`) evaluates: 3 copies, 2 media, 1 offsite, +1 offline, 0 errors.
The copies are the local repo, a git remote mirror, and a portable
`otitbup export` archive.

### How do I make the third (offline/offsite) copy?
`otitbup export --out /mnt/usb/otitbup-export.tar.gz` bundles the git repo,
blob store and run store into one archive to copy to removable or offsite
media. Point `strategy.offline.path` at it so the Strategy page counts it,
and set `git.push`+`git.remote` for the offsite mirror.

### How do I keep an encrypted copy offsite / in the cloud?
`otitbup offsite push` ships an **encrypted** snapshot of the whole backup
(git repo + blob store + run store) to an external server or cloud object
store. Pick a transport under `offsite`: `file` (a directory / NFS-SMB
mount / removable media), `sftp` (any SSH server), or `s3` (AWS S3 or any
S3-compatible store — MinIO, Backblaze B2, Wasabi, Ceph RGW, signed with
SigV4 over stdlib, no boto3). The snapshot is Fernet-encrypted on the
appliance *before* upload, so the remote only ever holds ciphertext.
Generate the key once with `otitbup offsite genkey` and keep it off the
remote. This is the encrypted, remote counterpart to `otitbup export`
(which writes a plaintext local tarball).

### Can I restore from the offsite/cloud copy?
Yes. `otitbup offsite pull` downloads, decrypts and extracts a whole
snapshot (`data/`, `blobs/`, `runstore.db`); `otitbup offsite restore
<device>` goes further and produces a hash-verified restore bundle for one
device straight from the remote — **with no device writes**, exactly like
`otitbup restore`. If the source appliance encrypted its blob store
(`encryption.blob_key`), offloaded artifacts are decrypted during the
restore. `offsite list` shows what snapshots exist; the newest is used by
default (`--name` picks another).

### Is my cloud provider able to read my configs?
No. The remote holds **ciphertext only** — the snapshot is encrypted on the
appliance before it is uploaded, and the key never leaves the appliance.
Losing the remote credentials (or the provider itself being compromised)
exposes no configuration; only the appliance-held offsite key can decrypt a
snapshot, and a wrong key fails loudly. Extraction is path-traversal-safe.

### What's the difference between 3-2-1 and 3-2-1-1-0?
3-2-1-1-0 adds one **offline/air-gapped** copy (ransomware can't reach it)
and requires **0 errors** — every backup verifies and no device is failing.
otitbup checks both extra conditions on the Strategy page: it counts the
`otitbup export` archive at `strategy.offline.path` (fresh within
`max_age_days`) as the offline copy, and it derives "0 errors" from the run
store (nothing FAILING, verification clean). See
[`examples/cloud-offsite.yml`](../examples/cloud-offsite.yml) for a config
that satisfies all five conditions.

### How do I back up the otitbup appliance itself?
The appliance *is* its backup — the whole state is `data/` (the git repo),
`blobs/`, `runstore.db` and your config/secrets, all next to `otitbup.yml`.
Two ways to protect it. `otitbup export --out backup.tar.gz` bundles repo +
blobs + run store into one **plaintext** tarball for removable/offline media
(the 3-2-1 offline leg). `otitbup offsite push` ships the same state as an
**encrypted** snapshot to a remote (`file`/`sftp`/`s3`). To rebuild on new
hardware, reinstall otitbup, restore your `otitbup.yml`/secrets, then
`otitbup offsite pull` (or untar the export) to repopulate `data/`, `blobs/`
and `runstore.db` — or recover backup bytes from the git remote if you
mirrored with `git.push`.

## Multi-site & federation

### How does it scale across plants / the Purdue model?
Deploy a **site collector** appliance per plant that backs up only its own
equipment, and a **central** appliance that rolls up health. The split is
deliberate: the federation link is a *control plane* only. Set
`federation.role: central` and list each collector under
`federation.collectors` (`url` + a scoped `token`/`token_file` + `verify_tls`);
`otitbup federation` then polls each collector's read-only `GET /api/status`
over HTTPS and aggregates coverage/staleness/failures. See
[CONFIGURATION.md](CONFIGURATION.md#federation) and
[`examples/multi-site-federation.yml`](../examples/multi-site-federation.yml).

### How do the backup bytes get from a collector to the centre?
Separately from health — over **plain git**. Each collector enables
`git.push` to a **shared remote**, so the actual backup commits federate as
ordinary git while only health rides the API. This keeps the data plane
(bytes) and control plane (status) independent: a collector can keep backing
up and pushing even if the central appliance is unreachable. Issue each
collector a scoped, read-only token with `otitbup token create <name>
--role viewer --scopes "*"` for the central appliance to authenticate with.

## Integrations

### Which ticketing systems are supported?
ServiceNow, Jira, Request Tracker (RT), and a generic JSON webhook (for
any CMDB or automation). Configure `tickets.backend` and the event types
that should open a ticket.

### How does the NetBox integration work?
`otitbup netbox reconcile` compares your inventory against NetBox devices
and reports coverage gaps both ways. `otitbup netbox import` turns NetBox
devices into an inventory proposal (driver guessed from platform) for you
to review. It reads NetBox; it never edits your inventory automatically.

### Can I feed device status back to a CMDB?
Use the JSON API (`/api/status`, `/api/devices`, `/api/device/<name>`) or
a generic ticket/webhook — a CMDB job can poll the API on a schedule.

## Devices (more)

### Which network vendors are supported now?
Cisco (IOS/NX-OS/SG/ASA), Juniper (Junos/SRX), Arista, HPE/Aruba (incl.
Aruba OS-Switch), Huawei, MikroTik, Nokia/Alcatel (SR OS), Extreme, Dell,
Zyxel, Fortinet, Palo Alto, Check Point, Stormshield, Sophos, VyOS, plus the
industrial lines (SCALANCE, RUGGEDCOM, Hirschmann/Belden, Moxa, Westermo,
Advantech, Phoenix Contact incl. mGuard, Red Lion, Korenix, Antaira,
Planet, Netgear, Teltonika). Run `otitbup drivers` for all 124.

### Which serial-to-ethernet / protocol gateways are supported?
Moxa NPort and MGate, Lantronix, Digi, Perle, Sena, Advantech EKI, HMS
Anybus, ProSoft, Red Lion, and HMS eWON — via CLI or web export depending
on the model. Use `generic_gateway` for anything else web-managed.

### Can I add my own vendor without writing code?
Often yes: `generic_ssh` (set `device_type` + `commands`), `generic_http`
(set `urls`), `generic_sftp` (set `paths`), `snmp_fingerprint`,
`generic_opcua`/`generic_enip`/`generic_dnp3`, or `generic_file` for
engineering-tool exports.

### How does network discovery work, and is it safe on an OT network?
`otitbup discover 10.20.0.0/24` performs a **sequential, TCP-connect-only**
scan of known management ports and writes a YAML *proposal*
(`discovered.yml`) for you to review — it never edits your inventory and
never sends protocol payloads (with `--enrich` it additionally probes
identity, including UDP services like SNMP). `otitbup reconcile
10.20.0.0/24` does the same scan and reports it against your inventory to
surface coverage gaps. It's the opt-in, review-first counterpart to NetBox
reconciliation for sites without a CMDB. Because it's sequential and
connect-only it stays gentle on control networks, but treat any active scan
of a production OT segment with the usual caution and prefer a maintenance
window.

### Which PLC / controller appliances were added recently?
The latest batch adds web/SFTP/DNP3 appliance drivers: `yokogawa_web` and
`honeywell_web` (DCS web exports), `fanuc_cnc` (HTTP — note FOCAS itself is
proprietary and not implemented), `bachmann_m1`, `br_automation` (B&R
project files over SFTP) and `emerson_roc` (ROC/FloBoss over DNP3). As
always each captures only what the device's open protocol exposes; run
`otitbup drivers` for the full list of 124.

## Web UI (more)

### Is there in-app help?
Yes — a **Help** page in the nav, and small **?** icons throughout that
show a popover explaining the feature on hover or keyboard focus.

### Can I trigger a backup or generate a report from the browser?
Yes, when signed in as operator or admin: a device page has "Back up now",
"Verify" and "Set baseline" buttons and a note form; the Health page has a
report generator (format + sign) and (admin) a config-reload button.

### Are there graphs/charts in the web UI?
Yes. The **Dashboard** page shows a coverage donut, a device-status bar,
backups-per-day and changes-per-day charts, policy findings by severity,
and storage. Each device page has a run-health timeline and a
runs-per-day chart. All charts are inline SVG rendered server-side — no
JavaScript, no external assets, and they adapt to light/dark themes.

### Is the PDF report just plain text?
No — the PDF has a title banner, KPI tiles (covered/stale/never/failing),
a vector bar chart of device status, and shaded tables, all drawn with
PDF vector operators (no reportlab). It's still stdlib-only and signable.
