# OTITbup

OT and IT backup and restore tool that extracts and versions
configurations, settings, logic and code from OT equipment (PLCs, RTUs,
gateways, comms equipment) and IT infrastructure (network switches,
routers, firewalls) into a **versioned git backup** on an on-prem
appliance.

It is built for OT reality: every collector is **read-only by contract**,
polling respects **maintenance windows** and **per-zone rate limits**, and
it degrades gracefully — capturing full configuration and program files
where a protocol allows, and identity + a change fingerprint where it does
not.

## What it does

- **Backs up 40+ device types** across PLCs, RTUs and network gear — from
  Siemens/Rockwell/Schneider/Mitsubishi/Omron/Beckhoff PLCs and DNP3/SEL
  RTUs to Cisco/Hirschmann/Moxa/Westermo switches — via native protocols,
  SSH, SFTP, HTTP, OPC UA, EtherNet/IP and more.
- **Versions everything in git**, one commit per device per change, with
  sha256 manifests, hierarchical **retention** and a deduplicated blob
  store for large project files.
- **Detects and classifies change**: alerts on *unexpected* changes (the
  unauthorized-change signal), tracks **golden-config drift** against an
  approved baseline, and lets you **annotate** changes with a work order.
- **Proves coverage and health**: persisted run history, staleness
  alerts, a Prometheus `/metrics` endpoint, config **policy/compliance**
  checks, scheduled compliance **reports**, and inventory **reconciliation**.
- **Recovers**: hash-verified restore bundles and per-site DR runbooks for
  PLCs/RTUs, **automated restore** for network gear (dry-run first), and
  restore-rehearsal tracking.
- **Read/write web UI** with roles, per-site **scopes**, optional SSO
  (trusted-header/LDAP), a live activity stream, and a scoped write API;
  syslog and **SNMP trap** event fan-out; and a JSON API.
- **Hardens the archive**: a tamper-evident, hash-chained **audit log**
  (`otitbup verify-audit`), optional **SSH-signed commits**, optional
  **at-rest encryption** of the large-artifact blob store (Fernet), and an
  **encrypted offsite/cloud copy** of the whole backup (`otitbup offsite`).
- **Scales across sites**: federated **site collectors** (Purdue model) —
  git for the backup bytes, a scoped health API for the roll-up — plus
  statistical **anomaly detection** and config-as-code **desired state**.

## Documentation

- [docs/USAGE.md](docs/USAGE.md) — task-oriented guide to using the tool
- [docs/FAQ.md](docs/FAQ.md) — frequently asked questions
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — full `otitbup.yml`
  reference (inventory, secrets, retention, policy, reports, events,
  tickets, web UI auth/TLS/roles, alerts)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module layout and design
- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — scope and decisions from
  the requirements interview
- [packaging/](packaging/) — systemd units, Dockerfile, and the SNMP MIB

## Quick start

```bash
pip install -e .[dev]          # core + tests
pip install -e .[ssh]          # + netmiko for network equipment

cp examples/otitbup.yml examples/secrets.yml .
$EDITOR otitbup.yml secrets.yml

otitbup help                   # grouped list of every command
otitbup explain backup         # a full description of one command
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
otitbup report --format pdf --sign      # signed compliance report (html/csv/pdf/docx)
otitbup report-verify report.pdf        # verify a signed report
otitbup export --out backup.tar.gz      # portable offsite/offline archive (3-2-1)
otitbup strategy                        # evaluate 3-2-1 / 3-2-1-1-0 posture
otitbup offsite push                    # encrypted snapshot to an external server / cloud (3-2-1 offsite)
otitbup offsite restore plc-01 --out ./bundle   # restore a device from the offsite/cloud copy
otitbup netbox reconcile                # inventory vs. NetBox (or: netbox import)
otitbup discover 10.20.0.0/24 --enrich  # scan + probe device identity
otitbup test plc-01                     # dry-run: test creds/reachability, no commit
otitbup anomalies                       # slow-backup / change-storm outliers
otitbup desired --diff                  # drift vs. declared config-as-code
otitbup gc                              # repack/prune the backup git repo
otitbup verify-audit                    # verify the tamper-evident audit chain
otitbup token create ci --role operator --scopes "plant-a/*"   # scoped API token
otitbup federation                      # roll up health from site collectors
```

See [docs/USAGE.md](docs/USAGE.md) for a full, task-oriented walkthrough
of every command and the web UI.

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
| Yokogawa | FA-M3 (e-RT3), STARDOM FCN/FCJ | `yokogawa_web` | web-server status/parameter/config pages (HTTP) + fingerprint | — (stdlib) |
| Honeywell | ControlEdge PLC / RTU | `honeywell_web` | web-server diagnostics/parameter/config pages (HTTP) + fingerprint | — (stdlib) |
| Fanuc | CNC (0i/30i/31i/32i) | `fanuc_cnc` | embedded web-server pages (HTTP); FOCAS is proprietary, **not** implemented | — (stdlib) |
| Bachmann | M1 (MX/CX/MC) | `bachmann_m1` | deployed project/config from the CFC card via SFTP | `otitbup[sftp]` |
| B&R | X20 / X90 / Automation PC | `br_automation` | deployed project/config files via SFTP (paths per target) | `otitbup[sftp]` |
| Netcontrol | Netcon 100/500/3000 RTUs & gateways | `netcontrol_rtu` | SSH CLI config; also DNP3/IEC-104/IEC-61850 | `otitbup[ssh]` |
| Any vendor | OPC UA server exposed | `generic_opcua` | build info, namespaces, optional nodes + fingerprint | `otitbup[opcua]` |
| Any vendor | EtherNet/IP device | `generic_enip` | CIP identity + fingerprint | — (stdlib) |
| Any vendor | SNMP agent (switch, UPS, gateway, RTU, ...) | `snmp_fingerprint` | system group (descr/name/location) + fingerprint | — (stdlib) |
| Any vendor | engineering-tool project exports (TIA Portal, Studio 5000, EcoStruxure, GX Works, Sysmac, PME, ...) | `generic_file` | **full project files** from a watch folder | — |

### HMI / SCADA & substation IEDs

| Vendor | Equipment | Driver | Captures | Extra install |
|---|---|---|---|---|
| Inductive Automation | Ignition gateway | `ignition_gateway` | **full `.gwbk` gateway backup** over HTTP | — (stdlib) |
| Siemens | WinCC / PCS7 project | `wincc` | **project tree** via SFTP | `otitbup[sftp]` |
| Siemens | WinCC OA (PVSS) project | `wincc_oa` | **project tree** via SFTP | `otitbup[sftp]` |
| Rockwell | FactoryTalk View project | `factorytalk_view` | **project tree** via SFTP | `otitbup[sftp]` |
| AVEVA / Wonderware | InTouch / System Platform | `wonderware` | **project tree** via SFTP | `otitbup[sftp]` |
| AVEVA | Citect / Plant SCADA | `citect` | **project tree** via SFTP | `otitbup[sftp]` |
| GE / Emerson | iFIX | `ifix` | **project tree** via SFTP | `otitbup[sftp]` |
| PTC / Kepware | KEPServerEX | `kepware` | **project file** via SFTP | `otitbup[sftp]` |
| Rockwell | FactoryTalk View SE | `factorytalk_se` | **project tree** via SFTP | `otitbup[sftp]` |
| Siemens | WinCC Unified | `siemens_wincc_unified` | **project tree** via SFTP | `otitbup[sftp]` |
| AVEVA / Schneider | ClearSCADA / Geo SCADA | `clearscada` / `geo_scada` | **project tree** via SFTP | `otitbup[sftp]` |
| COPA-DATA / AVEVA / Progea / Trihedral / Reliance | zenon, AVEVA Edge, Movicon, VTScada, Reliance | `zenon` / `aveva_edge` / `movicon` / `vtscada` / `reliance_scada` | **project tree** via SFTP | `otitbup[sftp]` |
| Any SCADA | project directory | `generic_scada` | **project tree** via SFTP | `otitbup[sftp]` |
| Substation IEDs | SIPROTEC, ABB Relion, GE Multilin, ... | `iec61850_mms` | MMS Identify (vendor/model/rev) + fingerprint — *experimental* | — (stdlib) |
| Protection relays | Siemens SIPROTEC, ABB Relion, Schneider MiCOM, NR Electric, NARI | `siemens_siprotec` / `abb_relion` / `schneider_micom` / `nr_electric` / `nari_relay` | IEC 61850 MMS identity + fingerprint | — (stdlib) |
| SEL / GE | SEL relays, GE Multilin | `sel_relay` / `ge_multilin` | web-server config export | — (stdlib) |

### RTUs

| Vendor | Equipment | Driver | Captures | Extra install |
|---|---|---|---|---|
| Any DNP3 vendor (Schneider SCADAPack, GE, Kingfisher/Semaphore, ...) | DNP3 outstations | `generic_dnp3` | device attributes (vendor, model, serial, versions) + fingerprint | — (stdlib) |
| SEL | RTAC 3530/3505, protection relays | `sel_terminal` | ID / STA / SHO terminal output (settings) | `otitbup[ssh]` |
| Siemens | SICAM A8000 (CP-8000/8021/8022/8050) | `siemens_sicam` | web-server endpoints (diagnostics/parameters) | — (stdlib) |
| ABB | RTU520, RTU540, RTU560 (RTU500 series) | `abb_rtu520` / `abb_rtu560` | web-server endpoints (status/config downloads) | — (stdlib) |
| GE / Emerson | D20 / D25 | `ge_d20` | CLI config; also DNP3/IEC-104 | `otitbup[ssh]` |
| NovaTech | Orion / OrionLX | `novatech_orion` | Linux CLI; also DNP3/IEC-61850 | `otitbup[ssh]` |
| Eaton / Cooper | SMP gateway | `smp_gateway` | CLI; also DNP3/IEC-61850 | `otitbup[ssh]` |
| Survalent | SmartVU / RTU | `survalent_rtu` | CLI; also DNP3 | `otitbup[ssh]` |
| Netcontrol | Netcon RTUs | `netcontrol_rtu` | SSH CLI; also DNP3/IEC-104/61850 | `otitbup[ssh]` |
| Emerson | ROC800 / FloBoss (100/107) | `emerson_roc` | DNP3 device attributes (vendor/product/serial/versions) + fingerprint | — (stdlib) |
| Schneider / Servelec | Kingfisher RTU | `kingfisher_rtu` | DNP3 device attributes + fingerprint | — (stdlib) |
| Motorola | ACE3600 | `motorola_ace` | DNP3 device attributes + fingerprint | — (stdlib) |
| SATEC | RTUs / meters | `satec_rtu` | web-server config export | — (stdlib) |

### Network equipment

| Vendor | Switches | Routers | Firewalls |
|---|---|---|---|
| Cisco | `cisco_ios`, `cisco_nxos` (Nexus), `cisco_sg` (SB) | `cisco_ios` | `cisco_asa` |
| Juniper | `juniper_junos` | `juniper_junos` | `juniper_srx` |
| Arista | `arista_eos` | — | — |
| HPE / Aruba | `hpe_comware`, `hpe_procurve`, `aruba_cx`, `aruba_osswitch` | — | — |
| Huawei | `huawei_vrp` | `huawei_vrp` | — |
| MikroTik | `mikrotik_routeros` | `mikrotik_routeros` | — |
| Nokia / Alcatel-Lucent | — | `nokia_sros` (7705 SAR, 7750 SR) | — |
| Extreme / Dell / Zyxel | `extreme_exos`, `dell_os10`, `dell_powerconnect`, `zyxel` | — | — |
| Fortinet / Palo Alto / Check Point / Stormshield / Sophos | — | — | `fortinet_fortigate`, `paloalto_panos`, `checkpoint_gaia`, `stormshield`, `sophos_xg` |
| VyOS | — | `vyos` | `vyos` |
| Siemens SCALANCE | `siemens_scalance` | `siemens_scalance` (M-series 4G/5G) | `siemens_scalance` (S/SC) |
| Siemens RUGGEDCOM | `ruggedcom_ros` | `ruggedcom_rox` | `ruggedcom_rox` |
| Hirschmann / Belden | `hirschmann_hios`, `hirschmann_classic`, `belden_switch` | — | `hirschmann_eagle` |
| Moxa | `moxa_switch` (EDS) | `moxa_edr` | `moxa_edr` |
| Westermo | `westermo_weos` | `westermo_weos` (RedFox), `westermo_merlin` (4G/5G) | `westermo_weos` |
| Advantech | `advantech_switch` (EKI) | `advantech_router` (ICR) | — |
| Phoenix / Red Lion / Korenix / Antaira / Planet | `phoenix_fl_switch`, `redlion_nt`, `korenix`, `antaira`, `planet_switch` | — | `phoenix_mguard` (mGuard) |
| Netgear | `netgear_switch` (M4300/M4250/ProSAFE) | web-managed → `generic_http` | web-managed → `generic_http` |
| Teltonika | — | `teltonika` (cellular) | — |
| Omron | `omron_switch` | — | — |

### Serial-to-ethernet & protocol gateways

| Vendor | Driver | How |
|---|---|---|
| Moxa NPort (serial-to-eth) | `moxa_nport` | HTTP config export |
| Lantronix / Digi / Perle / Sena (serial servers) | `lantronix`, `digi_connect`, `perle_iolan`, `sena_serial` (CLI); `lantronix_web`, `digi_web` (web) | SSH CLI or HTTP export |
| Advantech EKI serial | `advantech_eki_serial` | HTTP export |
| Moxa MGate / HMS Anybus / ProSoft / Red Lion (protocol gateways) | `moxa_mgate`, `hms_anybus`, `prosoft_gateway`, `redlion_gateway` | HTTP export |
| HMS eWON Flexy/Cosy (remote-access gateway) | `ewon_flexy` | HTTP export |
| Any web-managed gateway | `generic_gateway` | HTTP export |

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

`otitbup serve` — a **Dashboard** page with inline-SVG graphs (coverage
donut, device-status bar, backups/day and changes/day, policy findings by
severity, storage) and per-device history charts; the device list with
per-zone grouping and live filtering; a **health** page (coverage,
staleness, consecutive failures);
**config search** across the latest backup of every device; a **drift**
page (golden-config baselines); per-device pages with artifact lists,
history, diffs, run status, annotations, policy findings, rehearsal log,
baseline drift and effective retention; per-commit and **any-two-commit
compare** diff views; raw artifact viewing; activity feed; a **policy**
page; a retention page; the driver catalog; and an **audit log**
(admin-only). Machine-readable endpoints: `/metrics` (Prometheus),
`/api/status` · `/api/devices` · `/api/policy` · `/api/device/<name>`
(JSON), and `/healthz` (liveness, no auth). Optional HTTP Basic auth
(single user or **multiple users with roles**) and TLS. Users, sessions and
API tokens carry **scopes** (globs over `site/zone/name`) that gate write
actions per part of the estate; login can also be delegated to an upstream
OIDC/SAML proxy (trusted header) or LDAP/AD. A scoped **write API**
(`POST /api/device/<name>/backup|verify`, bearer token or session) lets CI
and other systems trigger backups, and the **Activity** page streams live
events over SSE. The **Help** page links to the full manuals — Usage,
Configuration, FAQ and Release notes — rendered in-app from the shipped
Markdown (`/help/usage`, `/help/configuration`, `/help/faq`,
`/help/releasenotes`; docs directory found relative to the package or via
the `OTITBUP_DOCS_DIR` override). The **Config** page (admin-only) edits the
whole configuration from the browser — global settings (offsite, SSO/LDAP,
events, logging, integrations, encryption/signing/TLS keys & certs) and the
inventory per **site / zone / device** (name, driver, schedule, credentials,
maintenance window, timezone, concurrency, retention; add/delete) — with
every save validated before write, a `.bak` kept, and an instant reload.

## Operations, compliance & recovery

- **Health & metrics** — persisted run results (SQLite) distinguish
  "unchanged" from "unreachable"; `otitbup status`, the Health page,
  Prometheus `/metrics`, and a staleness alert ("no successful backup in
  N days") make silent failure impossible. **Anomaly detection**
  (`otitbup anomalies`, and automatically after each backup) flags
  slow-backup outliers and change storms.
- **Reliability** — per-device **retries** with backoff for transient
  failures, pre/post **shell hooks** around each backup, and `otitbup test`
  / `backup --dry-run` for a commit-free connectivity and credential check.
- **Verification** — `otitbup verify` re-hashes stored artifacts against
  the manifest recorded at capture time and checks blob integrity.
- **Change management** — `otitbup annotate` links a change to a work
  order (git notes); maintenance mode classifies changes as expected vs.
  **unexpected** (the unauthorized-change signal); compliance reports
  (on demand or scheduled) cover coverage, changes, unannotated changes,
  policy findings and rehearsal status.
- **Config policy checks** — built-in and custom rules flag insecure
  configuration (telnet, SNMP public, weak passwords) across vendors.
- **Config-as-code** — declare intended device configs in a directory and
  measure live drift against them (`otitbup desired [--diff]`), distinct
  from a golden baseline (an approved *past* backup).
- **Integrity & at-rest** — a hash-chained, tamper-evident **audit log**
  (`otitbup verify-audit`); optional **SSH-signed** backup commits
  (`git.sign`); and optional **Fernet encryption** of the blob store
  (`encryption.blob_key`) — content-addressing by plaintext hash is
  preserved, so dedup is unaffected (LUKS still recommended for the rest).
- **Federation / site collectors** — a central appliance rolls up health
  from per-site collectors over a scoped read-only API (`otitbup
  federation`), while backup bytes federate over plain git to a shared
  remote — the Purdue-model split of control plane from data plane.
- **Recovery** — guided restore bundles for PLCs/RTUs; **automated
  restore for network gear** (`otitbup net-restore`, dry run by default,
  pre-change capture + post-change verify); per-site **DR runbooks**
  (`otitbup dr-plan`); and restore-**rehearsal** tracking surfaced in the
  UI and reports.
- **Events → syslog & SNMP traps** — every process start/stop, web UI
  start/stop, backup start/stop/error, config read/reload, login/logout,
  and user create/delete/password-change is emitted as a structured event
  to the audit log and, when configured, to **syslog** and **SNMPv2c
  traps** (stdlib, no extra dependencies).
- **Read/write web UI** — signed-in operators can start a backup, verify
  it, add notes, set baselines and generate reports from the browser;
  admins manage users and re-read the config. Cookie sessions with CSRF,
  role-based access (viewer/operator/admin), a full audit log, an online
  **Help** page and hover **popover help**.
- **Signed multi-format reports** — compliance reports in HTML, **CSV,
  PDF and DOCX** (all stdlib), optionally **Ed25519-signed** for auditors
  (`otitbup report --format pdf --sign`, verify with `report-verify`). The
  PDF is richly formatted with a title banner, KPI tiles and vector bar
  charts (hand-drawn — no PDF library).
- **Graphs** — the web Dashboard and per-device pages render inline-SVG
  charts (donut, bar, stacked, timeline), server-side and CSP-safe.
- **3-2-1 / 3-2-1-1-0 strategy** — the Strategy page (and `otitbup
  strategy`) evaluates your backup posture: 3 copies, 2 media, 1 offsite,
  +1 offline, 0 errors — and tells you exactly what's missing. `otitbup
  export` produces the portable offsite/offline archive.
- **Encrypted offsite / cloud copy** — `otitbup offsite` ships an
  **encrypted** snapshot of the whole backup (git repo + blob store + run
  store) to an external server or cloud object store (`file` / `sftp` /
  `s3`-compatible: MinIO, Backblaze B2, Wasabi, Ceph RGW). The snapshot is
  Fernet-encrypted on the appliance before upload — the remote holds
  ciphertext only and the key never leaves the appliance — and `offsite
  restore` produces a hash-verified bundle for one device straight from the
  remote, with no device writes.
- **CMDB / NetBox & ticketing** — `otitbup netbox` reconciles the
  inventory against NetBox (or imports from it); events open tickets in
  ServiceNow, Jira, **Request Tracker (RT)** or a generic CMDB webhook. Optional HTTP Basic auth
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
