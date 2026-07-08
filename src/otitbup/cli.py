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


def _build_runner(config, force: bool = False) -> Runner:
    store = GitStore(config.data_dir)
    secrets = load_backend(
        config.secrets, base_dir=Path(config.data_dir).parent
    ) if config.secrets else None
    return Runner(
        config,
        store,
        secrets=secrets,
        alerts=AlertManager(config.alerts),
        force=force,
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

    p_backup = sub.add_parser("backup", help="run a backup now")
    p_backup.add_argument("devices", nargs="*", help="device names (default: all)")
    p_backup.add_argument(
        "--force", action="store_true",
        help="ignore maintenance windows",
    )

    p_diff = sub.add_parser("diff", help="show a device's latest change")
    p_diff.add_argument("device")

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
        devices = (
            config.find_devices(args.devices)
            if args.devices else config.all_devices()
        )
        runner = _build_runner(config, force=args.force)
        results = runner.backup_devices(devices)
        for result in results:
            status = "OK " if result.ok else "FAIL"
            print(f"{status} {result.device:40s} {result.message}")
        return 0 if all(r.ok for r in results) else 1

    if args.command == "diff":
        [device] = config.find_devices([args.device])
        print(GitStore(config.data_dir).last_diff(device) or "no history")
        return 0

    if args.command == "log":
        store = GitStore(config.data_dir)
        device = None
        if args.device:
            [device] = config.find_devices([args.device])
        print(store.history(device, limit=args.limit) or "no history")
        return 0

    if args.command == "daemon":
        runner = _build_runner(config)
        daemon = Daemon(config, runner)
        if args.once:
            count = daemon.run_once()
            print(f"backed up {count} device(s)")
            return 0
        try:
            daemon.run_forever()
        except KeyboardInterrupt:
            return 0

    if args.command == "serve":
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
        try:
            serve(
                config, store,
                host=args.host or config.webui.get("host", "127.0.0.1"),
                port=args.port or int(config.webui.get("port", 8080)),
                auth=config.webui.get("auth"),
                tls=tls,
            )
        except KeyboardInterrupt:
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

    if args.command == "restore":
        from .restore import RestoreError, export_bundle
        [device] = config.find_devices([args.device])
        store = GitStore(config.data_dir)
        try:
            commit = args.commit or store.last_commit_hash(device)
            if not commit:
                print(f"no backups for {device.qualified_name}", file=sys.stderr)
                return 1
            out = args.out or f"restore-{device.name}-{commit[:8]}"
            commit, mismatches = export_bundle(
                store, device, out, commit=commit
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
