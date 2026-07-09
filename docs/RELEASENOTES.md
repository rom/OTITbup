# OTITbup — Release notes

All notable changes to OTITbup, newest first. The project follows
[Keep a Changelog](https://keepachangelog.com/) conventions and semantic
versioning. Dates are the milestone the work landed on the development
branch; `0.1.0` is the current package version.

---

## Unreleased

### Added / changed — web UI polish & configurability

- **Per-user colour themes.** Each user picks a theme from the menu-bar
  dropdown — `auto` (follows the OS), `light`, `dark`, `sky`, `desert`,
  `autumn`, or `spring`. The theme **applies instantly** (no reload or tab
  switch); the choice then persists in the background. Preferences are kept
  in an on-disk store (`user-prefs.json`, next to the data directory) keyed
  by account, so they follow *every* signed-in identity — including
  config-declared and SSO/LDAP users, not just UI-created accounts — across
  browsers and devices. A browser cookie is set as a fallback. `webui.theme`
  sets the default for users who haven't chosen one.
- **Login page** is now a dedicated, centred sign-in card with a **Login**
  heading and button, shown *without* the app menu bar — no navigation is
  exposed until you are authenticated.
- **Archived, versioned reports.** Generated compliance reports are written
  to a dedicated `reports/` subdirectory (next to the data directory) under
  timestamped names (`compliance-YYYYMMDD-HHMMSS.<ext>`), so every run is
  retained rather than overwriting the last. A new **Reports** page in the
  web UI lists them newest-first (format, size, signed badge), links each for
  inline viewing/download, and offers a generate control for operators. The
  CLI `report` command and the scheduled daemon report both archive here;
  `--out`/`reports.out` still writes an extra copy to a fixed path.
- **Syslog transport is selectable** for the events sink: `events.syslog.
  protocol` = `udp` (default), `tcp`, or `tls` (RFC 5425/6587; honours
  `cafile`/`verify`).
- **Config editor** now pre-fills sane example/default placeholders on nearly
  every field, renders dropdowns for fixed-choice settings (transport,
  theme, syslog protocol, roles, log level/format, …), and splits the former
  "Web UI & SSO" panel into separate **Web UI** and **SSO** sections.
- The menu bar shows **who is signed in** (username + role).
- **Menu order:** Dashboard now comes before Devices.
- **Logs menu.** The log views are grouped under a single **Logs** dropdown
  with three entries: **Audit logs** (access/change trail), **Backup logs**
  (commit history) and a new **Event logs** page.
- **Event logs.** A persistent operational log of the messages surfaced to
  operators — backup failures *with the full driver error text*, anomalies,
  integrity results, config reloads, logins — newest-first, colour-coded by
  severity, with an errors-&-warnings-only filter. Backed by a new `events`
  table in the run store (the audit chain only kept a short detail, so the
  full message a user saw was previously not retained anywhere queryable).
- **Graceful web-UI startup failures.** `otitbup serve` no longer dumps a
  socket traceback when the address is unavailable. A port already in use
  (another `otitbup serve` already running), a privileged port, or an
  unavailable host address now produce a clear one-line message with a fix
  and a non-zero exit, and the failure is recorded in the Event log.
- **Copy-pasteable install hints.** Optional-dependency error messages and
  the docs now quote the pip extras (`pip install 'otitbup[siemens]'`) so
  they work as-is in zsh, where the unquoted `otitbup[siemens]` is treated
  as a glob and fails with `no matches found`.
- **Fixed:** clicking **Logout** in the menu (a GET link) rendered a
  "not found" page — logout is now handled over GET and redirects to the
  sign-in page.

### Added — data integrity, preservation & chain of custody

A hardening batch focused on proving backups are correct, complete and
preserved over time.

- **Continuous integrity scrubbing.** New `integrity` command runs a
  one-pass scrub — content (`verify`), repository (`git fsck`), and
  optionally signed-commit signatures — and the daemon runs it on a
  schedule (`integrity.interval_days`), persisting the result, emitting
  `integrity.ok`/`integrity.error` events, and alerting on failure. Exposed
  on `/api/status` and `/metrics` (`otitbup_integrity_ok`).
- **Git-repository corruption detection** via `git fsck` (in the scrub and
  standalone).
- **Capture-quality guards.** A capture that produced no artifacts, is below
  `capture.min_bytes` (global or per-device `options.min_bytes`), or is
  missing required content (`capture.expect_match` / `options.expect_match`)
  is rejected before it enters the archive. New **size_drop** anomaly flags
  a capture far below the device's trailing median (captured size is now
  recorded per run).
- **Per-device GUID + provenance.** Every device has a GUID (config `guid:`
  or a stable UUIDv5); new `guids [--assign]` command pins persistent ones.
  Each backup records provenance in the manifest (`provenance: {device_guid,
  driver}` alongside `artifacts:`) and the commit trailers (`Device-GUID`,
  `Driver`, `Tool-Version`, `Appliance`, `Captured-At`).
- **Schedulable from the daemon:** offsite push (`offsite.interval_days`),
  integrity scrub, and restore rehearsals across all devices
  (`rehearsal.interval_days`).
- **Blob key rotation** (`blobkey rotate|genkey`) — re-encrypt every blob to
  a new key (or enable/disable encryption), names unchanged, plaintext hash
  re-verified. Optional **compression at rest** (`encryption.compress`).
- **Immutability & retention protection:** legal holds (`hold set|clear|
  list`) exempt a scope from pruning; a retention lock (`retention.lock_days`)
  keeps everything within a minimum-retention/WORM window; guidance for an
  append-only/object-locked off-appliance copy.
- The web Config editor gains all the new settings (capture, integrity,
  rehearsal, retention lock, compression, offsite interval).

### Added — config editor expansion, generic FTP/HTTPS, site-collector guide

- **Config editor covers more settings.** The web Config page now also edits
  the secrets backend, global retention defaults, alerts (staleness,
  rate-limit, email, webhooks), retry/backoff, pre/post hooks, anomaly
  thresholds, git housekeeping, config-as-code (desired), scheduled
  reports, and 3-2-1 strategy — plus add/remove of **federation
  collectors** for the central roll-up.
- **Comments are always preserved** on GUI saves: `ruamel.yaml` is now a
  declared dependency, so a hand-commented config survives round-trips.
- **`generic_ftp` driver** — read-only file/directory/glob fetch over
  FTP/FTPS (stdlib `ftplib`), the plain-FTP sibling of `generic_sftp`, for
  older and embedded devices.
- **`generic_https` driver + one-flag TLS.** Any `generic_http`-based driver
  can now be flipped to HTTPS with `options.https: true` (scheme-less and
  `http://` URLs are upgraded to `https://`); `generic_https` defaults to
  that.
- **`docs/SITECOLLECTOR.md`** — a detailed guide to setting up, configuring,
  using and supervising site collectors / Purdue-model federation, linked
  from the in-app Help.
- Extended the README **Tests** section.

### Added — web config editor, HMI/IED/RTU drivers, more examples

- **Configure the whole tool from the web UI.** A new admin-only **Config**
  page edits the configuration without hand-editing YAML: global settings
  (offsite targets/URIs, SSO/LDAP, events, logging, integrations,
  encryption/signing/TLS keys & certs) and the full inventory per
  **site / zone / device** — name, driver, address, schedule, credentials,
  maintenance window, timezone, concurrency and retention, plus add/delete
  of devices, zones and sites. Every save is validated through the real
  config loader before writing (invalid edits are refused, the file left
  untouched), the previous version is kept as `<config>.bak`, and the
  process reloads immediately. Comments and key order are preserved on save
  (via `ruamel.yaml`, now a declared dependency). CSRF-protected and audited.
- **More HMI/SCADA, substation IED and RTU drivers (124 total).** HMI/SCADA:
  AVEVA Edge, zenon, Movicon, FactoryTalk View SE, VTScada, ClearSCADA/Geo
  SCADA, WinCC Unified, Reliance. Protection relays / IEDs (IEC 61850 MMS
  where supported, else web/CLI): SEL, Siemens SIPROTEC, ABB Relion, GE
  Multilin, Schneider MiCOM, NR Electric, NARI. RTUs: Kingfisher, Motorola
  ACE, SATEC (plus the earlier Emerson ROC/FloBoss, Survalent). Each reuses
  an existing capture core and is honest about what an open protocol
  exposes.
- **More example configs.** `examples/` gains ready-to-adapt scenarios:
  `minimal`, `network-only`, `substation`, `multi-site-federation`,
  `cloud-offsite`, `high-security`, and a `hardening-checklist.md`.
- **Extended FAQ** — roughly doubled, with longer, more detailed answers.

### Fixed

- The web server no longer dumps a traceback when a client drops its
  connection (closed tab, dropped SSE stream, health probe); the benign
  `ConnectionResetError`/`BrokenPipeError` is logged at debug instead.

### Added — offsite, drivers, in-app docs, more anomalies

- **Encrypted offsite copies.** A new `offsite` command keeps an encrypted
  snapshot of the whole backup (git repo + blob store + run store) on an
  external server or cloud object store — the "1 offsite" leg of 3-2-1,
  hardened. The snapshot is a single tarball encrypted with a Fernet key
  *before* it leaves the appliance, so the remote only ever holds
  ciphertext and never the key.
  - Transports: `file` (a directory / NFS or SMB mount / removable media),
    `sftp` (any SSH server, needs `otitbup[sftp]`), and `s3` (AWS S3 or any
    S3-compatible store — MinIO, Backblaze B2, Wasabi, Ceph RGW — signed
    with SigV4 over the standard library, no boto3).
  - `offsite genkey | push | list | pull` and `offsite restore DEVICE`,
    which pulls a snapshot and produces a hash-verified restore bundle for
    one device straight from the offsite copy, with no device writes.
  - Path-traversal-safe extraction; wrong-key pulls fail loudly.
- **More device drivers (21 new; 105 total).**
  - Network profiles: `aruba_osswitch`, `nokia_sros`, `stormshield`,
    `phoenix_mguard` (plus the enterprise set: Juniper, Arista, Cisco
    NX-OS, HPE Comware, Huawei VRP, MikroTik RouterOS, FortiGate, PAN-OS,
    Extreme EXOS, Dell OS10, Check Point Gaia).
  - Protocol/appliance drivers: `yokogawa_web`, `honeywell_web`,
    `fanuc_cnc`, `bachmann_m1`, `br_automation`, `emerson_roc`. Each reuses
    an existing capture core (HTTP/SFTP/DNP3) and is honest in its docstring
    about what it can and cannot reach over an open protocol.
- **In-app documentation viewer.** The web UI Help page now links to the
  full manuals (Usage, Configuration, FAQ, Release notes) rendered in-app
  from the shipped Markdown, via a small dependency-free Markdown renderer
  (`mdrender.py`) that HTML-escapes all content and blocks `javascript:`
  links.
- **More anomaly detectors.** Added **flapping** (a device oscillating
  between success and failure — intermittent, caught by neither the failure
  nor recovery alert) and **slow-trend** (a gradual, sustained slowdown a
  single-point z-score would miss), alongside the existing slow-backup and
  change-storm detectors.

### Changed

- The daemon now runs periodic `git gc` (`housekeeping.gc_interval_days`).
- Documentation and the example config updated for all of the above.

---

## 0.1.0 — Reliability, security, observability and CI/CD

A large batch turning the tool from "backs things up" into an auditable,
hardened appliance.

### Added — reliability

- **Run-store schema migrations** (`PRAGMA user_version`, append-only) so
  upgrades never lose operational history.
- **Connectivity test / dry run** — `otitbup test` and `backup --dry-run`
  collect from a device without committing, to validate reachability and
  credentials.
- **Retry with backoff** on transient collection failures (`retry.attempts`,
  `retry.backoff`).
- **Recovery notifications** when a previously failing device backs up
  successfully again.
- **Timezone-aware maintenance windows** (`zone.timezone`, via `zoneinfo`).
- **Pre/post hooks** — shell commands around each device backup, global or
  per-device, with device context in the environment.
- **Log management** — text or JSON logging with a rotating file
  (`logging.*`).
- **Git housekeeping** — `otitbup gc` to repack/prune the backup repo.
- **Cron-expression schedules** — a device `schedule` accepts a 5-field
  cron expression in addition to the interval form (`12h`, `1d`).

### Added — security & hardening

- **Encryption at rest for the blob store** (Fernet). Blobs stay
  content-addressed by the *plaintext* sha256, so dedup and manifests are
  unchanged; only the on-disk bytes are ciphertext. Repo, run store and
  secrets rely on full-disk encryption (documented).
- **Signed commits / signed backup history** — SSH-signed commits via
  `git.sign.key_file`.
- **Tamper-evident audit log** — the audit log is sha256 hash-chained;
  `otitbup verify-audit` detects any edit or deletion.
- **Per-site/per-zone RBAC scoping** — users, sessions and API tokens carry
  space-separated scope globs (`plant-a/*`), enforced on all write actions.
- **Scoped write API** — `POST /api/device/<qn>/{backup,verify}` with
  Bearer tokens (managed by `otitbup token`), operator+ role, scope checks,
  and no CSRF for token auth.
- **Enterprise auth** — LDAP/AD bind login plus trusted-header SSO for
  OIDC/SAML behind a reverse proxy.

### Added — observability & multi-site

- **Anomaly detection** — statistical detectors (slow backups, change
  storms) after every backup and via `otitbup anomalies`, emitting an
  `anomaly.detected` event.
- **Live UI updates** — a Server-Sent Events endpoint (`/events/stream`)
  and a live activity panel.
- **Config-as-code drift** — `otitbup desired` compares live backups
  against intended configs declared in a directory.
- **Site-collector / Purdue-model federation** — `otitbup federation` rolls
  up health from per-site collector appliances (backup bytes federate over
  plain git; health federates over the read-only API).

### Added — quality gates

- **CI/CD** — ruff lint configuration and a GitHub Actions workflow (lint,
  a Python 3.11–3.13 test matrix, and an sdist/wheel build with a
  console-script smoke test).

---

## Earlier milestones

The features below shipped incrementally while the tool took shape.

### Compliance, recovery & operations

- Compliance reports (HTML/CSV/PDF/DOCX, optionally Ed25519-signed) and a
  hand-rolled PDF/DOCX writer with no heavy dependencies.
- Guided restore bundles (hash-verified, with a `RESTORE.md` checklist),
  network-device restore (dry-run first), DR runbooks, and tracked restore
  rehearsals.
- 3-2-1 / 3-2-1-1-0 strategy evaluation and a portable `export` archive.
- Retention with a deduplicated, content-addressed blob store; large
  artifacts offload and expire by policy.
- Golden-config baselines and drift, config policy/compliance linting,
  change annotations, and a maintenance-mode signal for expected changes.

### Web UI & integrations

- Read/write web UI with cookie sessions, CSRF protection, roles, per-page
  audit, dashboards and per-device graphs, search across the latest backup
  of every device, and TLS with a self-signed cert generator.
- Event system fanning out to syslog and hand-encoded SNMPv2c traps
  (bundled MIB), plus ServiceNow/Jira/RT ticket creation.
- Prometheus `/metrics` and JSON `/api/*` endpoints.
- NetBox reconciliation/import and network discovery with review-first
  proposals.
- Encrypted secrets (file, HashiCorp Vault, CyberArk CCP).
- `help` and `explain` commands and `python -m otitbup`.

### Device coverage

- PLCs and controllers: Siemens S7, Rockwell/Allen-Bradley Logix,
  Schneider/Modicon, Mitsubishi MELSEC, Omron, Beckhoff TwinCAT, WAGO,
  Phoenix PLCnext, GE/Emerson PACSystems, Codesys-based controllers.
- HMI/SCADA & substation IEDs: Ignition, WinCC, FactoryTalk View,
  Wonderware, iFIX, Citect, WinCC OA, Kepware; IEC 61850 MMS.
- RTUs and gateways: DNP3, SEL RTAC, Siemens SICAM, ABB RTU500-series,
  Netcontrol, and many protocol gateways.
- Network equipment: Cisco IOS/ASA, Siemens SCALANCE, RUGGEDCOM ROS/ROX,
  Hirschmann/Belden, Moxa, Westermo, Advantech, Netgear, and more.
- Generic transports: SSH (netmiko), SFTP, HTTP export, OPC UA, EtherNet/IP,
  DNP3, SNMP fingerprint, and watch-folder file ingest.

### Foundations

- Pluggable read-only drivers, versioned git storage, a scheduler daemon
  with maintenance windows and per-zone concurrency caps, alerting, and the
  CLI — the phase-1 MVP.
