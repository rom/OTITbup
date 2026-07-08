# OTITbup — FAQ

## General

### What is OTITbup?
An on-prem tool that backs up configurations, settings, logic and code
from OT equipment (PLCs, RTUs, HMIs/SCADA, gateways) and IT network gear
into a versioned git repository, then helps you detect change, prove
coverage, stay compliant, and recover.

### Is it safe to run against live OT equipment?
That is the central design constraint. Every collector is **read-only by
contract** — a driver's job is to *read* configuration, never write. The
one write path, automated restore, exists only for network gear, defaults
to a dry run, and refuses PLC/RTU drivers. Polling respects **maintenance
windows** and **per-zone concurrency limits** so backup traffic can't
disturb control traffic, and network discovery is opt-in, sequential, and
TCP-connect only.

### Will it write to or change my PLC?
No. PLC/RTU restore is *guided*: `otitbup restore` exports the exact
versioned artifacts plus a checklist, and a human performs the write with
vendor tools. Only network devices can be restored automatically, and only
when you explicitly pass `--apply`.

### What does it cost / what are the dependencies?
The core needs only Python 3.11+ and PyYAML. Everything else is an optional
extra installed only when a driver needs it (`netmiko`, `paramiko`,
`python-snap7`, `pycomm3`, `asyncua`, `pyads`, `cryptography`). Syslog,
SNMP traps, DNP3, Modbus, FINS, MC protocol, SNMP and the web UI are all
stdlib — no extra dependency.

## Devices & drivers

### What equipment is supported?
40+ driver types: Siemens/Rockwell/Schneider/Mitsubishi/Omron/Beckhoff/
WAGO/Phoenix/GE-Emerson PLCs; DNP3, SEL, SICAM, ABB RTU500 and Netcontrol
RTUs; Ignition/WinCC/FactoryTalk HMIs; Cisco/SCALANCE/RUGGEDCOM/Hirschmann/
Belden/Moxa/Westermo/Advantech/Netgear/Omron switches, routers and
firewalls; plus generic OPC UA, EtherNet/IP, DNP3, SNMP, SSH, SFTP and
HTTP drivers. Run `otitbup drivers` for the live list.

### My device isn't listed. Can I still back it up?
Almost certainly. Try, in order: a generic protocol driver
(`generic_snmp`… actually `snmp_fingerprint`, `generic_opcua`,
`generic_enip`, `generic_dnp3`), `generic_ssh` (any CLI device via a
netmiko device type), `generic_sftp` (any Linux device — fetch config
files), `generic_http` (web-managed devices), or `generic_file` (drop
engineering-tool exports into a watch folder).

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
Ignition has a clean HTTP gateway-backup endpoint — use
`ignition_gateway`. WinCC/PCS7 and FactoryTalk View projects are file
trees: fetch them with `wincc` / `factorytalk_view` (SFTP) or drop exports
into a `generic_file` watch folder.

## Storage & retention

### Where are backups stored?
In a local git repository under `data_dir` on the appliance — one commit
per device per change. Optionally mirrored to a remote git server
(`git.push`). Large artifacts are offloaded to a deduplicated blob store.

### Won't the git repo grow forever with big PLC files?
Text configs stay in git (cheap and worth keeping). Artifacts at or above
`large_file_threshold` go to a content-addressed **blob store**, and
`otitbup retention` prunes expired blobs by `keep_versions` / `keep_days`
— set globally and overridable per site/zone/device. Git history is never
rewritten.

### Can I keep different history for different devices?
Yes — retention resolves per field with device > zone > site > global >
defaults. A critical firewall can keep 100 versions while a noisy HMI
keeps 10.

## Change detection & compliance

### How do I know if someone changed a device without authorization?
Every detected change outside a **maintenance window** is classified
*unexpected* and alerts separately (and can open a ticket / fire an SNMP
trap). Set maintenance mode during planned work so authorized changes are
tagged *expected*.

### What is baseline / golden-config drift?
`otitbup baseline set <device>` marks an approved config. Drift is the diff
between the latest backup and that baseline — shown per device and on the
`/drift` page. Change detection tells you it differs from *last time*;
drift tells you it differs from *approved*.

### Can I record why a change happened?
Yes — `otitbup annotate <device> "MOC-1234: reason"` attaches a note (git
note) to a commit, or use the note form on the device page. Unannotated
changes are counted in the compliance report.

### What do the config policy checks do?
They lint captured configs against rules (telnet enabled, SNMP `public`,
weak passwords, …) and report findings per device — turning the backup
archive into a compliance scan. Built-in rules are vendor-neutral; add your
own `match`/`absent` regex rules.

## Operations

### How do I know a backup silently stopped working?
Run *attempts* are persisted, so the tool distinguishes "unchanged" from
"unreachable". `alerts.stale_days` alerts when a device has no successful
backup in N days, the Health page and `otitbup status` show it, and
`/metrics` exposes it to Prometheus.

### How do I run it as a service?
Use the systemd units or Docker files in `packaging/`. The web service
answers `GET /healthz` (no auth) for liveness, and the daemon reloads its
config on `SIGHUP` (`systemctl reload otitbup-daemon`).

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
reloads) is recorded and visible at `/audit` (admin only).

## Secrets

### Where do device credentials live?
In a separate secrets file (plaintext protected by OS permissions, or
Fernet-encrypted), or fetched from **HashiCorp Vault** or **CyberArk CCP**.
Credentials are never written into the git backup.

### Can I rotate the encryption key or move to Vault later?
Yes — the secrets backend is pluggable. Start with an encrypted file and
switch `secrets.backend` to `vault`/`cyberark` without touching drivers.

## Troubleshooting

### A backup fails with "credentials are required".
The device references a `credentials:` name that isn't in your secrets
file, or no secrets backend is configured. Check `secrets.path` and that
the key exists.

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

### What's the difference between 3-2-1 and 3-2-1-1-0?
3-2-1-1-0 adds one **offline/air-gapped** copy (ransomware can't reach it)
and requires **0 errors** — every backup verifies and no device is
failing. otitbup checks both extra conditions on the Strategy page.

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
Cisco (IOS/NX-OS/SG/ASA), Juniper (Junos/SRX), Arista, HPE/Aruba, Huawei,
MikroTik, Extreme, Dell, Zyxel, Fortinet, Palo Alto, Check Point, Sophos,
VyOS, plus the industrial lines (SCALANCE, RUGGEDCOM, Hirschmann/Belden,
Moxa, Westermo, Advantech, Phoenix Contact, Red Lion, Korenix, Antaira,
Planet, Netgear, Teltonika). Run `otitbup drivers`.

### Which serial-to-ethernet / protocol gateways are supported?
Moxa NPort and MGate, Lantronix, Digi, Perle, Sena, Advantech EKI, HMS
Anybus, ProSoft, Red Lion, and HMS eWON — via CLI or web export depending
on the model. Use `generic_gateway` for anything else web-managed.

### Can I add my own vendor without writing code?
Often yes: `generic_ssh` (set `device_type` + `commands`), `generic_http`
(set `urls`), `generic_sftp` (set `paths`), `snmp_fingerprint`,
`generic_opcua`/`generic_enip`/`generic_dnp3`, or `generic_file` for
engineering-tool exports.

## Web UI (more)

### Is there in-app help?
Yes — a **Help** page in the nav, and small **?** icons throughout that
show a popover explaining the feature on hover or keyboard focus.

### Can I trigger a backup or generate a report from the browser?
Yes, when signed in as operator or admin: a device page has "Back up now",
"Verify" and "Set baseline" buttons and a note form; the Health page has a
report generator (format + sign) and (admin) a config-reload button.
