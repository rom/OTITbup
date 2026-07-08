"""otitbup command-line interface.

    otitbup help                       # grouped list of all commands
    otitbup explain backup             # full description of one command
    otitbup -c otitbup.yml validate
    otitbup -c otitbup.yml list
    otitbup -c otitbup.yml backup [--all | DEVICE ...] [--force]
    otitbup -c otitbup.yml diff DEVICE
    otitbup -c otitbup.yml log [DEVICE]
    otitbup -c otitbup.yml daemon
    otitbup -c otitbup.yml serve [--host H] [--port P]
    otitbup secrets genkey [--out keyfile]
    otitbup secrets encrypt plain.yml encrypted.yml [--key-file keyfile]
    otitbup secrets decrypt encrypted.yml [--key-file keyfile]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .alerts import AlertManager
from .config import ConfigError, load_config
from .daemon import Daemon
from .gitstore import GitStore
from .runner import Runner
from .secrets import load_backend


def _build_events(config):
    from .events import EventBus
    from .runstore import default_runstore
    return EventBus(
        config.events, runstore=default_runstore(config),
        tickets=config.tickets,
    )


def _build_runner(config, force: bool = False, events=None,
                  dry_run: bool = False) -> Runner:
    from .runner import default_gitstore
    store = default_gitstore(config)
    secrets = load_backend(
        config.secrets, base_dir=Path(config.data_dir).parent
    ) if config.secrets else None
    return Runner(
        config,
        store,
        secrets=secrets,
        alerts=AlertManager(
            config.alerts,
            state_path=Path(config.data_dir).parent / "alert-state.json",
        ),
        force=force,
        events=events or _build_events(config),
        dry_run=dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="otitbup",
        description="OT and IT configuration backup into versioned git storage",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-c", "--config", default="otitbup.yml", help="config file path"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="check the config file and exit")
    sub.add_parser("list", help="list configured devices")
    sub.add_parser("drivers", help="list available drivers")
    sub.add_parser(
        "help", help="list the commands with a short description")
    p_explain = sub.add_parser(
        "explain", help="describe a command at length")
    p_explain.add_argument("topic", help="the command to explain")

    p_init = sub.add_parser("init", help="scaffold a starter config")
    p_init.add_argument("--dir", default=".", help="directory to write into")

    p_backup = sub.add_parser("backup", help="run a backup now")
    p_backup.add_argument("devices", nargs="*", help="device names (default: all)")
    p_backup.add_argument("--site", action="append", help="limit to site(s)")
    p_backup.add_argument("--zone", action="append", help="limit to zone(s)")
    p_backup.add_argument(
        "--force", action="store_true",
        help="ignore maintenance windows",
    )
    p_backup.add_argument(
        "--dry-run", action="store_true",
        help="collect but don't commit (test reachability/auth)",
    )

    p_test = sub.add_parser(
        "test", help="test reachability/credentials without committing")
    p_test.add_argument("devices", nargs="*", help="devices (default: all)")
    p_test.add_argument("--site", action="append")
    p_test.add_argument("--zone", action="append")

    p_gc = sub.add_parser("gc", help="repack/prune the backup git repo")
    p_gc.add_argument("--aggressive", action="store_true")

    p_blobkey = sub.add_parser(
        "blobkey", help="rotate/enable/disable blob-store encryption at rest")
    blobkey_sub = p_blobkey.add_subparsers(
        dest="blobkey_command", required=True)
    p_bkrot = blobkey_sub.add_parser(
        "rotate", help="re-encrypt every blob to a new key")
    p_bkrot.add_argument(
        "--old-key-file",
        help="current key file (default: the configured encryption key)")
    p_bkrot.add_argument(
        "--new-key-file", help="new key file ('-' or omit with --decrypt)")
    p_bkrot.add_argument(
        "--decrypt", action="store_true",
        help="remove encryption (store blobs in plaintext)")
    blobkey_sub.add_parser("genkey", help="print a new Fernet blob key")

    sub.add_parser(
        "verify-audit", help="verify the tamper-evident audit-log chain")

    p_token = sub.add_parser("token", help="manage scoped API tokens")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)
    p_tcreate = token_sub.add_parser("create", help="create an API token")
    p_tcreate.add_argument("name")
    p_tcreate.add_argument("--role", default="operator",
                           choices=["viewer", "operator", "admin"])
    p_tcreate.add_argument("--scopes", default="*",
                           help="space-separated site/zone globs")
    p_tcreate.add_argument("--days", type=int, help="expiry in days")
    token_sub.add_parser("list", help="list API tokens")
    p_tdel = token_sub.add_parser("delete", help="delete a token by name")
    p_tdel.add_argument("name")

    p_diff = sub.add_parser("diff", help="show a device's change or a range")
    p_diff.add_argument("device")
    p_diff.add_argument("--from", dest="from_commit", help="base commit")
    p_diff.add_argument("--to", dest="to_commit", default="HEAD",
                        help="head commit (default HEAD)")

    p_search = sub.add_parser(
        "search", help="search across the latest backup of every device"
    )
    p_search.add_argument("pattern", help="text or regex to grep for")
    p_search.add_argument("--case-sensitive", action="store_true")

    p_baseline = sub.add_parser(
        "baseline", help="manage golden-config baselines and drift"
    )
    baseline_sub = p_baseline.add_subparsers(
        dest="baseline_command", required=True
    )
    p_bset = baseline_sub.add_parser("set", help="approve current as baseline")
    p_bset.add_argument("device")
    p_bset.add_argument("--commit", help="commit to pin (default: latest)")
    p_bset.add_argument("--note")
    p_bclear = baseline_sub.add_parser("clear", help="remove a baseline")
    p_bclear.add_argument("device")
    baseline_sub.add_parser("drift", help="show devices drifted from baseline")

    p_reconcile = sub.add_parser(
        "reconcile", help="compare inventory against a network scan"
    )
    p_reconcile.add_argument("subnets", nargs="+", help="CIDR subnets to scan")
    p_reconcile.add_argument("--timeout", type=float, default=0.5)
    p_reconcile.add_argument("--delay", type=float, default=0.05)
    p_reconcile.add_argument(
        "--out", help="write unmanaged hosts as a YAML proposal to this file"
    )

    p_log = sub.add_parser("log", help="show backup history")
    p_log.add_argument("device", nargs="?")
    p_log.add_argument("-n", "--limit", type=int, default=20)

    p_daemon = sub.add_parser("daemon", help="run the scheduler")
    p_daemon.add_argument(
        "--once", action="store_true", help="one scheduler tick, then exit"
    )

    p_serve = sub.add_parser("serve", help="run the read-only web UI")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)

    p_discover = sub.add_parser(
        "discover",
        help="scan subnets for devices; writes a YAML proposal for review",
    )
    p_discover.add_argument("subnets", nargs="+", help="CIDR subnets to scan")
    p_discover.add_argument("--site", default="discovered-site")
    p_discover.add_argument("--zone", default="discovered")
    p_discover.add_argument("--timeout", type=float, default=0.5)
    p_discover.add_argument(
        "--delay", type=float, default=0.05,
        help="seconds between probes (sequential scan, OT-safe)",
    )
    p_discover.add_argument(
        "--out", default="discovered.yml", help="proposal file to write"
    )
    p_discover.add_argument(
        "--enrich", action="store_true",
        help="probe each finding's identity (vendor/model) via SNMP/driver",
    )

    p_retention = sub.add_parser(
        "retention",
        help="show effective retention policies and prune expired blobs",
    )
    p_retention.add_argument(
        "--apply", action="store_true",
        help="delete expired blobs (default: dry run, print only)",
    )

    p_restore = sub.add_parser(
        "restore",
        help="export a hash-verified restore bundle (no device writes)",
    )
    p_restore.add_argument("device")
    p_restore.add_argument(
        "--commit", help="backup commit to restore from (default: latest)"
    )
    p_restore.add_argument(
        "--out", help="bundle directory (default: restore-<device>-<commit>)"
    )

    p_verify = sub.add_parser(
        "verify", help="re-hash stored backups against their manifests"
    )
    p_verify.add_argument(
        "--all-commits", action="store_true",
        help="verify the whole history (default: latest per device)",
    )

    p_integrity = sub.add_parser(
        "integrity",
        help="full integrity scrub: verify + git fsck (+ signatures)",
    )
    p_integrity.add_argument("--all-commits", action="store_true")
    p_integrity.add_argument(
        "--no-fsck", action="store_true", help="skip git fsck")
    p_integrity.add_argument(
        "--signatures", action="store_true",
        help="also verify signed-commit signatures",
    )
    p_integrity.add_argument(
        "--alert", action="store_true",
        help="emit events and alert on failure (as the daemon does)",
    )

    sub.add_parser(
        "status", help="show per-device backup health from the run store"
    )

    sub.add_parser(
        "anomalies",
        help="report statistical anomalies (slow backups, change storms)",
    )

    p_guids = sub.add_parser(
        "guids", help="show device GUIDs; --assign pins persistent ones")
    p_guids.add_argument(
        "--assign", action="store_true",
        help="write a stable UUID into the config for devices without one",
    )

    p_desired = sub.add_parser(
        "desired",
        help="compare live backups against declared config-as-code",
    )
    p_desired.add_argument(
        "--diff", action="store_true", help="show the unified diff for drift"
    )

    sub.add_parser(
        "federation",
        help="roll up health from federated site collectors",
    )

    p_offsite = sub.add_parser(
        "offsite",
        help="manage the encrypted offsite copy (external server / cloud)",
    )
    offsite_sub = p_offsite.add_subparsers(
        dest="offsite_command", required=True)
    offsite_sub.add_parser("genkey", help="generate an offsite encryption key")
    offsite_sub.add_parser("push", help="upload an encrypted snapshot offsite")
    offsite_sub.add_parser("list", help="list offsite snapshots")
    p_opull = offsite_sub.add_parser(
        "pull", help="download+decrypt+extract a snapshot")
    p_opull.add_argument("--name", help="snapshot name (default: newest)")
    p_opull.add_argument("--out", help="extract directory")
    p_orestore = offsite_sub.add_parser(
        "restore", help="restore a device's bundle from the offsite copy")
    p_orestore.add_argument("device")
    p_orestore.add_argument("--name", help="snapshot name (default: newest)")
    p_orestore.add_argument("--commit", help="backup commit (default: latest)")
    p_orestore.add_argument("--out", help="bundle directory")

    sub.add_parser(
        "policy", help="run config policy checks over the latest backups"
    )

    p_annotate = sub.add_parser(
        "annotate", help="attach a change note (MOC/work order) to a commit"
    )
    p_annotate.add_argument("device")
    p_annotate.add_argument("text", help="annotation text")
    p_annotate.add_argument(
        "--commit", help="commit to annotate (default: latest for the device)"
    )

    p_hold = sub.add_parser(
        "hold",
        help="legal hold: protect a scope's backups from retention pruning",
    )
    hold_sub = p_hold.add_subparsers(dest="hold_command", required=True)
    p_hset = hold_sub.add_parser("set", help="place a legal hold")
    p_hset.add_argument("scope", help="device, 'site/*', 'site/zone/*', or '*'")
    p_hset.add_argument("--reason")
    p_hclear = hold_sub.add_parser("clear", help="release a legal hold")
    p_hclear.add_argument("scope")
    hold_sub.add_parser("list", help="list active legal holds")

    p_maint = sub.add_parser(
        "maintenance",
        help="mark a device/zone/site in maintenance (changes = expected)",
    )
    p_maint.add_argument(
        "scope", help="device name, 'site/*', or 'site/zone/*'"
    )
    p_maint.add_argument(
        "--off", action="store_true", help="clear maintenance instead of setting"
    )
    p_maint.add_argument(
        "--hours", type=float, help="auto-expire after N hours (default: until cleared)"
    )
    p_maint.add_argument("--reason")

    p_report = sub.add_parser(
        "report", help="write a compliance report (html/csv/pdf/docx)"
    )
    p_report.add_argument("--out", help="output path (default by format)")
    p_report.add_argument("--days", type=int, default=30)
    p_report.add_argument(
        "--format", choices=["html", "csv", "pdf", "docx"], default="html"
    )
    p_report.add_argument(
        "--sign", action="store_true", help="sign the report (Ed25519)"
    )
    p_report.add_argument(
        "--key-file", default="report-signing.key",
        help="signing key (generated if absent)",
    )

    p_rverify = sub.add_parser(
        "report-verify", help="verify a signed report"
    )
    p_rverify.add_argument("report")
    p_rverify.add_argument("--sig", help="default: <report>.sig")
    p_rverify.add_argument("--pubkey", help="default: <report>.pubkey")

    p_export = sub.add_parser(
        "export",
        help="write a portable archive (repo+blobs+runstore) for offsite/"
             "offline storage — the 3-2-1 third copy",
    )
    p_export.add_argument("--out", default="otitbup-export.tar.gz")

    sub.add_parser(
        "strategy", help="evaluate the 3-2-1 / 3-2-1-1-0 backup strategy"
    )

    p_netbox = sub.add_parser(
        "netbox", help="reconcile inventory against NetBox, or import from it"
    )
    p_netbox.add_argument(
        "action", choices=["reconcile", "import"], nargs="?",
        default="reconcile",
    )
    p_netbox.add_argument("--url", help="NetBox URL (or config netbox.url)")
    p_netbox.add_argument("--token", help="NetBox API token")
    p_netbox.add_argument("--out", default="netbox-import.yml")
    p_netbox.add_argument("--site", default="netbox")
    p_netbox.add_argument("--zone", default="imported")

    p_dr = sub.add_parser(
        "dr-plan", help="write an HTML disaster-recovery runbook for a site"
    )
    p_dr.add_argument("site")
    p_dr.add_argument("--out", help="default: dr-runbook-<site>.html")

    p_netrestore = sub.add_parser(
        "net-restore",
        help="restore a stored config to a network device (dry run default)",
    )
    p_netrestore.add_argument("device")
    p_netrestore.add_argument("--commit")
    p_netrestore.add_argument(
        "--apply", action="store_true",
        help="push the config (default: dry run, shows the diff only)",
    )

    p_rehearse = sub.add_parser(
        "rehearse",
        help="record a restore-rehearsal result (exports+verifies a bundle)",
    )
    p_rehearse.add_argument("device")
    p_rehearse.add_argument("--by", help="who performed the rehearsal")
    p_rehearse.add_argument("--notes")
    p_rehearse.add_argument(
        "--result", choices=["pass", "fail"],
        help="override; default derives from bundle hash verification",
    )

    p_passwd = sub.add_parser(
        "passwd", help="hash a web UI password (prints a config snippet)"
    )
    p_passwd.add_argument("--username", default="admin")
    p_passwd.add_argument(
        "--password", help="password (omit to be prompted securely)"
    )

    p_certgen = sub.add_parser(
        "certgen", help="generate a self-signed TLS pair for the web UI"
    )
    p_certgen.add_argument(
        "--host", action="append", dest="hosts",
        help="DNS name for the certificate (repeatable; default localhost)",
    )
    p_certgen.add_argument(
        "--ip", action="append", dest="ips",
        help="IP address for the certificate (repeatable)",
    )
    p_certgen.add_argument("--out-dir", default=".")
    p_certgen.add_argument("--days", type=int, default=3650)

    p_secrets = sub.add_parser("secrets", help="manage encrypted secrets")
    secrets_sub = p_secrets.add_subparsers(dest="secrets_command", required=True)
    p_genkey = secrets_sub.add_parser(
        "genkey", help="generate an encryption key"
    )
    p_genkey.add_argument(
        "--out", help="write the key to this file (mode 0600) instead of stdout"
    )
    p_encrypt = secrets_sub.add_parser(
        "encrypt", help="encrypt a plaintext secrets YAML file"
    )
    p_encrypt.add_argument("src", help="plaintext secrets YAML")
    p_encrypt.add_argument("dst", help="encrypted output file")
    p_encrypt.add_argument("--key-file", help="key file (or set OTITBUP_KEY)")
    p_decrypt = secrets_sub.add_parser(
        "decrypt", help="decrypt an encrypted secrets file to stdout"
    )
    p_decrypt.add_argument("src", help="encrypted secrets file")
    p_decrypt.add_argument("--key-file", help="key file (or set OTITBUP_KEY)")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Re-configure from the config file's `logging` block once loaded
    # (below); this basicConfig covers pre-config commands.

    if args.command == "drivers":
        from .drivers import driver_descriptions
        for name, description in driver_descriptions().items():
            print(f"{name:20s} {description}")
        return 0

    if args.command == "help":
        from .helptext import render_help
        print(render_help())
        return 0

    if args.command == "explain":
        from .helptext import render_explain
        text, found = render_explain(args.topic)
        print(text, file=sys.stdout if found else sys.stderr)
        return 0 if found else 2

    if args.command == "init":
        from .scaffold import init_project
        try:
            written = init_project(args.dir)
        except FileExistsError as exc:
            print(f"init error: {exc}", file=sys.stderr)
            return 2
        for path in written:
            print(f"wrote {path}")
        print("edit otitbup.yml and secrets.yml, then `otitbup validate`")
        return 0

    if args.command == "passwd":
        from .auth import hash_password
        password = args.password
        if not password:
            import getpass
            password = getpass.getpass("password: ")
            if getpass.getpass("repeat: ") != password:
                print("passwords do not match", file=sys.stderr)
                return 2
        print("webui:")
        print("  auth:")
        print(f"    username: {args.username}")
        print(f"    password_hash: {hash_password(password)}")
        return 0

    if args.command == "certgen":
        from .tlscert import TLSCertError, generate_self_signed
        out_dir = Path(args.out_dir)
        cert_file = out_dir / "webui-cert.pem"
        key_file = out_dir / "webui-key.pem"
        try:
            generate_self_signed(
                cert_file, key_file,
                hostnames=args.hosts, ips=args.ips, days=args.days,
            )
        except (TLSCertError, ValueError) as exc:
            print(f"certgen error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {cert_file} and {key_file} (key mode 0600)")
        print("webui:")
        print("  tls:")
        print(f"    cert_file: {cert_file}")
        print(f"    key_file: {key_file}")
        return 0

    if args.command == "secrets":
        from . import secrets as secrets_mod
        try:
            if args.secrets_command == "genkey":
                key = secrets_mod.generate_key(args.out)
                if args.out:
                    print(f"key written to {args.out}")
                else:
                    print(key)
            elif args.secrets_command == "encrypt":
                secrets_mod.encrypt_file(args.src, args.dst, args.key_file)
                print(f"encrypted {args.src} -> {args.dst}")
            elif args.secrets_command == "decrypt":
                print(secrets_mod.decrypt_file(args.src, args.key_file), end="")
        except Exception as exc:
            print(f"secrets error: {exc}", file=sys.stderr)
            return 2
        return 0

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    from .logsetup import configure as _configure_logging
    _configure_logging(config.logging, verbose=args.verbose)

    # Emit config.read + process.start once the config is available (skip
    # for read-only introspection commands that don't act on devices).
    from .events import CONFIG_READ, PROCESS_START, PROCESS_STOP
    events = _build_events(config)
    if args.command in ("backup", "daemon", "serve"):
        events.emit(CONFIG_READ, f"config read: {args.config}",
                    detail=args.config)
        events.emit(PROCESS_START, f"otitbup {args.command} starting",
                    detail=args.command)

    if args.command == "validate":
        devices = config.all_devices()
        print(f"OK: {len(config.sites)} site(s), {len(devices)} device(s)")
        return 0

    if args.command == "list":
        for device in config.all_devices():
            print(
                f"{device.qualified_name:40s} driver={device.driver:15s} "
                f"schedule={device.schedule}"
            )
        return 0

    if args.command == "backup":
        try:
            devices = config.select(
                names=args.devices, sites=args.site, zones=args.zone
            )
        except KeyError as exc:
            print(f"backup error: {exc}", file=sys.stderr)
            return 2
        if not devices:
            print("no devices matched the selection", file=sys.stderr)
            return 1
        runner = _build_runner(config, force=args.force, events=events,
                               dry_run=args.dry_run)
        results = runner.backup_devices(devices)
        for result in results:
            status = "OK " if result.ok else "FAIL"
            print(f"{status} {result.device:40s} {result.message}")
        events.emit(PROCESS_STOP, "otitbup backup finished", detail="backup")
        return 0 if all(r.ok for r in results) else 1

    if args.command == "test":
        try:
            devices = config.select(
                names=args.devices, sites=args.site, zones=args.zone)
        except KeyError as exc:
            print(f"test error: {exc}", file=sys.stderr)
            return 2
        runner = _build_runner(config, force=True, events=events,
                               dry_run=True)
        results = runner.backup_devices(devices)
        for result in results:
            status = "OK  " if result.ok else "FAIL"
            print(f"{status} {result.device:40s} {result.message}")
        return 0 if all(r.ok for r in results) else 1

    if args.command == "gc":
        from .runner import default_gitstore
        store = default_gitstore(config)
        store.ensure_repo()
        before = store.repo_size_bytes()
        store.gc(aggressive=args.aggressive)
        after = store.repo_size_bytes()
        print(f"git gc: {before / 1048576:.1f} MiB -> {after / 1048576:.1f} MiB")
        return 0

    if args.command == "blobkey":
        import os

        from .blobstore import BlobStoreError, rotate_key
        blobs_dir = Path(config.data_dir).parent / "blobs"
        if args.blobkey_command == "genkey":
            from cryptography.fernet import Fernet
            print(Fernet.generate_key().decode())
            return 0
        # rotate
        enc = config.encryption or {}
        current = (os.environ.get("OTITBUP_BLOB_KEY") or enc.get("blob_key"))
        if not current and enc.get("blob_key_file"):
            current = Path(enc["blob_key_file"]).read_text().strip()
        old_key = None
        if args.old_key_file:
            old_key = Path(args.old_key_file).read_text().strip()
        elif current:
            old_key = current
        new_key = None
        if not args.decrypt:
            if not args.new_key_file:
                print("blobkey rotate: --new-key-file or --decrypt required",
                      file=sys.stderr)
                return 2
            new_key = Path(args.new_key_file).read_text().strip()
        try:
            rotated, skipped = rotate_key(blobs_dir, old_key, new_key)
        except BlobStoreError as exc:
            print(f"blobkey error: {exc}", file=sys.stderr)
            return 1
        print(f"re-encrypted {rotated} blob(s)"
              + (f", skipped {skipped} (hash mismatch)" if skipped else ""))
        print("update encryption.blob_key_file in the config to the new key"
              if new_key else "remove encryption.blob_key* from the config")
        return 0

    if args.command == "verify-audit":
        from .runstore import default_runstore
        intact, bad_id = default_runstore(config).verify_audit()
        if intact:
            print("audit log intact (hash chain verified)")
            return 0
        print(f"AUDIT TAMPERING DETECTED at entry id {bad_id}",
              file=sys.stderr)
        return 1

    if args.command == "token":
        from . import apitoken
        from .runstore import default_runstore
        runstore = default_runstore(config)
        if args.token_command == "create":
            plaintext = apitoken.create(
                runstore, args.name, args.role, args.scopes, args.days)
            print(f"token '{args.name}' created (role={args.role}, "
                  f"scopes={args.scopes}):")
            print(f"\n  {plaintext}\n")
            print("store it now — it is not shown again.")
        elif args.token_command == "list":
            import datetime as _dt
            for t in runstore.list_api_tokens():
                exp = ("never" if not t["expires_at"] else
                       _dt.datetime.fromtimestamp(
                           t["expires_at"], _dt.UTC).strftime("%Y-%m-%d"))
                print(f"{t['name']:20s} role={t['role']:9s} "
                      f"scopes={t['scopes']:12s} expires={exp}")
        elif args.token_command == "delete":
            n = runstore.delete_api_token(args.name)
            print(f"deleted {n} token(s)")
        return 0

    if args.command == "diff":
        [device] = config.find_devices([args.device])
        store = GitStore(config.data_dir)
        if args.from_commit:
            print(
                store.diff_between(device, args.from_commit, args.to_commit)
                or "no differences"
            )
        else:
            print(store.last_diff(device) or "no history")
        return 0

    if args.command == "search":
        store = GitStore(config.data_dir)
        store.ensure_repo()
        by_path = {d.path: d for d in config.all_devices()}
        hits = store.search(
            args.pattern, ignore_case=not args.case_sensitive
        )
        for repo_path, lineno, text in hits:
            # Map sites/<site>/<zone>/<device>/<artifact> back to a device.
            device_name = repo_path
            for path, device in by_path.items():
                if repo_path.startswith(path + "/"):
                    device_name = (
                        f"{device.qualified_name}:{repo_path[len(path) + 1:]}"
                    )
                    break
            print(f"{device_name}:{lineno}: {text.strip()}")
        print(f"\n{len(hits)} match(es)", file=sys.stderr)
        return 0 if hits else 1

    if args.command == "baseline":
        import time

        from .baseline import all_drift
        from .runstore import default_runstore
        store = GitStore(config.data_dir)
        runstore = default_runstore(config)
        if args.baseline_command == "set":
            [device] = config.find_devices([args.device])
            commit = args.commit or store.last_commit_hash(device)
            if not commit:
                print(f"no backups for {device.qualified_name}",
                      file=sys.stderr)
                return 1
            runstore.set_baseline(
                device.qualified_name, commit, time.time(),
                set_by=userEmail_or_none(), note=args.note,
            )
            print(f"baseline for {device.qualified_name} = {commit[:10]}")
            return 0
        if args.baseline_command == "clear":
            [device] = config.find_devices([args.device])
            runstore.clear_baseline(device.qualified_name)
            print(f"baseline cleared for {device.qualified_name}")
            return 0
        # drift
        drifted = 0
        for drift in all_drift(config, store, runstore):
            if not drift.has_baseline:
                state = "no-baseline"
            elif drift.drifted:
                state = "DRIFTED"
                drifted += 1
            else:
                state = "ok"
            print(f"{state:12s} {drift.device}")
        print(f"\n{drifted} device(s) drifted from baseline",
              file=sys.stderr)
        return 0 if not drifted else 1

    if args.command == "reconcile":
        from .discovery import proposal_yaml
        from .reconcile import reconcile
        result = reconcile(
            config, args.subnets, timeout=args.timeout, delay=args.delay
        )
        print(f"unmanaged (on network, not in inventory): "
              f"{len(result.unmanaged)}")
        for finding in result.unmanaged:
            ports = ", ".join(str(p) for p in finding.open_ports)
            print(f"  {finding.address:16s} ports {ports:20s} "
                  f"-> {finding.driver}")
        print(f"unreachable (in inventory, no scan response): "
              f"{len(result.unreachable)}")
        for name in result.unreachable:
            print(f"  {name}")
        print(f"managed & reachable: {len(result.managed_reachable)}")
        if args.out and result.unmanaged:
            Path(args.out).write_text(
                proposal_yaml(result.unmanaged, "reconciled", "found")
            )
            print(f"\nunmanaged hosts written to {args.out} for review")
        return 0

    if args.command == "log":
        store = GitStore(config.data_dir)
        device = None
        if args.device:
            [device] = config.find_devices([args.device])
        print(store.history(device, limit=args.limit) or "no history")
        return 0

    if args.command == "daemon":
        runner = _build_runner(config, events=events)
        daemon = Daemon(config, runner, config_path=args.config, events=events)
        if args.once:
            count = daemon.run_once()
            print(f"backed up {count} device(s)")
            return 0
        try:
            daemon.run_forever()
        except KeyboardInterrupt:
            events.emit(PROCESS_STOP, "otitbup daemon stopping",
                        detail="daemon")
            return 0

    if args.command == "serve":
        from .runner import default_blobstore
        from .runstore import default_runstore
        from .webui import serve
        store = GitStore(config.data_dir)
        store.ensure_repo()
        tls = config.webui.get("tls")
        if tls:
            # cert/key paths are relative to the config file's directory.
            base = Path(args.config).resolve().parent
            tls = {
                key: str(base / value) if not Path(value).is_absolute()
                else value
                for key, value in tls.items()
            }
        from .events import WEBUI_START, WEBUI_STOP
        host = args.host or config.webui.get("host", "127.0.0.1")
        port = args.port or int(config.webui.get("port", 8080))
        events.emit(WEBUI_START, f"web UI starting on {host}:{port}",
                    detail=f"{host}:{port}")
        try:
            serve(
                config, store,
                host=host, port=port,
                auth=config.webui.get("auth"),
                tls=tls,
                blobstore=default_blobstore(config),
                runstore=default_runstore(config),
                events=events,
                config_path=args.config,
            )
        except KeyboardInterrupt:
            events.emit(WEBUI_STOP, "web UI stopping", detail=f"{host}:{port}")
            return 0

    if args.command == "discover":
        from .discovery import proposal_yaml, scan
        exclude = {d.address for d in config.all_devices() if d.address}
        print(
            f"scanning {', '.join(args.subnets)} sequentially "
            f"(timeout {args.timeout}s, delay {args.delay}s) ..."
        )
        findings = scan(
            args.subnets, timeout=args.timeout, delay=args.delay,
            exclude=exclude,
        )
        if not findings:
            print("no new devices found")
            return 0
        if args.enrich:
            from .discovery import enrich
            print("enriching findings (identity probe) ...")
            enrich(findings)
        for finding in findings:
            ports = ", ".join(str(p) for p in finding.open_ports)
            extra = f"  [{finding.identity}]" if finding.identity else ""
            print(f"  {finding.address:16s} ports {ports:20s} "
                  f"-> {finding.driver}{extra}")
        Path(args.out).write_text(
            proposal_yaml(findings, args.site, args.zone)
        )
        print(
            f"\n{len(findings)} device(s) written to {args.out} — review "
            "and merge the entries you approve into your config; nothing "
            "was added automatically"
        )
        return 0

    if args.command == "retention":
        from .retention import apply as retention_apply
        from .retention import describe_policy, plan
        from .runner import default_blobstore
        from .runstore import default_runstore
        store = GitStore(config.data_dir)
        store.ensure_repo()
        blobstore = default_blobstore(config)
        prune_plan = plan(config, store, blobstore,
                          runstore=default_runstore(config))
        for dplan in prune_plan.devices:
            sources = ", ".join(
                f"{key}={dplan.sources[key]}"
                for key in sorted(dplan.policy)
                if dplan.sources.get(key) not in (None, "default")
            )
            print(
                f"{dplan.device:40s} {describe_policy(dplan.policy):45s} "
                f"backups kept {dplan.kept_backups}/{dplan.backups}"
                + ("  [HELD]" if dplan.held else "")
                + (f"   [{sources}]" if sources else "")
            )
        print(
            f"\nblob store: {prune_plan.blob_count} blob(s), "
            f"{prune_plan.blob_bytes / 1048576:.1f} MiB total; "
            f"{len(prune_plan.deletable)} expired "
            f"({prune_plan.deletable_bytes / 1048576:.1f} MiB)"
        )
        if not prune_plan.deletable:
            return 0
        if args.apply:
            freed = retention_apply(prune_plan, blobstore)
            print(f"pruned {len(prune_plan.deletable)} blob(s), "
                  f"freed {freed / 1048576:.1f} MiB")
        else:
            print("dry run — re-run with --apply to delete")
        return 0

    if args.command == "restore":
        from .restore import RestoreError, export_bundle
        from .runner import default_blobstore
        [device] = config.find_devices([args.device])
        store = GitStore(config.data_dir)
        try:
            commit = args.commit or store.last_commit_hash(device)
            if not commit:
                print(f"no backups for {device.qualified_name}", file=sys.stderr)
                return 1
            out = args.out or f"restore-{device.name}-{commit[:8]}"
            commit, mismatches = export_bundle(
                store, device, out, commit=commit,
                blobstore=default_blobstore(config),
            )
        except RestoreError as exc:
            print(f"restore error: {exc}", file=sys.stderr)
            return 1
        print(f"restore bundle written to {out} (backup {commit[:10]})")
        if mismatches:
            print(
                "HASH MISMATCH on: " + ", ".join(mismatches)
                + " — do not use this bundle", file=sys.stderr,
            )
            return 1
        print("all artifact hashes verified; see RESTORE.md for the checklist")
        return 0

    if args.command == "verify":
        from .runner import default_blobstore
        from .verify import verify
        store = GitStore(config.data_dir)
        store.ensure_repo()
        report = verify(
            config, store, blobstore=default_blobstore(config),
            all_commits=args.all_commits,
        )
        for key, problems in report.problems.items():
            for problem in problems:
                print(f"FAIL {key}: {problem}")
        print(
            f"verified {report.checked_commits} commit(s) across "
            f"{report.checked_devices} device(s); "
            f"{len(report.problems)} with problems"
            + (f", {report.orphan_blobs} orphan blob(s)"
               if args.all_commits else "")
        )
        return 0 if report.ok else 1

    if args.command == "integrity":
        import time as _time

        from . import integrity as integrity_mod
        from .runner import default_blobstore
        from .runstore import default_runstore
        store = GitStore(config.data_dir)
        store.ensure_repo()
        blobstore = default_blobstore(config)
        if args.alert:
            result = integrity_mod.run_scheduled(
                config, store, blobstore, default_runstore(config),
                events, AlertManager(
                    config.alerts,
                    state_path=Path(config.data_dir).parent
                    / "alert-state.json"),
                _time.time(), all_commits=args.all_commits)
        else:
            result = integrity_mod.check(
                config, store, blobstore=blobstore,
                all_commits=args.all_commits, do_fsck=not args.no_fsck,
                do_signatures=args.signatures)
        for problem in result.content_problems:
            print(f"CONTENT  {problem}")
        for problem in result.repo_problems:
            print(f"REPO     {problem}")
        for problem in result.signature_problems:
            print(f"SIG      {problem}")
        print("\n" + result.summary())
        return 0 if result.ok else 1

    if args.command == "status":
        import time

        from .runstore import default_runstore
        runstore = default_runstore(config)
        now = time.time()
        for device in config.all_devices():
            st = runstore.status(device.qualified_name)
            if st.last_success is None:
                health = "NEVER"
            elif now - st.last_success > 7 * 86400:
                health = "STALE"
            elif not st.last_ok:
                health = "FAILING"
            else:
                health = "ok"
            age = (
                "-" if st.last_success is None
                else f"{(now - st.last_success) / 86400:.1f}d"
            )
            print(
                f"{health:8s} {device.qualified_name:40s} "
                f"last success {age:8s} "
                f"fails={st.consecutive_failures} {st.last_message}"
            )
        return 0

    if args.command == "anomalies":
        from . import anomaly
        from .runstore import default_runstore
        runstore = default_runstore(config)
        history = int(config.anomaly.get("history", 50))
        found = 0
        for device in config.all_devices():
            runs = runstore.recent_runs(device.qualified_name, limit=history)
            for finding in anomaly.analyze(
                device.qualified_name, runs, config.anomaly
            ):
                found += 1
                print(f"{finding.kind:14s} {finding.device:40s} "
                      f"{finding.message}")
        print(f"\n{found} anomaly(ies) across {len(config.all_devices())} "
              "device(s)", file=sys.stderr)
        return 0 if not found else 1

    if args.command == "guids":
        import uuid as _uuid

        from . import configedit
        assigned = 0
        for device in config.all_devices():
            pinned = bool(device.guid)
            if args.assign and not pinned:
                new = str(_uuid.uuid4())
                try:
                    configedit.set_device(
                        args.config, device.site, device.zone, device.name,
                        {"guid": new})
                except configedit.ConfigEditError as exc:
                    print(f"guid assign error: {exc}", file=sys.stderr)
                    return 2
                assigned += 1
                print(f"{device.qualified_name:40s} {new}  (assigned)")
            else:
                tag = "pinned" if pinned else "derived"
                print(f"{device.qualified_name:40s} "
                      f"{device.effective_guid}  ({tag})")
        if args.assign:
            print(f"\nassigned {assigned} persistent GUID(s)", file=sys.stderr)
        return 0

    if args.command == "desired":
        from . import desired as desired_mod
        from .runner import default_blobstore
        store = GitStore(config.data_dir)
        store.ensure_repo()
        results = desired_mod.check_all(
            config, store, blobstore=default_blobstore(config))
        drifted = 0
        for res in results:
            state = "in-sync" if res.in_sync else "DRIFTED"
            if not res.in_sync:
                drifted += 1
            print(f"{state:8s} {res.device:40s} "
                  f"{res.drifted}/{res.checked} artifact(s) drifted")
            if args.diff:
                for art in res.artifacts:
                    if art.status == "drift" and art.diff:
                        print(art.diff)
                    elif art.status == "missing":
                        print(f"  {art.artifact}: not in any backup")
        if not results:
            print("no devices have a desired-config declaration "
                  f"(set desired.dir; looked in "
                  f"{config.desired.get('dir', 'desired')})",
                  file=sys.stderr)
        print(f"\n{drifted} device(s) drifted from desired config",
              file=sys.stderr)
        return 0 if not drifted else 1

    if args.command == "federation":
        from . import federation
        healths = federation.poll_all(config.federation)
        if not healths:
            print("no collectors configured (set federation.collectors)",
                  file=sys.stderr)
            return 0
        for h in healths:
            if h.ok:
                t = h.totals
                print(f"OK   {h.name:20s} devices={t['devices']:4d} "
                      f"covered={t['covered']:4d} stale={t['stale']:3d} "
                      f"failing={t['failing']:3d}")
            else:
                print(f"DOWN {h.name:20s} {h.error}")
        agg = federation.aggregate(healths)
        t = agg["totals"]
        print(f"\n{agg['reachable']}/{agg['collectors']} collectors reachable; "
              f"total devices={t['devices']} covered={t['covered']} "
              f"stale={t['stale']} failing={t['failing']}")
        return 0 if agg["unreachable"] == 0 else 1

    if args.command == "offsite":
        import datetime as _dt
        import os

        from . import offsite as offsite_mod
        enc = config.encryption or {}
        blob_key = (os.environ.get("OTITBUP_BLOB_KEY") or enc.get("blob_key"))
        if not blob_key and enc.get("blob_key_file"):
            blob_key = Path(enc["blob_key_file"]).read_text().strip()
        try:
            if args.offsite_command == "genkey":
                print(offsite_mod.generate_key())
                print("store this as offsite.key_file — it is NOT kept on the "
                      "remote and is required to restore.", file=sys.stderr)
                return 0
            if args.offsite_command == "push":
                stamp = _dt.datetime.now(_dt.UTC).strftime(
                    "%Y%m%d-%H%M%S")
                name = offsite_mod.push(config, stamp)
                print(f"uploaded {name}")
                return 0
            if args.offsite_command == "list":
                names = offsite_mod.list_snapshots(config)
                for n in names:
                    print(n)
                print(f"\n{len(names)} snapshot(s)", file=sys.stderr)
                return 0
            if args.offsite_command == "pull":
                out = Path(args.out or "offsite-restore")
                name, extracted = offsite_mod.pull(config, args.name, out)
                print(f"{name} extracted to {extracted} "
                      "(contains data/, blobs/, runstore.db)")
                return 0
            if args.offsite_command == "restore":
                [device] = config.find_devices([args.device])
                out = args.out or f"restore-{device.name}-offsite"
                if Path(out).exists() and any(Path(out).iterdir()):
                    print(f"output directory not empty: {out}", file=sys.stderr)
                    return 1
                name, commit, mismatches, bundle = \
                    offsite_mod.restore_from_offsite(
                        config, device, Path(out), name=args.name,
                        commit=args.commit, blob_key=blob_key)
                print(f"restored {device.qualified_name} from {name} "
                      f"(backup {commit[:10]}) -> {bundle}")
                if mismatches:
                    print("HASH MISMATCH on: " + ", ".join(mismatches),
                          file=sys.stderr)
                    return 1
                print("all artifact hashes verified; see RESTORE.md")
                return 0
        except offsite_mod.OffsiteError as exc:
            print(f"offsite error: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.command == "policy":
        from .policy import check_all, severity_rank
        findings = check_all(config, GitStore(config.data_dir))
        flat = [f for group in findings.values() for f in group]
        flat.sort(key=lambda f: severity_rank(f.severity), reverse=True)
        for f in flat:
            print(f"{f.severity:8s} {f.device:40s} {f.rule_id:20s} "
                  f"{f.description} [{f.artifact}]")
        print(f"\n{len(flat)} finding(s) across {len(findings)} device(s)")
        return 0 if not flat else 1

    if args.command == "annotate":
        [device] = config.find_devices([args.device])
        store = GitStore(config.data_dir)
        commit = args.commit or store.last_commit_hash(device)
        if not commit:
            print(f"no backups for {device.qualified_name}", file=sys.stderr)
            return 1
        store.set_annotation(commit, args.text)
        print(f"annotated {commit[:10]} on {device.qualified_name}")
        return 0

    if args.command == "hold":
        import time

        from .runstore import default_runstore
        runstore = default_runstore(config)
        if args.hold_command == "set":
            runstore.set_hold(args.scope, time.time(), reason=args.reason,
                              set_by=userEmail_or_none())
            print(f"legal hold set on {args.scope} — retention will not prune "
                  "it until cleared")
        elif args.hold_command == "clear":
            n = runstore.clear_hold(args.scope)
            print(f"legal hold cleared on {args.scope}" if n
                  else f"no hold on {args.scope}")
        else:
            holds = runstore.holds()
            for h in holds:
                print(f"{h['scope']:30s} {h.get('reason') or ''}")
            print(f"\n{len(holds)} active hold(s)", file=sys.stderr)
        return 0

    if args.command == "maintenance":
        import time

        from .runstore import default_runstore
        runstore = default_runstore(config)
        now = time.time()
        if args.off:
            runstore.clear_maintenance(args.scope)
            print(f"maintenance cleared for {args.scope}")
        else:
            until = now + args.hours * 3600 if args.hours else None
            runstore.set_maintenance(
                args.scope, until, now, reason=args.reason,
                set_by=userEmail_or_none(),
            )
            when = (
                f"until {time.ctime(until)}" if until else "until cleared"
            )
            print(f"maintenance set for {args.scope} ({when})")
        return 0

    if args.command == "report":
        from .runstore import default_runstore
        store = GitStore(config.data_dir)
        store.ensure_repo()
        runstore = default_runstore(config)
        out = args.out or f"compliance-report.{args.format}"
        if args.format == "html":
            from .reports import compliance_report
            Path(out).write_text(compliance_report(
                config, store, runstore, period_days=args.days))
        else:
            from .reportfmt import render
            data, _ct, _ext = render(args.format, config, store, runstore)
            Path(out).write_bytes(data)
        print(f"compliance report written to {out}")
        if args.sign:
            from .signing import SigningError, sign_file
            try:
                sig = sign_file(out, args.key_file)
            except SigningError as exc:
                print(f"signing error: {exc}", file=sys.stderr)
                return 2
            print(f"signed: {sig} (+ {out}.pubkey)")
        return 0

    if args.command == "report-verify":
        from .signing import SigningError, verify_file
        sig = args.sig or args.report + ".sig"
        pubkey = args.pubkey or args.report + ".pubkey"
        try:
            ok = verify_file(args.report, sig, pubkey)
        except (SigningError, OSError) as exc:
            print(f"verify error: {exc}", file=sys.stderr)
            return 2
        print("VALID signature" if ok else "INVALID signature")
        return 0 if ok else 1

    if args.command == "export":
        import tarfile
        base = Path(config.data_dir).parent
        members = [
            (Path(config.data_dir), "data"),
            (base / "blobs", "blobs"),
            (base / "runstore.db", "runstore.db"),
        ]
        with tarfile.open(args.out, "w:gz") as tar:
            for path, arcname in members:
                if path.exists():
                    tar.add(path, arcname=arcname)
        size = Path(args.out).stat().st_size
        print(f"export archive written to {args.out} "
              f"({size / 1048576:.1f} MiB) — copy to offsite/offline media")
        return 0

    if args.command == "strategy":
        from .runner import default_blobstore
        from .runstore import default_runstore
        from .strategy import evaluate
        store = GitStore(config.data_dir)
        store.ensure_repo()
        result = evaluate(config, store, default_runstore(config),
                          blobstore=default_blobstore(config))
        for check in result.checks:
            mark = "OK  " if check.ok else "MISS"
            print(f"{mark} {check.label:42s} {check.detail}")
            if not check.ok and check.remediation:
                print(f"       -> {check.remediation}")
        print(f"\n3-2-1:      {'SATISFIED' if result.satisfies_321 else 'NOT met'}")
        print(f"3-2-1-1-0:  {'SATISFIED' if result.satisfies_32110 else 'NOT met'}")
        return 0 if result.satisfies_321 else 1

    if args.command == "netbox":
        from .netbox import NetBoxClient, NetBoxError, import_proposal, reconcile_netbox
        nb = config.netbox or {}
        url = args.url or nb.get("url")
        token = args.token or nb.get("token")
        if not url or not token:
            print("netbox: --url and --token (or config netbox.*) required",
                  file=sys.stderr)
            return 2
        client = NetBoxClient(url, token,
                              verify_tls=nb.get("verify_tls", True))
        try:
            if args.action == "reconcile":
                rec = reconcile_netbox(config, client, nb.get("filters"))
                print(f"in NetBox, NOT backed up: {len(rec.not_backed_up)}")
                for d in rec.not_backed_up:
                    print(f"  {d.name:24s} {d.address or '-':16s} "
                          f"role={d.role} platform={d.platform}")
                print(f"backed up, NOT in NetBox: {len(rec.not_in_netbox)}")
                for name in rec.not_in_netbox:
                    print(f"  {name}")
                print(f"matched: {len(rec.matched)}")
            else:  # import
                devices = client.devices(nb.get("filters"))
                Path(args.out).write_text(
                    import_proposal(devices, args.site, args.zone))
                print(f"{len(devices)} device(s) -> {args.out} (review "
                      "before merging)")
        except NetBoxError as exc:
            print(f"netbox error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "dr-plan":
        from .reports import dr_runbook
        from .runstore import default_runstore
        store = GitStore(config.data_dir)
        store.ensure_repo()
        try:
            content = dr_runbook(
                config, store, default_runstore(config), args.site
            )
        except KeyError as exc:
            print(f"dr-plan error: {exc}", file=sys.stderr)
            return 2
        out = args.out or f"dr-runbook-{args.site}.html"
        Path(out).write_text(content)
        print(f"DR runbook written to {out}")
        return 0

    if args.command == "net-restore":
        from .netrestore import NetRestoreError, restore_network_config
        [device] = config.find_devices([args.device])
        store = GitStore(config.data_dir)
        secrets = load_backend(
            config.secrets, base_dir=Path(config.data_dir).parent
        ) if config.secrets else None
        secret = (
            secrets.get(device.credentials)
            if secrets and device.credentials else None
        )
        try:
            result = restore_network_config(
                store, device, secret, commit=args.commit, apply=args.apply,
            )
        except NetRestoreError as exc:
            print(f"net-restore error: {exc}", file=sys.stderr)
            return 1
        if not result.applied:
            print(f"DRY RUN for {result.device} — candidate config "
                  f"({len(result.candidate_config.splitlines())} lines) "
                  "NOT pushed. Re-run with --apply.")
            print(f"pre-change running config saved "
                  f"({len(result.pre_config.splitlines())} lines)")
        else:
            verdict = "VERIFIED" if result.verified else "MISMATCH after push"
            print(f"applied to {result.device}: {verdict}")
            return 0 if result.verified else 1
        return 0

    if args.command == "rehearse":
        import tempfile
        import time

        from .restore import RestoreError, export_bundle
        from .runner import default_blobstore
        from .runstore import default_runstore
        [device] = config.find_devices([args.device])
        store = GitStore(config.data_dir)
        runstore = default_runstore(config)
        commit = store.last_commit_hash(device)
        result = args.result
        if result is None:
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    _, mismatches = export_bundle(
                        store, device, Path(tmp) / "b", commit=commit,
                        blobstore=default_blobstore(config),
                    )
                result = "pass" if not mismatches else "fail"
            except RestoreError as exc:
                print(f"rehearse: {exc}", file=sys.stderr)
                result = "fail"
        runstore.record_rehearsal(
            device.qualified_name, time.time(), commit, result,
            tested_by=args.by, notes=args.notes,
        )
        print(f"recorded restore rehearsal for {device.qualified_name}: "
              f"{result}")
        return 0 if result == "pass" else 1

    return 0


def userEmail_or_none() -> str | None:
    import os
    return os.environ.get("USER") or None


if __name__ == "__main__":
    sys.exit(main())
