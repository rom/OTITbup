# OTITbup — Architecture

Implements the decisions in [REQUIREMENTS.md](REQUIREMENTS.md). Python
3.11+, minimal core dependency (PyYAML); vendor protocol libraries are
optional extras so the appliance only carries what it uses.

```
otitbup.yml (inventory, source of truth, versioned by the operator)
    │
    ▼
┌──────────┐   ┌───────────┐   ┌──────────────────────────────┐
│  config   │──▶│  Runner   │──▶│ drivers (pluggable, lazy)    │
│  loader   │   │ per-zone  │   │  generic_file  (watch dir)   │
└──────────┘   │ semaphores │   │  generic_ssh   (netmiko)     │
    │          │ + windows  │   │  siemens_s7    (snap7)       │
    ▼          └─────┬──────┘   │  rockwell_enip (pycomm3)     │
┌──────────┐         │          │  schneider_modbus (stdlib)   │
│ secrets   │────────┤          └──────────────────────────────┘
│ backends  │        ▼
└──────────┘   ┌───────────┐        ┌────────────────────────┐
               │ GitStore  │───────▶│ alerts: webhook /      │
               │ local repo │ diff?  │ syslog / email         │
               │ (+push)    │        └────────────────────────┘
               └───────────┘
```

## Modules (`src/otitbup/`)

| Module | Responsibility |
|---|---|
| `models.py` | Site → Zone → Device dataclasses; multi-site-ready hierarchy |
| `config.py` | YAML inventory loading with load-time validation (schedules, windows, duplicates) |
| `windows.py` | Interval schedules (`30m`, `4h`, `1d`) and maintenance windows (`22:00-06:00`, overnight-aware) |
| `secrets.py` | `SecretsBackend` interface; `plainfile`, `encryptedfile` (Fernet), `vault` (HashiCorp KV v2, stdlib HTTP) and `cyberark` (CCP REST, incl. client-cert auth) backends |
| `drivers/` | `Driver.collect(device, secrets) -> [Artifact]`; registry with lazy imports; read-only by contract. PLC: `siemens_s7`, `rockwell_enip`, `schneider_modbus`, `mitsubishi_mc`, `omron_fins`, `beckhoff_ads`, `generic_opcua`, `generic_file`. RTU: `generic_dnp3`, `sel_terminal`, `siemens_sicam`, `abb_rtu520`/`abb_rtu560`. Network: `generic_ssh` + vendor profiles and `generic_http` for web-managed gear — run `otitbup drivers` for the full list |
| `discovery.py` | Opt-in sequential TCP probe of known OT/IT ports; emits an inventory-shaped YAML *proposal* for human review — never edits the inventory |
| `auth.py` | PBKDF2 password hashing and HTTP Basic verification for the web UI |
| `restore.py` | Guided restore: exports hash-verified artifacts + RESTORE.md checklist with driver-specific vendor-tool instructions; performs no device writes |
| `blobstore.py` | Content-addressed store for large artifacts (sha256, deduplicated); pointer files go into git |
| `retention.py` | Hierarchical retention policies (device > zone > site > global), prune planning/apply; never rewrites git history |
| `runstore.py` | SQLite: backup run results, restore rehearsals, maintenance state — the source for status, metrics and reports |
| `verify.py` | Backup verification job: re-hash artifacts against manifests, check blob integrity |
| `policy.py` | Config policy/compliance checks (built-in + custom rules) over captured text configs |
| `metrics.py` | Prometheus text metrics and JSON status |
| `reports.py` | HTML compliance reports and per-site DR runbooks |
| `netrestore.py` | Automated restore for network gear (netmiko), dry-run default, pre/post verify; PLCs stay guided |
| `baseline.py` | Golden-config baselines and drift (latest vs. approved commit) |
| `reconcile.py` | Inventory reconciliation: inventory vs. network scan (unmanaged / unreachable) |
| `filelock.py` | Cross-process advisory lock (flock) around the backup critical section |
| `scaffold.py` | `otitbup init` — starter config/secrets scaffolding |
| `events.py` | Event bus: audit + Python log + syslog + SNMPv2c traps (stdlib BER encoder) |
| `sessions.py` | In-memory web sessions (cookie login/logout, CSRF token) |
| `snmp.py` | Minimal SNMPv2c GET client + BER decoder (fingerprint driver, discovery enrichment) |
| `tickets.py` | Ticketing hooks (ServiceNow/Jira/RT/generic) fired from the event bus |
| `netbox.py` | NetBox CMDB integration: reconcile inventory / import proposal |
| `reportfmt.py` | CSV/PDF/DOCX report rendering (stdlib) |
| `signing.py` | Detached Ed25519 report signatures |
| `strategy.py` | 3-2-1 / 3-2-1-1-0 backup-strategy evaluation |
| `charts.py` | Inline-SVG charts (donut/bar/stacked/timeline) for the web UI, theme-aware, CSP-safe |
| `pdfcanvas.py` | Minimal vector PDF canvas (rects, lines, text, colour) for rich report graphics |
| `gitstore.py` | Local git repo; per-device commits; `manifest.yml` with sha256 fingerprints (change detection for binaries); optional push to remote |
| `runner.py` | Orchestration: per-zone concurrency semaphores, maintenance-window checks, change/failure alerts |
| `daemon.py` | Scheduler loop; per-device interval state in `state.json` |
| `alerts.py` | Webhook, syslog, email notifiers; failures logged, never fatal |
| `webui.py` | Read/write web UI (stdlib http.server): dashboard with tiles/zone grouping/filtering, activity feed, driver catalog, per-device artifacts + history, per-commit diffs, raw artifact viewing; optional HTTP Basic auth and TLS, warns when bound beyond loopback without auth |
| `tlscert.py` | Self-signed EC P-256 certificate generation for the web UI (`otitbup certgen`) |
| `cli.py` | `validate`, `list`, `drivers`, `backup`, `diff`, `log`, `daemon`, `serve`, `discover`, `restore`, `passwd`, `certgen`, `secrets genkey/encrypt/decrypt` |

## Key design points

- **Repo layout**: `sites/<site>/<zone>/<device>/…` — one repo, one commit
  per device per change, so `git log -- sites/plant-a/cell-1/plc-01` is the
  device's full history.
- **Fingerprints**: every backup writes `manifest.yml` (sha256 per
  artifact). Even opaque binary uploads produce a meaningful diff signal.
- **OT safety**: maintenance windows are validated at config load, enforced
  per zone; `max_concurrent` caps simultaneous connections per zone
  (default 1 = strictly sequential). `backup --force` overrides windows for
  supervised on-demand runs.
- **Drivers are lazy-loaded** so `otitbup[ssh]`, `[siemens]`, `[rockwell]`
  extras are only needed for the drivers in use. Custom drivers can be
  referenced as `module.path:ClassName` directly in the config.
- **State separation**: scheduler state (`state.json`) and secrets live
  next to — not inside — the backup repo, keeping history clean.

## The siemens_s7 driver

Degrades gracefully by fidelity level: program blocks as binary MC7 via
block upload (S7-300/400, unprotected CPUs), always CPU identification
(`cpu_info.yml`: module, serial, order code, firmware, state) and a program
fingerprint (`fingerprint.yml`: sha256 across uploaded blocks). Protected
CPUs and S7-1200/1500, which refuse upload, still yield metadata +
fingerprint, and every degradation is recorded in `collection_notes` so a
partial backup is visible in the diff rather than silent.

## The rockwell_enip driver

The .ACD project cannot be pulled over EtherNet/IP, so Studio 5000 exports
are versioned via generic_file. Over the wire the driver captures
controller identity (`controller_info.yml`: product, revision, serial,
keyswitch, program/task names), the full tag list (`tags.yml`), and a
program fingerprint over tags + programs + revision — a reliable change
signal without the project file.

## The schneider_modbus driver

UMAS (Unity's program-upload protocol) is proprietary, so program logic is
versioned via generic_file exports. Over open Modbus TCP the driver reads
Device Identification (function 0x2B/0x0E, implemented on stdlib sockets —
no dependency): vendor, product, revision, and `UserApplicationName`,
which Modicon CPUs set to the loaded project's name, giving a genuine
program-change signal. Unsupported identification categories degrade to
`collection_notes`, never a failed backup.

## PLC identity drivers (Mitsubishi, Omron, Beckhoff, OPC UA)

Where a vendor's program-upload protocol is closed, the drivers follow the
fingerprint level of the capture ladder — identity + hash, projects via
generic_file:

- `mitsubishi_mc` — MC protocol 3E binary frames on stdlib sockets: CPU
  model name and model code (command 0x0101). Q/L/iQ-R/iQ-F CPUs.
- `omron_fins` — FINS/TCP on stdlib sockets (handshake + CPU UNIT DATA
  READ): controller model and firmware version. CJ/CS/CP, NJ/NX.
- `beckhoff_ads` — pyads: ADS device name, TwinCAT version, Run/Stop
  state. Requires an ADS route configured on the target.
- `generic_opcua` — asyncua: BuildInfo (manufacturer, product, software
  version) + namespace array from any OPC UA server, plus optional
  configured node reads. One driver fingerprints every modern controller
  exposing OPC UA (S7-1200/1500, NJ/NX, Beckhoff, WAGO, B&R, ...).
- `generic_enip` — stdlib EtherNet/IP: one ListIdentity (0x0063) returns
  the CIP Identity Object (vendor, device type, product code, revision,
  serial, product name) from any EtherNet/IP device. Status/state are
  recorded but excluded from the fingerprint so run-mode changes don't
  churn diffs. `ge_pacsystems`/`emerson_pacsystems` are aliases for
  PACSystems RX3i/RSTi-EP with EtherNet/IP enabled; SRTP-only legacy GE
  CPUs stay with PAC Machine Edition exports via generic_file.
- `generic_sftp` (paramiko) — fetches remote files/dirs/globs from
  Linux-based controllers, capturing the deployed boot project itself
  rather than a fingerprint. Presets: `wago_pfc` (/home/codesys and
  /home/codesys3), `phoenix_plcnext` (/opt/plcnext/projects),
  `codesys_ssh` (vendor-neutral, paths required). Missing preset paths
  are noted, not fatal — runtime dirs vary by firmware.

## RTU drivers

- `generic_dnp3` — a minimal, read-only DNP3 client on stdlib sockets
  (link CRC verified against the published test vector): one READ of
  group 0 device attributes (g0v254) returns vendor, product, serial,
  software/hardware versions from any conforming outstation. Unsolicited
  responses are skipped, multi-fragment responses reassembled; no writes,
  no time sync, no confirmations.
- `sel_terminal` — SSH profile issuing SEL terminal commands (ID, STA,
  SHO) for RTAC and protection relays, with date/time scrubbing.
- `siemens_sicam` / `abb_rtu520` / `abb_rtu560` — the SICAM A8000 and ABB
  RTU500-series web servers via the generic_http machinery (set your
  firmware's export/diagnostic URLs). Full configurations remain with
  SICAM TOOLBOX II / RTUtil500 exports through generic_file.

## Network equipment: SSH profiles and HTTP export

`network_profiles.py` layers vendor presets over the shared SSH core
(`generic_ssh.collect_ssh`): each profile fixes the netmiko device_type,
the commands to capture, and `scrub` regexes that strip volatile lines
(uptime, "last configuration change" stamps, ntp clock-period,
ASA Cryptochecksum) so diffs only show real changes. Profiles register
themselves into the driver registry from the PROFILES table, so the two
can never drift; `otitbup drivers` lists them all with descriptions.

Coverage spans switches, routers and firewalls per vendor: Cisco
(IOS/IOS-XE + ASA firewalls), Siemens SCALANCE (X-switches, M-series
cellular routers, S/SC firewalls on one CLI), RUGGEDCOM (ROS switches
whose whole config lives in `config.csv`; ROX II routers/firewalls),
Hirschmann/Belden (HiOS and Classic switches, EAGLE firewalls), Moxa
(EDS switches, EDR secure routers/firewalls), Westermo (WeOS
switches/RedFox routers, Merlin 4G/5G), Advantech (EKI switches, ICR
cellular routers), Netgear (M4300/ProSAFE switches; their
routers/firewalls are web-managed → generic_http), and Omron switches
(no router/firewall product line). Industrial firmware lines vary, so
every profile field is overridable per device via options.

Moxa NPort serial-to-ethernet converters are web-managed, not CLI devices:
`moxa_nport` (an alias of `generic_http`) fetches the configuration from
the device's export endpoint over HTTP(S) with Basic/Digest auth,
stdlib-only, deliberately bypassing any proxy environment so backup
traffic never leaves the OT network.

## Discovery

`otitbup discover <cidr>` is the only path — it never runs automatically.
Probes are TCP connects only (no payloads, no UDP broadcast), strictly
sequential with a configurable delay, against a short list of well-known
ports (102 → siemens_s7, 44818 → rockwell_enip, 502 → schneider_modbus,
22 → generic_ssh). Addresses already in the inventory are skipped. Output
is a commented, inventory-shaped YAML proposal the operator reviews and
merges by hand.

## Restore (phase 2, guided)

`otitbup restore <device> [--commit SHA] --out DIR` exports the artifacts
of a chosen backup, verifies every file against the sha256 manifest
recorded at backup time, and writes RESTORE.md: an MOC-style checklist
plus driver-specific instructions for performing the restore with vendor
tools (TIA Portal, Studio 5000, EcoStruxure, or network-gear config
load). otitbup itself never writes to a device — the read-only driver
contract holds even in the restore workflow.

## Later

- Automated restore paths where a vendor-supported, safe write API exists.
- git-lfs or artifact store for very large project files.
