# OTITbup — Requirements

OT and IT backup and restore tool that extracts and versions configurations,
settings, logic and code from OT equipment (PLCs, RTUs, gateways, comms
equipment) and IT infrastructure (network equipment) into a versioned git
backup.

This document captures the requirements interview held on 2026-07-07.

## 1. Scope of supported equipment

### OT targets (all planned, starting vendor-agnostic)

- **Generic / file-based first**: vendor-agnostic core that ingests exported
  project files and pulls configs via generic transports (FTP/SFTP, HTTP,
  serial). This is the foundation the vendor drivers plug into.
- **Siemens**: S7-300/400/1200/1500 via S7comm / OPC UA; handling of TIA
  Portal project files.
- **Rockwell / Allen-Bradley**: ControlLogix / CompactLogix via EtherNet/IP;
  Studio 5000 project files (`.ACD`).
- **Schneider / Modicon**: M340 / M580 via Modbus/UMAS; EcoStruxure /
  Unity Pro project files.

### IT / network targets

- **Cisco** (IOS / IOS-XE): running/startup config via SSH.
- **Industrial switches**: Hirschmann, Moxa, Westermo.
- **Broad multi-vendor support** through a pluggable driver model
  (Netmiko/Scrapli/Oxidized-style), so new vendors are added as drivers, not
  core changes.

## 2. What is captured

For PLCs and OT devices, capture as much as the device/protocol allows,
in descending order of fidelity:

1. **Program logic/blocks as binary** where the protocol supports upload
   (e.g. S7 block upload) — not human-readable, but restorable and
   binary-diffable.
2. **Engineer-exported project files** — a watch folder and/or upload
   endpoint where TIA Portal, Studio 5000, and Unity projects are dropped
   and versioned alongside network-pulled data.
3. **Device configuration** — IP settings, hardware configuration,
   diagnostics/identification data.
4. **Metadata + fingerprint** — firmware version, program hash/checksum —
   so change is detected even when content cannot be pulled.

For network equipment: full configuration (running/startup) as text, which
diffs naturally in git.

## 3. Backup vs. restore

- **Phase 1: backup + diff.** Versioned git backup with change detection.
- **Restore is phase 2** — writing to PLCs is high-risk and deferred until
  the backup side is solid. The data model should preserve everything needed
  to restore later (binary blocks, project files, device identity).

## 4. Deployment model

- **On-prem server/appliance inside the plant network.** A central service
  reaches out to devices on schedule; suits air-gapped OT networks.
- **Scale**: start single-site with tens of devices, but the data model must
  be multi-site-ready from day one (site → zone → device hierarchy,
  per-site scheduling), so per-site collectors can be added later.

## 5. Technology

- **Language: Python.** Chosen for ecosystem fit: `python-snap7` (Siemens),
  `pycomm3` (Rockwell), Netmiko/Scrapli (network gear), GitPython.
- Pluggable driver architecture for both OT and IT device types.

## 6. Storage

- **Local bare git repo on the appliance** as the primary store, with
  **optional push to a remote** (GitLab/Gitea/GitHub) when a route exists.
  Self-contained by default; mirroring is additive.

## 7. Device inventory

- **Config files (YAML) as the source of truth**, versioned in git —
  infrastructure-as-code style (devices, driver type, credential refs,
  schedules, site/zone assignment).
- **Web UI as a read-only viewer** of inventory and status (does not edit
  the source of truth).
- **Auto-discovery** of subnets to help populate the inventory — with OT
  safeguards: opt-in, passive/limited probing, results proposed as YAML
  additions for human review rather than auto-added.

## 8. Credentials

- **Start simple, design for pluggable secret backends**: an encrypted
  local credentials file now, with a backend interface so HashiCorp Vault /
  CyberArk integration can be added without touching drivers.

## 9. Scheduling, alerting, UI

- **Scheduled polling per device/group** — cron-like schedules (e.g.
  network configs hourly, PLC uploads nightly).
- **Manual/on-demand runs** — trigger a backup before/after maintenance.
- **Change alerts** on detected diffs via email, syslog, and webhooks —
  doubles as unauthorized-change detection.
- **Web UI dashboard** with device list, backup history, and a diff viewer.

## 10. OT safety constraints

Standard careful defaults are acceptable for the first version, but the
scheduler must support from the start:

- **Maintenance windows / time restrictions** per device or group — only
  poll during defined windows.
- **Rate limiting and sequential polling per zone** — cap concurrent
  connections so backup traffic cannot disturb control traffic.
- Collection drivers are read-only in phase 1 by construction (no write
  paths shipped until restore is designed).

## Open questions for later

- Restore workflow design (phase 2): automated push vs. guided/manual with
  vendor tools.
- Retention policy / repo size management for large binary artifacts
  (git-lfs or artifact store?).
- Authentication/authorization for the web UI.
- Exact repo layout (per-site repo vs. single repo with site folders).
