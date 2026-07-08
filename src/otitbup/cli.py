"""otitbup command-line interface.

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
    return EventBus(config.events, runstore=default_runstore(config))


def _build_runner(config, force: bool = False, events=None) -> Runner:
    store = GitStore(config.data_dir)
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

    p_status = sub.add_parser(
        "status", help="show per-device backup health from the run store"
    )

    p_policy = sub.add_parser(
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
        "report", help="write an HTML compliance report"
    )
    p_report.add_argument("--out", default="compliance-report.html")
    p_report.add_argument("--days", type=int, default=30)

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

    if args.command == "drivers":
        from .drivers import driver_descriptions
        for name, description in driver_descriptions().items():
            print(f"{name:20s} {description}")
        return 0

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
        runner = _build_runner(config, force=args.force, events=events)
        results = runner.backup_devices(devices)
        for result in results:
            status = "OK " if result.ok else "FAIL"
            print(f"{status} {result.device:40s} {result.message}")
        events.emit(PROCESS_STOP, "otitbup backup finished", detail="backup")
        return 0 if all(r.ok for r in results) else 1

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
        from .baseline import all_drift, drift_diff
        from .runstore import default_runstore
        import time
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
        from .reconcile import reconcile
        from .discovery import proposal_yaml
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
        for finding in findings:
            ports = ", ".join(str(p) for p in finding.open_ports)
            print(f"  {finding.address:16s} ports {ports:20s} -> {finding.driver}")
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
        store = GitStore(config.data_dir)
        store.ensure_repo()
        blobstore = default_blobstore(config)
        prune_plan = plan(config, store, blobstore)
        for dplan in prune_plan.devices:
            sources = ", ".join(
                f"{key}={dplan.sources[key]}"
                for key in sorted(dplan.policy)
                if dplan.sources.get(key) not in (None, "default")
            )
            print(
                f"{dplan.device:40s} {describe_policy(dplan.policy):45s} "
                f"backups kept {dplan.kept_backups}/{dplan.backups}"
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

    if args.command == "status":
        from .runstore import default_runstore
        import time
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

    if args.command == "maintenance":
        from .runstore import default_runstore
        import time
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
        from .reports import compliance_report
        from .runstore import default_runstore
        store = GitStore(config.data_dir)
        store.ensure_repo()
        content = compliance_report(
            config, store, default_runstore(config), period_days=args.days
        )
        Path(args.out).write_text(content)
        print(f"compliance report written to {args.out}")
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
