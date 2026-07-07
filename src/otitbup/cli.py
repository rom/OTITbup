"""otitbup command-line interface.

    otitbup -c otitbup.yml validate
    otitbup -c otitbup.yml list
    otitbup -c otitbup.yml backup [--all | DEVICE ...] [--force]
    otitbup -c otitbup.yml diff DEVICE
    otitbup -c otitbup.yml log [DEVICE]
    otitbup -c otitbup.yml daemon
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

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
