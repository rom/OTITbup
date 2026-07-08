# OTITbup

OT and IT backup and restore tool that can extract and save configurations,
settings, logic and code etc from OT equipment (PLC, RTU, gateways, com
equipment), IT infrastructure equipment (network equipment), in a versioned
git backup.

- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — scope and decisions from
  the requirements interview
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module layout and design
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — configuration file
  reference (`otitbup.yml`, secrets, web UI auth/TLS, alerts)

## Quick start

```bash
pip install -e .[dev]          # core + tests
pip install -e .[ssh]          # + netmiko for network equipment

cp examples/otitbup.yml examples/secrets.yml .
$EDITOR otitbup.yml secrets.yml

otitbup init                   # scaffold a starter otitbup.yml + secrets.yml
otitbup validate               # check the config
otitbup list                   # show the inventory
otitbup drivers                # list all drivers with descriptions
otitbup backup                 # back up everything now
otitbup backup core-sw-01      # one device
otitbup diff core-sw-01        # latest change
otitbup log core-sw-01         # backup history
otitbup daemon                 # run the scheduler
otitbup serve                  # web UI on 127.0.0.1:8080
otitbup passwd                 # hash a web UI password (webui.auth)
otitbup certgen                # self-signed TLS pair (webui.tls)
otitbup discover 10.20.0.0/24  # sequential scan -> YAML proposal for review
otitbup restore plc-01 --out ./bundle   # hash-verified restore bundle
otitbup retention              # show effective policies + prune dry run
otitbup retention --apply      # delete expired large-artifact blobs
otitbup status                 # per-device backup health (last success, fails)
otitbup verify                 # re-hash stored backups against manifests
otitbup policy                 # config policy/compliance findings
otitbup annotate plc-01 "MOC-1234"      # attach a change note to a commit
otitbup maintenance plant-a/cell-1/* --hours 8   # changes now "expected"
otitbup report --out report.html        # HTML compliance report
otitbup dr-plan plant-a        # HTML disaster-recovery runbook for a site
otitbup net-restore core-sw-01 [--apply]   # push a stored config (dry run default)
otitbup rehearse plc-01 --by me         # record a restore-rehearsal result
otitbup backup --site plant-a --zone cell-1     # back up a site/zone subset
otitbup diff plc-01 --from <commit>     # diff any two commits
otitbup search "vlan 30"                # search across the latest config of all devices
otitbup baseline set plc-01             # approve current config as the golden baseline
otitbup baseline drift                  # devices that have drifted from baseline
otitbup reconcile 10.20.0.0/24          # inventory vs. network (coverage gaps)
```

### Encrypted secrets

```bash
pip install -e .[crypto]
otitbup secrets genkey --out otitbup.key
otitbup secrets encrypt secrets.yml secrets.enc --key-file otitbup.key
otitbup secrets decrypt secrets.enc --key-file otitbup.key   # to view/edit
```

Then point the config at it (`backend: encryptedfile`, `path: secrets.enc`,
`key_file: otitbup.key` — or provide the key via `OTITBUP_KEY`).

**HashiCorp Vault** (KV v2) and **CyberArk CCP** are supported as secret
backends too — stdlib HTTP clients, no extra dependencies; see
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

### Retention

Text configs stay in git forever (cheap); artifacts above a size
threshold are offloaded to a deduplicated blob store and expire by
policy — `keep_versions` / `keep_days`, settable globally and overridden
per site, zone, or device. `otitbup retention` shows every device's
effective policy and prunes with `--apply`; the web UI's Retention page
shows the same. Git history is never rewritten. See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Supported equipment

Every driver is read-only by contract. Fidelity varies by what each
vendor's protocol allows: **full content** (configuration or program
files land in the repo) or **identity + fingerprint** (change detection;
the full project is versioned via engineering-tool exports with
`generic_file`). All defaults — ports, commands, paths — are overridable
per device; see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

### PLCs and controllers

| Vendor | Equipment | Driver | Captures | Extra install |
|---|---|---|---|---|
| Siemens | S7-300/400 (and unprotected CPUs) | `siemens_s7` | program blocks (MC7) + CPU info + fingerprint | `otitbup[siemens]` |
| Siemens | S7-1200/1500, protected CPUs | `siemens_s7` | CPU info + fingerprint (upload refused is noted, not fatal) | `otitbup[siemens]` |
| Rockwell / Allen-Bradley | ControlLogix, CompactLogix | `rockwell_enip` | controller identity, tag list, program fingerprint | `otitbup[rockwell]` |
| Schneider / Modicon | M340, M580, Quantum, Premium | `schneider_modbus` | device identification incl. loaded application name + fingerprint | — (stdlib) |
| Mitsubishi | MELSEC Q, L, iQ-R, iQ-F | `mitsubishi_mc` | CPU model + fingerprint (MC protocol) | — (stdlib) |
| Omron | CJ, CS, CP, NJ, NX | `omron_fins` | controller model + firmware + fingerprint (FINS) | — (stdlib) |
| Beckhoff | CX / TwinCAT 2 & 3 | `beckhoff_ads` | device name, TwinCAT version, run state + fingerprint | `otitbup[beckhoff]` |
| WAGO | PFC100, PFC200 | `wago_pfc` | **deployed boot project files** via SFTP | `otitbup[sftp]` |
| Phoenix Contact | PLCnext (AXC F, RFC) | `phoenix_plcnext` | **deployed project** (/opt/plcnext/projects) via SFTP | `otitbup[sftp]` |
| Codesys family (Festo, Bosch Rexroth ctrlX, ...) | Linux-based controllers | `codesys_ssh` | project/settings files via SFTP (paths per device) | `otitbup[sftp]` |
| GE / Emerson | PACSystems RX3i, RSTi-EP | `ge_pacsystems` | CIP identity + fingerprint (EtherNet/IP enabled) | — (stdlib) |
| Any vendor | OPC UA server exposed | `generic_opcua` | build info, namespaces, optional nodes + fingerprint | `otitbup[opcua]` |
| Any vendor | EtherNet/IP device | `generic_enip` | CIP identity + fingerprint | — (stdlib) |
| Any vendor | engineering-tool project exports (TIA Portal, Studio 5000, EcoStruxure, GX Works, Sysmac, PME, ...) | `generic_file` | **full project files** from a watch folder | — |

### RTUs

| Vendor | Equipment | Driver | Captures | Extra install |
|---|---|---|---|---|
| Any DNP3 vendor (Schneider SCADAPack, GE, Kingfisher/Semaphore, ...) | DNP3 outstations | `generic_dnp3` | device attributes (vendor, model, serial, versions) + fingerprint | — (stdlib) |
| SEL | RTAC 3530/3505, protection relays | `sel_terminal` | ID / STA / SHO terminal output (settings) | `otitbup[ssh]` |
| Siemens | SICAM A8000 (CP-8000/8021/8022/8050) | `siemens_sicam` | web-server endpoints (diagnostics/parameters) | — (stdlib) |
| ABB | RTU520, RTU540, RTU560 (RTU500 series) | `abb_rtu520` / `abb_rtu560` | web-server endpoints (status/config downloads) | — (stdlib) |

### Network equipment

| Vendor | Switches | Routers | Firewalls |
|---|---|---|---|
| Cisco | `cisco_ios` | `cisco_ios` | `cisco_asa` |
| Siemens SCALANCE | `siemens_scalance` | `siemens_scalance` (M-series 4G/5G) | `siemens_scalance` (S/SC) |
| Siemens RUGGEDCOM | `ruggedcom_ros` | `ruggedcom_rox` | `ruggedcom_rox` |
| Hirschmann | `hirschmann_hios`, `hirschmann_classic` | — | `hirschmann_eagle` |
| Belden | `belden_switch` (Hirschmann family) | — | `hirschmann_eagle` |
| Moxa | `moxa_switch` (EDS) | `moxa_edr` | `moxa_edr` |
| Moxa NPort (serial-to-ethernet) | — | `moxa_nport` (HTTP export) | — |
| Westermo | `westermo_weos` | `westermo_weos` (RedFox), `westermo_merlin` (4G/5G) | `westermo_weos` |
| Advantech | `advantech_switch` (EKI) | `advantech_router` (ICR) | — |
| Netgear | `netgear_switch` (M4300/M4250/ProSAFE) | web-managed → `generic_http` | web-managed → `generic_http` |
| Omron | `omron_switch` | — (no router/firewall line) | — |

All SSH profiles capture full configurations with volatile-line
scrubbing (uptime, "last change" stamps) so diffs only show real
changes. They need `otitbup[ssh]`.

### Generic transports

| Driver | Use it for | Extra install |
|---|---|---|
| `generic_ssh` | any SSH-CLI device netmiko reaches (device_type + commands) | `otitbup[ssh]` |
| `generic_sftp` | files/directories/globs off any Linux device | `otitbup[sftp]` |
| `generic_http` | config exports from web-managed devices (Basic/Digest auth) | — |
| `generic_file` | watch-folder ingest of anything exported by hand | — |
| `generic_opcua` / `generic_enip` / `generic_dnp3` | protocol-level identity + fingerprint | see above |

## Web UI

`otitbup serve` — read-only by design (the YAML config stays the source
of truth): dashboard with summary tiles, per-zone grouping and live
filtering; a **health** page (coverage, staleness, consecutive failures);
**config search** across the latest backup of every device; a **drift**
page (golden-config baselines); per-device pages with artifact lists,
history, diffs, run status, annotations, policy findings, rehearsal log,
baseline drift and effective retention; per-commit and **any-two-commit
compare** diff views; raw artifact viewing; activity feed; a **policy**
page; a retention page; the driver catalog; and an **audit log**
(admin-only). Machine-readable endpoints: `/metrics` (Prometheus),
`/api/status` · `/api/devices` · `/api/policy` · `/api/device/<name>`
(JSON), and `/healthz` (liveness, no auth). Optional HTTP Basic auth
(single user or **multiple users with roles**) and TLS.

## Operations, compliance & recovery

- **Health & metrics** — persisted run results (SQLite) distinguish
  "unchanged" from "unreachable"; `otitbup status`, the Health page,
  Prometheus `/metrics`, and a staleness alert ("no successful backup in
  N days") make silent failure impossible.
- **Verification** — `otitbup verify` re-hashes stored artifacts against
  the manifest recorded at capture time and checks blob integrity.
- **Change management** — `otitbup annotate` links a change to a work
  order (git notes); maintenance mode classifies changes as expected vs.
  **unexpected** (the unauthorized-change signal); compliance reports
  (on demand or scheduled) cover coverage, changes, unannotated changes,
  policy findings and rehearsal status.
- **Config policy checks** — built-in and custom rules flag insecure
  configuration (telnet, SNMP public, weak passwords) across vendors.
- **Recovery** — guided restore bundles for PLCs/RTUs; **automated
  restore for network gear** (`otitbup net-restore`, dry run by default,
  pre-change capture + post-change verify); per-site **DR runbooks**
  (`otitbup dr-plan`); and restore-**rehearsal** tracking surfaced in the
  UI and reports. Optional HTTP Basic auth
(`otitbup passwd`) and TLS (`otitbup certgen` for a self-signed pair, or
any PEM cert/key) — see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## OT safety

- Drivers are read-only by contract; restore is a guided export
  (`otitbup restore` produces hash-verified artifacts + a RESTORE.md
  checklist), never a device write.
- Per-zone maintenance windows and connection caps keep backup traffic
  from disturbing control traffic.
- Discovery (`otitbup discover`) is opt-in, sequential TCP-connect only,
  and produces a YAML proposal for human review — it never edits the
  inventory.

## Tests

```bash
pytest
```
