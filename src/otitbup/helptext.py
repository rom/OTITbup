"""Human-facing command help for `otitbup help` and `otitbup explain`.

One registry, two views: `help` prints the grouped one-line summaries;
`explain <command>` prints the long-form description. Keeping both here
means the summaries and the deep help never drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandHelp:
    name: str
    group: str
    summary: str
    detail: str


# Display order of groups.
GROUPS = [
    "Setup", "Backup & schedule", "Inspect", "Change management",
    "Health & compliance", "Retention & strategy", "Recovery",
    "Discovery & inventory", "Reporting", "Web UI & secrets", "Help",
]


def _c(name, group, summary, detail):
    return CommandHelp(name, group, summary, detail.strip("\n"))


COMMANDS: dict[str, CommandHelp] = {c.name: c for c in [
    # ------------------------------------------------------------- Setup
    _c("init", "Setup", "scaffold a starter otitbup.yml + secrets.yml", """
`otitbup init [--dir DIR]` writes a minimal, commented `otitbup.yml` and a
matching `secrets.yml` (mode 0600) into DIR (default: the current
directory), then tells you to edit them and run `validate`. It refuses to
overwrite existing files, so it is safe to run in a populated directory.

This is the fastest way to a first backup: `init`, fill in one device and
its credentials, then `otitbup backup --force`.

See also: validate, drivers, backup.
"""),
    _c("validate", "Setup", "check the config file and exit", """
`otitbup validate` loads the config and reports the number of sites and
devices, or exits non-zero with the first error it finds (bad schedule,
duplicate device, unknown retention key, malformed maintenance window,
etc.). It touches no devices and writes nothing — run it after every edit
to `otitbup.yml`, and in CI.

See also: init, list.
"""),
    _c("list", "Setup", "list configured devices", """
`otitbup list` prints every device in the inventory with its driver and
schedule, resolved from `otitbup.yml`. Use it to confirm the inventory
parses the way you expect before running a backup.

See also: drivers, validate, status.
"""),
    _c("drivers", "Setup", "list available drivers with descriptions", """
`otitbup drivers` lists every registered driver (95+) with a one-line
description — PLC, RTU, HMI/SCADA, network switch/router/firewall,
serial/protocol gateway, and the generic transports. Use it to find the
right `driver:` value for a device, then `explain` nothing further is
needed — driver options live in each driver's docstring and in
docs/CONFIGURATION.md.

See also: discover, list.
"""),

    # -------------------------------------------------- Backup & schedule
    _c("backup", "Backup & schedule", "run a backup now", """
`otitbup backup [DEVICE ...] [--site S] [--zone Z] [--force]` collects a
backup immediately. With no arguments it backs up every device; name
devices, or filter with `--site`/`--zone` (repeatable, and combinable).
`--force` ignores maintenance windows for a supervised run.

Each device is committed to git individually; a device whose config hasn't
changed produces no commit. Backups are single-writer across processes (a
file lock), so a manual run and the daemon can't collide. Changes detected
outside a maintenance window are flagged UNEXPECTED and alert accordingly.

Examples:
  otitbup backup
  otitbup backup core-sw-01 plc-01
  otitbup backup --site plant-a --zone cell-1 --force

See also: daemon, diff, status, maintenance.
"""),
    _c("test", "Backup & schedule",
       "test reachability/credentials without committing", """
`otitbup test [DEVICE ...] [--site S] [--zone Z]` connects to devices and
collects a backup but commits nothing — a dry run to confirm a new device's
address, credentials and driver options work before adding it to the
schedule. Same selection flags as `backup`. (Equivalent to
`backup --dry-run`.)

See also: backup, list.
"""),
    _c("gc", "Backup & schedule", "repack/prune the backup git repo", """
`otitbup gc [--aggressive]` runs `git gc --prune=now` on the backup
repository to repack loose objects and reclaim space — housekeeping for a
long-lived repo. `--aggressive` repacks harder (slower). Safe to run from
cron while the repo is idle.

See also: retention, export.
"""),
    _c("daemon", "Backup & schedule", "run the scheduler", """
`otitbup daemon [--once]` runs the scheduler, backing up each device when
its `schedule` comes due and its zone's `maintenance_window` is open.
`--once` performs a single tick and exits (handy from cron). The daemon
reloads the config automatically when the file changes (or on SIGHUP —
`systemctl reload otitbup-daemon`); a broken edit is rejected and the
previous config kept. It also emits scheduled compliance reports when
`reports.interval` is set.

See also: backup, serve, strategy.
"""),
    _c("serve", "Web UI & secrets", "run the web UI", """
`otitbup serve [--host H] [--port P]` starts the web UI (defaults from
`webui` config, else 127.0.0.1:8080). It offers a Dashboard with graphs,
per-device pages (history, diffs, timeline, notes, drift), Health, Search,
Policy, Retention, Strategy, and — for admins — Users and Audit. It is
read/write with roles: operators can back up/verify/annotate/set baselines
and generate reports; admins manage users and reload the config. Optional
HTTP Basic/cookie auth and TLS. `/healthz` is an unauthenticated liveness
probe; `/metrics` (Prometheus) and `/api/*` (JSON) are available.

See also: passwd, certgen, daemon.
"""),

    # ------------------------------------------------------------ Inspect
    _c("diff", "Inspect", "show a device's change (or a range)", """
`otitbup diff DEVICE [--from A] [--to B]` shows the diff of a device's most
recent backup, or — with `--from`/`--to` — between any two commits
(`--to` defaults to HEAD). Useful for reviewing exactly what changed on a
switch or PLC between two points in time.

See also: log, search, annotate.
"""),
    _c("log", "Inspect", "show backup history", """
`otitbup log [DEVICE] [-n N]` prints the git commit history — for one
device, or across all devices if none is named. Each line is a backup:
hash, date, and subject.

See also: diff, status.
"""),
    _c("search", "Inspect", "search across the latest backup of every device", """
`otitbup search PATTERN [--case-sensitive]` greps the latest backup of
every device for PATTERN (a string or regex) and prints device:artifact:
line matches. This is the fast way to answer "which devices reference this
VLAN / IP / tag / username?" across the whole estate. The web UI Search
page adds site/zone filters.

Examples:
  otitbup search "vlan 30"
  otitbup search "10\\.0\\.0\\.1"

See also: diff, policy.
"""),

    # -------------------------------------------------- Change management
    _c("annotate", "Change management", "attach a change note to a commit", """
`otitbup annotate DEVICE TEXT [--commit HASH]` attaches a note (stored as a
git note) to a backup commit — typically a work-order or MOC number
explaining why a config changed. Without `--commit` it annotates the
latest backup. Annotations show up in the device history and the web UI,
and unannotated changes are counted in the compliance report.

See also: maintenance, diff, report.
"""),
    _c("maintenance", "Change management",
       "mark a device/zone/site in maintenance", """
`otitbup maintenance SCOPE [--hours N] [--reason R] [--off]` puts a device
(`site/zone/name`), a whole zone (`site/zone/*`) or a whole site
(`site/*`) into maintenance mode. Changes detected while in maintenance
are classified *expected*; changes outside it are *unexpected* and alert
harder (the unauthorized-change signal, which can also open a ticket or
fire an SNMP trap). `--hours` auto-expires the window; `--off` clears it.

Examples:
  otitbup maintenance plant-a/cell-1/plc-01 --hours 8 --reason upgrade
  otitbup maintenance "plant-a/*"
  otitbup maintenance plant-a/cell-1/plc-01 --off

See also: annotate, backup.
"""),
    _c("baseline", "Change management",
       "manage golden-config baselines and drift", """
`otitbup baseline set DEVICE [--commit H] [--note ...]` approves a config
as the golden baseline. `otitbup baseline drift` lists devices whose latest
backup differs from their baseline. `otitbup baseline clear DEVICE` removes
it. Change detection tells you a config differs from *last time*; drift
tells you it differs from *approved* — the distinction auditors care about.

See also: diff, strategy, report.
"""),

    # -------------------------------------------------- Health & compliance
    _c("status", "Health & compliance",
       "per-device backup health from the run store", """
`otitbup status` prints, per device, its health (ok / STALE / FAILING /
NEVER), how long since the last successful backup, and the consecutive-
failure count. Because run *attempts* are persisted, this distinguishes
"unchanged" from "unreachable" — the thing plain git history can't show.

See also: verify, report, strategy.
"""),
    _c("anomalies", "Health & compliance",
       "report statistical anomalies in backup history", """
`otitbup anomalies` scans each device's run history for signals that it is
misbehaving *while still succeeding* — the cases plain failure/staleness
alerts miss. It flags **slow** backups (a run whose duration is a
statistical outlier versus that device's own baseline) and **change
storms** (a normally-stable device that has changed on most of its recent
runs — config flapping, a stuck auto-save, or tampering).

The same checks run automatically after each backup and emit an
`anomaly.detected` event (alerts + syslog + SNMP + tickets). Tune under the
`anomaly:` config section (sigma, duration_floor, change_window, …); set
`anomaly.enabled: false` to disable. Exits non-zero if anomalies are found.

See also: status, activity, verify.
"""),
    _c("desired", "Health & compliance",
       "compare live backups against config-as-code", """
`otitbup desired [--diff]` compares each device's latest backup against a
*declared* intended configuration (config-as-code / GitOps), reporting
where the live device has drifted from intent. This is the inverse of
`baseline`: baseline approves whatever was captured; `desired` asserts what
*should* be there.

Declare intended configs as files under `desired.dir`, mirroring the repo
layout: `<desired.dir>/<site>/<zone>/<device>/<artifact-name>`. Only
devices with a desired file are checked. `--diff` prints the unified diff
for each drifted artifact. Exits non-zero if any device has drifted.

See also: baseline, policy, verify.
"""),
    _c("federation", "Health & compliance",
       "roll up health from federated site collectors", """
`otitbup federation` gives a central appliance a single-pane-of-glass view
over per-site *collector* appliances (the Purdue-model pattern: one
OTITbup per plant/DMZ). Backup bytes federate over plain git (each
collector pushes to a shared remote via `git.push`/`git.remote`); this
command polls each collector's read-only `/api/status` over HTTPS with a
scoped API token and aggregates device counts, coverage, staleness and
failures.

Configure collectors under `federation.collectors` (name, url, token or
token_file, verify_tls). An unreachable collector shows as DOWN rather than
failing the whole roll-up. Exits non-zero if any collector is unreachable.

See also: status, token, serve.
"""),
    _c("verify", "Health & compliance",
       "re-hash stored backups against their manifests", """
`otitbup verify [--all-commits]` re-hashes every stored artifact against
the sha256 manifest recorded at capture time and checks that offloaded
blobs are present and intact — proving your backups aren't silently
corrupt. By default it checks each device's latest backup; `--all-commits`
walks the entire history and also reports orphan blobs.

See also: status, restore, retention.
"""),
    _c("policy", "Health & compliance",
       "run config policy/compliance checks", """
`otitbup policy` lints the latest captured config of every device against
policy rules — built-in vendor-neutral ones (telnet enabled, SNMP
`public`, unencrypted HTTP admin, weak/plaintext passwords) plus any
custom `match`/`absent` regex rules in the `policy` config — and prints
findings sorted by severity. It turns the backup archive into a compliance
scan. Exits non-zero if there are findings.

See also: report, search.
"""),

    # ------------------------------------------------ Retention & strategy
    _c("retention", "Retention & strategy",
       "show retention policies and prune expired blobs", """
`otitbup retention [--apply]` shows every device's effective retention
policy (resolved device > zone > site > global > default) and what would
be pruned. Large artifacts are offloaded to a deduplicated blob store and
expire by `keep_versions` / `keep_days`; text configs stay in git forever
and are never rewritten. Without `--apply` it is a dry run; `--apply`
deletes the expired blobs. Run it from cron.

See also: verify, strategy, export.
"""),
    _c("strategy", "Retention & strategy",
       "evaluate the 3-2-1 / 3-2-1-1-0 backup strategy", """
`otitbup strategy` grades your deployment against the 3-2-1 rule (3 copies,
2 media, 1 offsite) and 3-2-1-1-0 (+1 offline, 0 errors), and prints what's
missing with remediation. otitbup maps the copies to the local repo, a git
remote mirror (`git.push`+`git.remote`), and a portable `otitbup export`
archive; declare the offline copy under `strategy.offline` so it counts.

See also: export, retention, verify.
"""),
    _c("export", "Retention & strategy",
       "write a portable archive for offsite/offline storage", """
`otitbup export [--out FILE]` bundles the git repo, blob store and run
store into a single .tar.gz to copy to removable or offsite media — the
third copy of the 3-2-1 rule and the air-gapped copy of 3-2-1-1-0. Point
`strategy.offline.path` at it so the Strategy page counts it.

See also: strategy, restore, offsite.
"""),
    _c("offsite", "Retention & strategy",
       "encrypted offsite copy on an external server / cloud", """
`otitbup offsite {genkey|push|list|pull|restore}` keeps an encrypted copy
of the whole backup (git repo + blob store + run store) on an external
server or cloud object store — the "1 offsite" leg of 3-2-1, hardened.

The snapshot is a single tarball encrypted with a Fernet key BEFORE upload,
so the remote only ever holds ciphertext; the key stays on the appliance.
Transports: `file` (a dir / NFS/SMB mount / removable media), `sftp` (any
SSH server, needs otitbup[sftp]), and `s3` (AWS S3 or any S3-compatible
store — MinIO, Backblaze B2, Wasabi — signed with SigV4 over stdlib, no
boto3). Configure under `offsite:`.

  otitbup offsite genkey > offsite.key   # then set offsite.key_file
  otitbup offsite push                   # upload an encrypted snapshot
  otitbup offsite list                   # what's stored offsite
  otitbup offsite pull [--name N --out D]        # fetch+decrypt+extract
  otitbup offsite restore DEVICE [--name N --commit H --out D]

`offsite restore` pulls a snapshot and produces a hash-verified restore
bundle for one device straight from the offsite copy — no device writes,
no need to touch the live appliance.

See also: export, restore, strategy.
"""),

    # ----------------------------------------------------------- Recovery
    _c("restore", "Recovery",
       "export a hash-verified restore bundle (no device writes)", """
`otitbup restore DEVICE [--commit H] [--out DIR]` exports the exact
versioned artifacts of a backup into a bundle directory, verifies every
file against the manifest sha256, and writes a RESTORE.md checklist with
driver-specific vendor-tool instructions. otitbup performs NO device writes
for PLCs/RTUs — a human does the restore with vendor tooling; this gives
them the right files and a procedure. Content expired by retention is
flagged, never silently missing.

See also: net-restore, dr-plan, rehearse, verify.
"""),
    _c("net-restore", "Recovery",
       "push a stored config to a network device (dry run default)", """
`otitbup net-restore DEVICE [--commit H] [--apply]` is the one automated
write path, and only for network gear (`cisco_ios`, `cisco_asa`,
`generic_ssh`). By default it is a DRY RUN: it captures the device's
current running config, shows the candidate config that would be pushed,
and writes nothing. With `--apply` it pushes the stored config, then
re-reads and verifies the result. PLC/RTU drivers are refused — use
`restore` for those.

See also: restore, rehearse.
"""),
    _c("dr-plan", "Recovery",
       "write an HTML disaster-recovery runbook for a site", """
`otitbup dr-plan SITE [--out FILE]` generates a per-site DR runbook: every
device's latest backup, artifact hashes, restore instructions and last
rehearsal — the document you print and keep in the cabinet for the day a
controller dies.

See also: restore, rehearse, report.
"""),
    _c("rehearse", "Recovery",
       "record a restore-rehearsal result", """
`otitbup rehearse DEVICE [--by WHO] [--notes ...] [--result pass|fail]`
records that a restore was tested. By default it exports and hash-verifies
a bundle and derives pass/fail from that; `--result` overrides. Rehearsal
history surfaces on device pages, in DR runbooks and in the compliance
report — because a backup that's never been restored is a hope, not a
backup.

See also: restore, dr-plan, report.
"""),

    # -------------------------------------------- Discovery & inventory
    _c("discover", "Discovery & inventory",
       "scan subnets for devices; write a YAML proposal", """
`otitbup discover SUBNET... [--enrich] [--out FILE]` runs an opt-in,
strictly sequential, TCP-connect scan of known OT/IT ports and writes an
inventory-shaped YAML *proposal* for human review — it never edits the
inventory. `--enrich` additionally probes each finding's identity
(vendor/model via SNMP or the matching identity driver) and annotates the
proposal. OT-safe by design: no payloads, no UDP broadcast, a configurable
inter-probe delay.

See also: reconcile, netbox, drivers.
"""),
    _c("reconcile", "Discovery & inventory",
       "compare the inventory against a network scan", """
`otitbup reconcile SUBNET... [--out FILE]` scans the network and compares
it to your inventory, reporting **unmanaged** hosts (answering on the
network but not backed up) and **unreachable** devices (in the inventory
but silent). Coverage you can prove. Unmanaged hosts can be written as a
YAML proposal for review.

See also: discover, netbox.
"""),
    _c("netbox", "Discovery & inventory",
       "reconcile inventory against NetBox, or import from it", """
`otitbup netbox [reconcile|import] [--url U --token T]` integrates with
NetBox (your CMDB/IPAM). `reconcile` (default) reports devices in NetBox
that otitbup does NOT back up and inventory devices absent from NetBox.
`import` turns NetBox devices into an inventory proposal (driver guessed
from platform) for review. It reads NetBox; it never edits your inventory
automatically. Credentials come from flags or the `netbox` config.

See also: reconcile, discover.
"""),

    # --------------------------------------------------------- Reporting
    _c("report", "Reporting",
       "write a compliance report (html/csv/pdf/docx, signable)", """
`otitbup report [--format html|csv|pdf|docx] [--out FILE] [--sign
--key-file KEY]` writes a compliance report covering coverage,
unexpected/unannotated changes, policy findings and rehearsal status. CSV,
PDF and DOCX are all generated with the standard library; the PDF is
richly formatted (title banner, KPI tiles, a vector bar chart, shaded
tables). `--sign` adds a detached Ed25519 signature (and a .pubkey) so
auditors can confirm it wasn't altered. Set `reports.interval` to have the
daemon emit one on a schedule.

See also: report-verify, policy, status.
"""),
    _c("report-verify", "Reporting", "verify a signed report", """
`otitbup report-verify REPORT [--sig FILE] [--pubkey FILE]` checks a
report's detached Ed25519 signature (defaults: `<report>.sig` and
`<report>.pubkey`). Exits 0 for VALID, non-zero for INVALID — tampering
with even one byte fails verification.

See also: report.
"""),

    # ---------------------------------------------------- Web UI & secrets
    _c("passwd", "Web UI & secrets",
       "hash a web UI password (prints a config snippet)", """
`otitbup passwd [--username U] [--password P]` prints a `webui.auth`
snippet with a PBKDF2-hashed password (prompted securely if `--password`
is omitted). Passwords are never stored in plaintext. For multiple users
with roles, add entries under `webui.users`, or create them at runtime
from the web UI's Users page (admin).

See also: serve, certgen.
"""),
    _c("certgen", "Web UI & secrets",
       "generate a self-signed TLS pair for the web UI", """
`otitbup certgen [--host NAME] [--ip ADDR] [--out-dir DIR] [--days N]`
writes a self-signed EC P-256 certificate/key pair (key mode 0600) and
prints the `webui.tls` snippet. OT appliances rarely have a PKI, so this
makes HTTPS turnkey; for a real CA, point `webui.tls` at your PEM files
instead. Needs the crypto extra.

See also: serve, passwd.
"""),
    _c("secrets", "Web UI & secrets",
       "manage encrypted secrets (genkey/encrypt/decrypt)", """
`otitbup secrets genkey|encrypt|decrypt` manages the Fernet-encrypted
secrets file. `genkey --out KEY` writes a key (mode 0600); `encrypt SRC
DST` encrypts a plaintext secrets YAML (validated first); `decrypt SRC`
prints the plaintext to edit. Point the config at it with
`secrets.backend: encryptedfile`. Vault and CyberArk are also supported as
backends (config only). Needs the crypto extra for the encrypted file
backend.

See also: passwd.
"""),

    _c("verify-audit", "Health & compliance",
       "verify the tamper-evident audit-log chain", """
`otitbup verify-audit` recomputes the audit log's hash chain and reports
whether it is intact. Each audit entry is chained to the previous one's
hash, so any edit or deletion of a row breaks the chain and is detected —
tamper-evidence for who did what. Exits non-zero and names the first bad
entry if tampering is found.

See also: report.
"""),
    _c("token", "Web UI & secrets", "manage scoped API tokens", """
`otitbup token create NAME [--role R] [--scopes GLOBS] [--days N]` mints an
API token for the write API and prints it once (only its hash is stored).
`token list` and `token delete NAME` manage them. A token carries a role
(viewer/operator/admin) and scopes (space-separated site/zone globs), so
automation gets least-privilege access without a user's full credentials.
Use it as `Authorization: Bearer <token>` against the API.

See also: serve, passwd.
"""),
    # -------------------------------------------------------------- Help
    _c("help", "Help", "list the commands with a short description", """
`otitbup help` prints every command grouped by theme with a one-line
summary. For a full description of one command, run
`otitbup explain <command>`. (`otitbup --help` gives the raw argparse
usage; this is the friendlier overview.)

See also: explain.
"""),
    _c("explain", "Help", "describe a command at length", """
`otitbup explain COMMAND` prints a detailed description of one command:
what it does, its arguments and options, examples, and related commands.
Run `otitbup help` for the list of commands you can explain.

Example:
  otitbup explain backup

See also: help.
"""),
]}


def render_help() -> str:
    """The grouped one-line summary view for `otitbup help`."""
    width = max(len(c.name) for c in COMMANDS.values())
    lines = ["otitbup — OT/IT backup. Commands:\n"]
    for group in GROUPS:
        members = [c for c in COMMANDS.values() if c.group == group]
        if not members:
            continue
        lines.append(f"{group}:")
        for cmd in members:
            lines.append(f"  {cmd.name:<{width}}  {cmd.summary}")
        lines.append("")
    lines.append("Run 'otitbup explain <command>' for a full description,")
    lines.append("or 'otitbup <command> --help' for exact usage.")
    return "\n".join(lines)


def render_explain(command: str) -> tuple[str, bool]:
    """(text, found) for `otitbup explain <command>`."""
    cmd = COMMANDS.get(command)
    if cmd is None:
        # Suggest close matches.
        import difflib
        near = difflib.get_close_matches(command, COMMANDS, n=3)
        msg = f"unknown command: {command!r}"
        if near:
            msg += "\ndid you mean: " + ", ".join(near) + "?"
        msg += "\nrun 'otitbup help' for the list of commands."
        return msg, False
    text = (
        f"otitbup {cmd.name} — {cmd.summary}\n"
        f"group: {cmd.group}\n\n{cmd.detail}\n"
    )
    return text, True
