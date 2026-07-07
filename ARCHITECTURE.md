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
    │          │ + windows  │   │  siemens_s7    (planned)     │
    ▼          └─────┬──────┘   │  rockwell_enip (planned)     │
┌──────────┐         │          │  schneider     (planned)     │
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
| `drivers/` | `Driver.collect(device, secrets) -> [Artifact]`; registry with lazy imports; read-only by contract in phase 1 |
| `gitstore.py` | Local git repo; per-device commits; `manifest.yml` with sha256 fingerprints (change detection for binaries); optional push to remote |
| `runner.py` | Orchestration: per-zone concurrency semaphores, maintenance-window checks, change/failure alerts |
| `daemon.py` | Scheduler loop; per-device interval state in `state.json` |
| `alerts.py` | Webhook, syslog, email notifiers; failures logged, never fatal |
| `cli.py` | `validate`, `list`, `backup`, `diff`, `log`, `daemon` |

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

## Phase 2+ (not yet implemented)

- Vendor drivers: `siemens_s7` (python-snap7 block upload + fingerprint),
  `rockwell_enip` (pycomm3 identity/program hash), `schneider_umas`.
- Web UI: read-only inventory/status viewer with diff browsing.
- Auto-discovery proposing YAML inventory additions for human review.
- Restore workflows (guided first, automated later).
- git-lfs or artifact store for very large project files.
