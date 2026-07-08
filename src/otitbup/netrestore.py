"""Automated restore for network equipment (the safe first write path).

PLC writes stay guided (docs/REQUIREMENTS.md section 3) — the blast radius
is too large. Network gear is different: pushing a known-good config to a
switch or router is routine and reversible. This restores a stored config
to an SSH-managed device via netmiko, with hard interlocks:

- Only SSH-profile / generic_ssh drivers are eligible; anything else is
  refused. Even a driver alias that has no push command is refused.
- Dry run by DEFAULT: it prints the config that WOULD be pushed and the
  device's current running config for comparison, and writes nothing.
- The pre-change running config is captured first and returned, so the
  operator holds a rollback artifact before any write.
- After `--apply`, the running config is re-read and diffed so the result
  is verified, not assumed.

This is opt-in per invocation and never runs from the scheduler.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .gitstore import GitStore
from .models import Device

# Driver -> (netmiko device_type, artifact whose content is the config,
# the config-file-mode load verb). Only entries here are restorable.
_RESTORABLE = {
    "cisco_ios": ("cisco_ios", "show_running_config.txt"),
    "cisco_asa": ("cisco_asa", "show_running_config.txt"),
    "generic_ssh": (None, "show_running_config.txt"),
}


class NetRestoreError(Exception):
    pass


@dataclass
class NetRestoreResult:
    device: str
    applied: bool
    pre_config: str
    candidate_config: str
    post_config: str | None = None
    verified: bool | None = None
    message: str = ""


def _device_type(device: Device, fallback: str | None) -> str:
    return device.options.get("device_type") or fallback or "cisco_ios"


def restore_network_config(
    store: GitStore,
    device: Device,
    secret: dict[str, Any] | None,
    commit: str | None = None,
    artifact: str | None = None,
    apply: bool = False,
) -> NetRestoreResult:
    if device.driver not in _RESTORABLE:
        raise NetRestoreError(
            f"{device.qualified_name}: driver {device.driver!r} is not "
            "eligible for automated restore (network SSH drivers only; "
            "use `otitbup restore` for a guided bundle)"
        )
    fallback_type, default_artifact = _RESTORABLE[device.driver]
    artifact = artifact or default_artifact

    commit = commit or store.last_commit_hash(device)
    if not commit:
        raise NetRestoreError(f"{device.qualified_name}: no backups to restore")
    try:
        candidate = store.read_file_at(
            commit, f"{device.path}/{artifact}"
        ).decode()
    except Exception as exc:
        raise NetRestoreError(
            f"{device.qualified_name}: cannot read {artifact} at "
            f"{commit[:8]}: {exc}"
        ) from exc

    try:
        from netmiko import ConnectHandler
    except ImportError as exc:
        raise NetRestoreError(
            "automated restore requires netmiko (pip install otitbup[ssh])"
        ) from exc
    if not device.address or not secret:
        raise NetRestoreError(
            f"{device.qualified_name}: address and credentials are required"
        )

    params = {
        "device_type": _device_type(device, fallback_type),
        "host": device.address,
        "port": int(device.options.get("port", 22)),
        "username": secret.get("username"),
        "password": secret.get("password"),
    }
    if secret.get("enable_secret"):
        params["secret"] = secret["enable_secret"]

    try:
        with ConnectHandler(**params) as conn:
            if secret.get("enable_secret"):
                conn.enable()
            pre_config = conn.send_command("show running-config") or ""
            if not apply:
                return NetRestoreResult(
                    device=device.qualified_name,
                    applied=False,
                    pre_config=pre_config,
                    candidate_config=candidate,
                    message="dry run — re-run with --apply to push",
                )
            lines = [
                ln for ln in candidate.splitlines()
                # Skip header/banner noise the vendor won't accept as config.
                if ln.strip() and not ln.startswith(("!", "Building",
                                                     "Current configuration"))
            ]
            conn.send_config_set(lines)
            try:
                conn.save_config()
            except Exception:
                pass  # not all platforms implement save_config
            post_config = conn.send_command("show running-config") or ""
    except NetRestoreError:
        raise
    except Exception as exc:
        raise NetRestoreError(
            f"{device.qualified_name}: restore failed: {exc}"
        ) from exc

    return NetRestoreResult(
        device=device.qualified_name,
        applied=True,
        pre_config=pre_config,
        candidate_config=candidate,
        post_config=post_config,
        verified=_normalize(post_config) == _normalize(candidate),
        message="applied",
    )


def is_restorable(driver: str) -> bool:
    return driver in _RESTORABLE


def _normalize(config: str) -> list[str]:
    return [
        ln.rstrip() for ln in config.splitlines()
        if ln.strip() and not ln.startswith(("!", "Building",
                                             "Current configuration"))
    ]
