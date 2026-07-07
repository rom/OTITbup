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
| `secrets.py` | `SecretsBackend` interface; `plainfile` and `encryptedfile` (Fernet) backends; Vault/CyberArk slot in later |
| `drivers/` | `Driver.collect(device, secrets) -> [Artifact]`; registry with lazy imports; read-only by contract. Ships `generic_file`, `generic_ssh`, `siemens_s7`, `rockwell_enip`, `schneider_modbus` |
| `discovery.py` | Opt-in sequential TCP probe of known OT/IT ports; emits an inventory-shaped YAML *proposal* for human review — never edits the inventory |
| `auth.py` | PBKDF2 password hashing and HTTP Basic verification for the web UI |
| `restore.py` | Guided restore: exports hash-verified artifacts + RESTORE.md checklist with driver-specific vendor-tool instructions; performs no device writes |
| `gitstore.py` | Local git repo; per-device commits; `manifest.yml` with sha256 fingerprints (change detection for binaries); optional push to remote |
| `runner.py` | Orchestration: per-zone concurrency semaphores, maintenance-window checks, change/failure alerts |
| `daemon.py` | Scheduler loop; per-device interval state in `state.json` |
| `alerts.py` | Webhook, syslog, email notifiers; failures logged, never fatal |
| `webui.py` | Read-only web UI (stdlib http.server): device dashboard, per-device history and diffs; optional HTTP Basic auth, warns when bound beyond loopback without it |
| `cli.py` | `validate`, `list`, `backup`, `diff`, `log`, `daemon`, `serve`, `discover`, `restore`, `passwd`, `secrets genkey/encrypt/decrypt` |

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
- Web UI TLS.
- git-lfs or artifact store for very large project files.
