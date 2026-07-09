"""Config policy checks — turn the backup archive into a compliance scan.

Rules run against a device's latest captured text artifacts and flag
insecure or non-conformant configuration (telnet enabled, SNMP community
"public", weak SSH, no AAA, ...). This is best-effort pattern matching over
heterogeneous vendor output, not a parser — rules are conservative and
each states what it looked for.

Built-in rules apply by artifact-content match, so they work across
vendors. Additional rules can be declared in config:

    policy:
      rules:
        - id: no-http-server
          description: HTTP server (unencrypted) enabled
          severity: medium
          match: "^ip http server$"      # regex; presence = finding
        - id: require-ntp
          description: no NTP server configured
          severity: low
          absent: "ntp server"           # regex; absence = finding
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from .gitstore import GitStore
from .models import AppConfig, Device

log = logging.getLogger("otitbup.policy")

_SEVERITIES = ("low", "medium", "high", "critical")


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern:
    """Compile a rule regex once, in MULTILINE mode so that ^/$ anchor to
    each config line (matching how operators write rules, e.g.
    `^ip http server$`). Built-in rules carry their own inline flags; the
    extra re.M is harmless for them."""
    return re.compile(pattern, re.MULTILINE)


@dataclass
class Rule:
    id: str
    description: str
    severity: str = "medium"
    match: str | None = None      # finding if this regex is present
    absent: str | None = None     # finding if this regex is absent


@dataclass
class Finding:
    device: str
    rule_id: str
    severity: str
    description: str
    artifact: str


# Vendor-neutral defaults. `match` = insecure line present; `absent` =
# expected hardening line missing.
BUILTIN_RULES = [
    Rule("no-telnet", "Telnet server/transport enabled", "high",
         match=r"(?im)^\s*(transport input.*telnet|feature telnet|"
               r"ip telnet server enable)"),
    Rule("no-snmp-public", "SNMP community 'public' or 'private'", "high",
         match=r"(?im)^\s*snmp-server community\s+(public|private)\b"),
    Rule("no-snmpv1v2", "SNMP v1/v2c community configured (prefer v3)",
         "medium", match=r"(?im)^\s*snmp-server community\s+\S+"),
    Rule("no-http-server", "Unencrypted HTTP admin server enabled", "medium",
         match=r"(?im)^\s*ip http server\s*$"),
    Rule("no-default-cisco-pw", "Default/weak enable password 'cisco'",
         "critical", match=r"(?im)(enable password|username \S+ password)\s+"
                            r"(0\s+)?cisco\b"),
    Rule("no-plaintext-enable", "Enable password stored unencrypted",
         "high", match=r"(?im)^\s*enable password\s+(?!5\b|8\b|9\b)"),
    Rule("no-ftp-server", "FTP server (cleartext file transfer) enabled",
         "high", match=r"(?im)^\s*(ftp-server enable|feature ftp-server|"
                       r"ftp server enable)"),
    Rule("no-tftp-server", "TFTP server (unauthenticated) enabled", "high",
         match=r"(?im)^\s*tftp-server\s+\S+"),
    Rule("no-ssh-v1", "SSH protocol version 1 enabled", "high",
         match=r"(?im)^\s*ip ssh version 1\b"),
    Rule("no-snmpv3-des", "SNMPv3 user with weak DES/3DES privacy", "medium",
         match=r"(?im)^\s*snmp-server user\s+\S+.*\bpriv\s+(3des|des\d*)\b"),
    Rule("no-weak-user-password",
         "User password stored plaintext or reversibly (type 0/7)", "high",
         match=r"(?im)^\s*username\s+\S+\s+password\s+"
               r"(?!5\s|8\s|9\s)(?:(?:0|7)\s+)?\S+"),
    Rule("no-default-admin-password",
         "Default admin/root account with a well-known password", "critical",
         match=r"(?im)^\s*username\s+(admin|root)\s+(password|secret)\s+"
               r"(0\s+)?(admin|root|password|default|1234?5?6?)\s*$"),
    Rule("no-permit-any-any", "ACL permits ip any any (wide open)", "medium",
         match=r"(?im)^\s*(\d+\s+)?(access-list\s+\d+\s+)?"
               r"permit\s+ip\s+any\s+any\b"),
    Rule("no-vty-transport-all",
         "VTY lines accept all transports (incl. telnet)", "high",
         match=r"(?im)^\s*transport input\s+all\b"),
    Rule("weak-enable-md5", "Enable secret uses weak MD5 hashing (type 5)",
         "low", match=r"(?im)^\s*enable secret 5\s"),
    Rule("no-ip-source-route", "IP source routing enabled", "low",
         match=r"(?im)^\s*ip source-route\b"),
]


def load_rules(config: AppConfig) -> list[Rule]:
    rules = list(BUILTIN_RULES)
    for raw in (config.policy.get("rules") or []):
        if "id" not in raw or not (raw.get("match") or raw.get("absent")):
            continue
        # Validate the user regex up front and skip a broken one with a
        # warning, rather than letting re.error escape from check_device and
        # 500 the policy report / web page.
        bad = False
        for pattern in (raw.get("match"), raw.get("absent")):
            if pattern:
                try:
                    _compiled(str(pattern))
                except re.error as exc:
                    log.warning("ignoring policy rule %s: invalid regex %r: %s",
                                raw["id"], pattern, exc)
                    bad = True
        if bad:
            continue
        rules.append(Rule(
            id=str(raw["id"]),
            description=str(raw.get("description", raw["id"])),
            severity=str(raw.get("severity", "medium")),
            match=raw.get("match"),
            absent=raw.get("absent"),
        ))
    return rules


def _text_artifacts(store: GitStore, device: Device, commit: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for repo_path in store.list_files_at(commit, device.path):
        name = repo_path[len(device.path) + 1:]
        if name == "manifest.yml":
            continue
        try:
            data = store.read_file_at(commit, repo_path)
        except Exception:
            continue
        if b"\x00" in data:
            continue
        try:
            out[name] = data.decode()
        except UnicodeDecodeError:
            continue
    return out


def check_device(
    config: AppConfig, store: GitStore, device: Device,
    rules: list[Rule] | None = None,
) -> list[Finding]:
    rules = rules if rules is not None else load_rules(config)
    disabled = set(config.policy.get("disable") or [])
    commit = store.last_commit_hash(device)
    if not commit:
        return []
    artifacts = _text_artifacts(store, device, commit)
    if not artifacts:
        return []
    combined = "\n".join(artifacts.values())
    findings: list[Finding] = []
    for rule in rules:
        if rule.id in disabled:
            continue
        if rule.match:
            for name, text in artifacts.items():
                if _compiled(rule.match).search(text):
                    findings.append(Finding(
                        device.qualified_name, rule.id, rule.severity,
                        rule.description, name,
                    ))
                    break
        elif rule.absent and not _compiled(rule.absent).search(combined):
            findings.append(Finding(
                device.qualified_name, rule.id, rule.severity,
                rule.description, "(config)",
            ))
    return findings


def check_all(
    config: AppConfig, store: GitStore
) -> dict[str, list[Finding]]:
    rules = load_rules(config)
    result: dict[str, list[Finding]] = {}
    for device in config.all_devices():
        findings = check_device(config, store, device, rules)
        if findings:
            result[device.qualified_name] = findings
    return result


def severity_rank(severity: str) -> int:
    try:
        return _SEVERITIES.index(severity)
    except ValueError:
        return 0
