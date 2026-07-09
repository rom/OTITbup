"""Config-editor form schema and per-setting context help for the web UI.

Pure data (plus the tiny _form_id helper) describing the admin settings forms
and the selectable theme/size vocabularies. Extracted from webui.py so the
UI module stays focused on rendering and request handling.
"""
from __future__ import annotations

# Selectable colour themes for the web UI (applied via <html data-theme>).
_THEMES = ("auto", "light", "dark", "high-contrast", "solarized", "nord",
           "dracula", "gruvbox", "wcag", "sky", "desert", "autumn", "spring")

# Text-size preference (applied via <html data-size>).
_SIZES = ("small", "medium", "large", "x-large")

# Admin-editable global config. Each field is
# (dotted-path, label, type, example) where type is
# str | int | float | bool | csv | "choice:a|b|c", and example is the
# placeholder / default shown in an empty field (or the default choice).
# `id` disambiguates two forms that write the same config section.
_SETTINGS_FORMS = [
    {"section": "offsite", "title": "Offsite copy (external server / cloud)",
     "fields": [
         ("transport", "Transport", "choice:file|sftp|s3", "s3"),
         ("interval_days", "Auto-push every N days (0=off)", "float", "1"),
         ("key_file", "Encryption key file", "str", "/etc/otitbup/offsite.key"),
         ("dir", "file: directory / mount", "str", "/mnt/offsite/otitbup"),
         ("host", "sftp: host", "str", "backup.example.com"),
         ("port", "sftp: port", "int", "22"),
         ("username", "sftp: username", "str", "otitbup"),
         ("ssh_key_file", "sftp: SSH key file", "str",
          "/etc/otitbup/id_ed25519"),
         ("path", "sftp: remote path", "str", "/srv/otitbup"),
         ("bucket", "s3: bucket", "str", "ot-backups"),
         ("prefix", "s3: prefix", "str", "otitbup/"),
         ("region", "s3: region", "str", "eu-central-1"),
         ("endpoint", "s3: endpoint", "str",
          "https://s3.eu-central-1.amazonaws.com"),
         ("access_key", "s3: access key", "str", "AKIA..."),
         ("secret_key_file", "s3: secret key file", "str",
          "/etc/otitbup/s3.secret"),
     ]},
    {"id": "webui", "section": "webui", "title": "Web UI",
     "fields": [
         ("host", "Bind host", "str", "127.0.0.1"),
         ("port", "Bind port", "int", "8080"),
         ("theme", "Default colour theme (per-user overrides)",
          "choice:" + "|".join(_THEMES), "auto"),
         ("tls.cert_file", "TLS certificate file", "str", "webui-cert.pem"),
         ("tls.key_file", "TLS key file", "str", "webui-key.pem"),
     ]},
    {"id": "sso", "section": "webui", "title": "Single sign-on (SSO)",
     "fields": [
         ("trusted_header", "Trusted user header (from auth proxy)", "str",
          "X-Forwarded-User"),
         ("trusted_role_header", "Trusted role header", "str",
          "X-Forwarded-Role"),
         ("trusted_default_role", "Default role for SSO users",
          "choice:viewer|operator|admin", "viewer"),
     ]},
    {"section": "ldap", "title": "LDAP / Active Directory login (SSO)",
     "fields": [
         ("url", "LDAP URL", "str", "ldaps://dc.example.com"),
         ("user_dn_template", "User DN template", "str",
          "uid={username},ou=people,dc=example,dc=com"),
         ("group_base", "Group search base", "str",
          "ou=groups,dc=example,dc=com"),
         ("default_role", "Default role", "choice:viewer|operator|admin",
          "viewer"),
     ]},
    {"section": "events", "title": "Events (syslog / SNMP traps)",
     "fields": [
         ("syslog.address", "syslog address", "str", "10.0.0.1"),
         ("syslog.port", "syslog port", "int", "514"),
         ("syslog.protocol", "syslog transport", "choice:udp|tcp|tls", "udp"),
         ("syslog.facility", "syslog facility", "str", "local0"),
         ("syslog.cafile", "syslog TLS CA bundle (tls only)", "str",
          "/etc/ssl/certs/ca-bundle.crt"),
         ("snmp_trap.address", "SNMP trap address", "str", "10.0.0.2"),
         ("snmp_trap.port", "SNMP trap port", "int", "162"),
         ("snmp_trap.version", "SNMP trap version", "choice:v1|v2c|v3",
          "v2c"),
         ("snmp_trap.community", "SNMP community (v1/v2c)", "str", "public"),
         ("snmp_trap.enterprise_oid", "enterprise OID", "str",
          "1.3.6.1.4.1.99999"),
         ("snmp_trap.v3.engine_id", "v3: engine id (hex)", "str",
          "8000270b0102030405"),
         ("snmp_trap.v3.username", "v3: username", "str", "otitbup"),
         ("snmp_trap.v3.auth_protocol", "v3: auth protocol",
          "choice:none|md5|sha1|sha256", "sha256"),
         ("snmp_trap.v3.auth_key_file", "v3: auth passphrase file", "str",
          "/etc/otitbup/snmpv3.auth"),
         ("snmp_trap.v3.priv_protocol", "v3: privacy protocol",
          "choice:none|aes128", "none"),
         ("snmp_trap.v3.priv_key_file", "v3: privacy passphrase file", "str",
          "/etc/otitbup/snmpv3.priv"),
     ]},
    {"section": "logging", "title": "Logging",
     "fields": [
         ("level", "Level", "choice:debug|info|warning|error", "info"),
         ("format", "Format", "choice:text|json", "text"),
         ("file", "Log file (rotates)", "str",
          "/var/log/otitbup/otitbup.log"),
         ("max_bytes", "Max bytes", "int", "10485760"),
         ("backups", "Rotations kept", "int", "5"),
     ]},
    {"section": "netbox", "title": "Integration: NetBox",
     "fields": [("url", "URL", "str", "https://netbox.example.com"),
                ("token", "API token", "str", "0123456789abcdef")]},
    {"section": "tickets", "title": "Integration: ticketing",
     "fields": [
         ("backend", "Backend", "choice:servicenow|jira|rt|generic",
          "servicenow"),
         ("url", "URL", "str", "https://example.service-now.com"),
         ("username", "Username", "str", "svc-otitbup"),
         ("password", "Password / API token", "str", ""),
     ]},
    {"section": "encryption", "title": "Encryption at rest",
     "fields": [
         ("blob_key_file", "Blob-store key file", "str",
          "/etc/otitbup/blob.key"),
         ("compress", "Compress blobs before encrypting", "bool", ""),
     ]},
    {"section": "capture", "title": "Capture-quality guards",
     "fields": [
         ("min_bytes", "Reject captures smaller than (bytes)", "int", "512"),
         ("expect_match", "Required content (regex)", "str", "hostname"),
     ]},
    {"section": "integrity", "title": "Integrity scrubbing",
     "fields": [
         ("interval_days", "Scrub every N days (0=off)", "float", "7"),
         ("all_commits", "Verify whole history", "bool", ""),
         ("fsck", "Run git fsck", "bool", ""),
         ("signatures", "Verify commit signatures", "bool", ""),
     ]},
    {"section": "rehearsal", "title": "Scheduled restore rehearsals",
     "fields": [("interval_days", "Rehearse every N days (0=off)", "float",
                 "30")]},
    {"section": "git", "title": "Git remote & signed history",
     "fields": [
         ("remote", "Push remote", "str",
          "git@gitlab.example.com:ot/backups.git"),
         ("push", "Push after each backup", "bool", ""),
         ("sign.key_file", "Commit signing key (SSH)", "str",
          "/etc/otitbup/commit-signing-key"),
     ]},
    {"section": "secrets", "title": "Secrets backend",
     "fields": [
         ("backend", "Backend",
          "choice:plainfile|encryptedfile|vault|cyberark", "encryptedfile"),
         ("path", "File path", "str", "secrets.yml"),
         ("key_file", "Encryption key file", "str", "otitbup.key"),
         ("url", "Vault/CyberArk URL", "str", "https://vault.example:8200"),
         ("mount", "Vault mount", "str", "secret"),
         ("token_file", "Vault token file", "str", "vault.token"),
     ]},
    {"section": "retention", "title": "Retention (global defaults)",
     "fields": [
         ("keep_versions", "Keep newest N backups", "int", "30"),
         ("keep_days", "Keep backups newer than N days", "int", "365"),
         ("large_file_threshold", "Blob offload threshold (bytes)", "int",
          "1048576"),
         ("lock_days", "Retention lock: keep last N days (WORM)", "int", "90"),
     ]},
    {"section": "alerts", "title": "Alerts",
     "fields": [
         ("stale_days", "Stale after N days (0=off)", "int", "7"),
         ("min_interval", "Rate-limit window (s)", "int", "3600"),
         ("webhooks", "Webhook URLs (comma-separated)", "csv",
          "https://chat.example.com/hook"),
         ("email.smtp_host", "SMTP host", "str", "mail.example.com"),
         ("email.from", "From address", "str", "otitbup@example.com"),
         ("email.to", "Recipients (comma-separated)", "csv",
          "ot-team@example.com"),
     ]},
    {"section": "retry", "title": "Retry on transient failures",
     "fields": [
         ("attempts", "Attempts per device (1=no retry)", "int", "3"),
         ("backoff", "Backoff seconds (doubles each retry)", "float", "2.0"),
     ]},
    {"section": "hooks", "title": "Pre/post hooks (global)",
     "fields": [
         ("pre", "Pre-backup shell command", "str",
          "/usr/local/bin/notify start $OTITBUP_DEVICE"),
         ("post", "Post-backup shell command", "str",
          "/usr/local/bin/notify done $OTITBUP_DEVICE $OTITBUP_OK"),
     ]},
    {"section": "anomaly", "title": "Anomaly detection",
     "fields": [
         ("enabled", "Enabled", "bool", ""),
         ("sigma", "Duration z-score threshold", "float", "3.0"),
         ("duration_floor", "Ignore runs faster than (s)", "float", "5.0"),
         ("change_window", "Change-storm window", "int", "5"),
         ("change_recent", "Recent change-rate trigger", "float", "0.8"),
         ("change_baseline", "Max baseline change-rate", "float", "0.2"),
         ("flap_window", "Flapping window", "int", "6"),
         ("flap_transitions", "Flapping transitions", "int", "3"),
         ("trend_window", "Slow-trend window", "int", "5"),
         ("trend_ratio", "Slow-trend multiplier", "float", "2.0"),
         ("size_drop", "Size-drop fraction of median", "float", "0.5"),
     ]},
    {"section": "policy", "title": "Policy (config compliance rules)",
     "fields": [
         ("disable", "Disabled rule ids (comma-separated)", "csv",
          "no-snmpv1v2, no-http-server"),
     ]},
    {"section": "housekeeping", "title": "Git housekeeping",
     "fields": [
         ("gc_interval_days", "git gc every N days (0=off)", "int", "7"),
         ("gc_aggressive", "Aggressive gc", "bool", ""),
     ]},
    {"section": "desired", "title": "Config-as-code (desired state)",
     "fields": [
         ("dir", "Desired-config directory", "str", "./desired"),
         ("strip_trailing_ws", "Ignore trailing whitespace", "bool", ""),
     ]},
    {"section": "reports", "title": "Scheduled compliance reports",
     "fields": [
         ("interval", "Interval (e.g. 7d; empty=off)", "str", "7d"),
         ("period_days", "Report period (days)", "int", "30"),
         ("out", "Output path", "str", "compliance-report.html"),
     ]},
    {"section": "strategy", "title": "3-2-1 strategy",
     "fields": [
         ("offsite", "Git remote is genuinely off-site", "bool", ""),
         ("offline.path", "Offline export path", "str",
          "/mnt/usb/otitbup-export.tar.gz"),
         ("offline.max_age_days", "Offline max age (days)", "int", "7"),
     ]},
    {"section": "federation", "title": "Federation (central roll-up)",
     "fields": [("role", "Role", "str", "central")]},
]


# Context help for the config editor: (form id, dotted field) -> what the
# setting does, expanded by the per-setting "?" button. Fields without an
# entry get a generated pointer to the Configuration reference.
_FIELD_HELP = {
    ("offsite", "transport"): "How the encrypted offsite snapshot leaves the "
        "appliance: a local/mounted directory (file), SFTP to a remote "
        "server, or an S3-compatible object store.",
    ("offsite", "interval_days"): "The daemon pushes a fresh encrypted "
        "snapshot every N days. 0 disables automatic pushes; `otitbup "
        "offsite push` still works manually.",
    ("offsite", "key_file"): "Fernet key used to encrypt snapshots before "
        "they leave the appliance. Generate with `otitbup offsite genkey`. "
        "Keep a copy off-appliance — without it snapshots are unreadable.",
    ("offsite", "dir"): "Target directory for the `file` transport, e.g. a "
        "mounted NAS/USB path.",
    ("offsite", "host"): "SFTP server hostname or IP (sftp transport).",
    ("offsite", "port"): "SFTP server TCP port, usually 22.",
    ("offsite", "username"): "SFTP login user on the remote server.",
    ("offsite", "ssh_key_file"): "Private SSH key used for the SFTP login "
        "(password auth is deliberately unsupported for unattended pushes).",
    ("offsite", "path"): "Remote directory on the SFTP server where "
        "snapshots are stored.",
    ("offsite", "bucket"): "S3 bucket name (s3 transport).",
    ("offsite", "prefix"): "Key prefix inside the bucket, so several "
        "appliances can share one bucket.",
    ("offsite", "region"): "AWS/S3 region of the bucket.",
    ("offsite", "endpoint"): "S3 endpoint URL — set for MinIO/Ceph or "
        "region-specific endpoints.",
    ("offsite", "access_key"): "S3 access key id. The paired secret key "
        "lives in a file (next field), never in this config.",
    ("offsite", "secret_key_file"): "File containing the S3 secret key "
        "(mode 0600). Kept out of the YAML so the config can live in git.",
    ("webui", "host"): "Interface the web UI binds to. Keep 127.0.0.1 "
        "behind a reverse proxy; 0.0.0.0 exposes it on every interface.",
    ("webui", "port"): "TCP port for the web UI.",
    ("webui", "theme"): "Default colour theme for everyone. Each user can "
        "override it for themselves under Administration → Web UI settings.",
    ("webui", "tls.cert_file"): "PEM certificate chain for HTTPS. Create a "
        "self-signed pair with `otitbup certgen`.",
    ("webui", "tls.key_file"): "PEM private key matching the certificate.",
    ("sso", "trusted_header"): "Header carrying the already-authenticated "
        "username from your OIDC/SAML reverse proxy. Only enable when the "
        "proxy strips this header from client requests.",
    ("sso", "trusted_role_header"): "Optional header carrying the user's "
        "role from the auth proxy.",
    ("sso", "trusted_default_role"): "Role given to SSO users when no role "
        "header is present.",
    ("ldap", "url"): "LDAP/AD server URL; ldaps:// for TLS.",
    ("ldap", "user_dn_template"): "Template used to build the bind DN from "
        "the login name; {username} is substituted.",
    ("ldap", "group_base"): "Search base for group lookups used in role "
        "mapping.",
    ("ldap", "default_role"): "Role for LDAP users that match no group "
        "mapping.",
    ("events", "syslog.address"): "Syslog collector (SIEM) address; every "
        "operational event is forwarded there.",
    ("events", "syslog.port"): "Syslog port — 514 for UDP/TCP, commonly "
        "6514 for TLS.",
    ("events", "syslog.protocol"): "udp (fire-and-forget), tcp, or tls "
        "(RFC 5425, verified against the CA bundle below).",
    ("events", "syslog.facility"): "Syslog facility events are tagged "
        "with, e.g. local0.",
    ("events", "syslog.cafile"): "CA bundle used to verify the syslog "
        "server certificate (tls protocol only).",
    ("events", "snmp_trap.address"): "SNMP trap receiver (NMS) address.",
    ("events", "snmp_trap.port"): "Trap receiver port, normally 162.",
    ("events", "snmp_trap.version"): "SNMP trap format: v1 (Trap-PDU), "
        "v2c (SNMPv2-Trap, community-based) or v3 (USM: authenticated and "
        "optionally encrypted — fill in the v3 fields below).",
    ("events", "snmp_trap.community"): "Community string for v1/v2c traps.",
    ("events", "snmp_trap.enterprise_oid"): "OID base for the trap "
        "identity; use your own enterprise arc.",
    ("events", "snmp_trap.v3.engine_id"): "Authoritative engine id (hex) "
        "for v3 traps — must match the user config on the receiver.",
    ("events", "snmp_trap.v3.username"): "USM security name for v3 traps.",
    ("events", "snmp_trap.v3.auth_protocol"): "Authentication hash for v3: "
        "sha1/sha256 (md5 exists for legacy receivers). 'none' sends "
        "noAuthNoPriv.",
    ("events", "snmp_trap.v3.auth_key_file"): "File with the v3 "
        "authentication passphrase (mode 0600, kept out of the YAML).",
    ("events", "snmp_trap.v3.priv_protocol"): "Privacy (encryption) for "
        "v3: aes128 requires the `crypto` extra; 'none' sends authNoPriv.",
    ("events", "snmp_trap.v3.priv_key_file"): "File with the v3 privacy "
        "passphrase.",
    ("logging", "level"): "Verbosity of the process log (not the audit "
        "trail): debug is very chatty, info is the sensible default.",
    ("logging", "format"): "text for humans/journald, json for log "
        "shippers.",
    ("logging", "file"): "Log file path; rotated at max_bytes keeping "
        "`backups` old files. Empty logs to stderr only.",
    ("logging", "max_bytes"): "Rotate the log file when it reaches this "
        "size.",
    ("logging", "backups"): "How many rotated log files to keep.",
    ("netbox", "url"): "NetBox instance used by `otitbup reconcile` to "
        "compare the inventory against your DCIM source of truth.",
    ("netbox", "token"): "NetBox API token (read access to dcim.devices).",
    ("tickets", "backend"): "Ticket system that receives incidents for "
        "backup failures and unexpected changes.",
    ("tickets", "url"): "Base URL of the ticket system.",
    ("tickets", "username"): "Service account used to open tickets.",
    ("tickets", "password"): "Password or API token for the service "
        "account.",
    ("encryption", "blob_key_file"): "Fernet key encrypting the blob store "
        "at rest (large artifacts). Without it blobs are stored plain.",
    ("encryption", "compress"): "Compress blobs before encrypting — saves "
        "space, costs a little CPU.",
    ("capture", "min_bytes"): "Reject captures smaller than this: a "
        "too-small file usually means a login page or error instead of a "
        "real config.",
    ("capture", "expect_match"): "Regex that must appear somewhere in the "
        "captured text; rejects error pages that pass the size check.",
    ("integrity", "interval_days"): "How often the daemon re-hashes stored "
        "artifacts against their manifests (bit-rot scrubbing). 0 = off.",
    ("integrity", "all_commits"): "Scrub the whole history, not just each "
        "device's latest backup — slower, most thorough.",
    ("integrity", "fsck"): "Also run `git fsck` on the repository during "
        "scrubs.",
    ("integrity", "signatures"): "Verify commit signatures during scrubs "
        "(needs git.sign configured).",
    ("rehearsal", "interval_days"): "Reminder cadence for restore "
        "rehearsals — devices with no rehearsal in N days are flagged. "
        "0 = off.",
    ("git", "remote"): "Git remote the backup repo mirrors to after each "
        "backup — your second copy (3-2-1).",
    ("git", "push"): "Push to the remote automatically after every "
        "backup.",
    ("git", "sign.key_file"): "SSH private key used to sign backup "
        "commits, giving cryptographic provenance to the history.",
    ("secrets", "backend"): "Where device credentials live: an encrypted "
        "YAML file (default), plain YAML (labs only), HashiCorp Vault, or "
        "CyberArk.",
    ("secrets", "path"): "Path of the secrets YAML (plainfile/"
        "encryptedfile backends).",
    ("secrets", "key_file"): "Fernet key that decrypts the encrypted "
        "secrets file. Generate with `otitbup secrets genkey`.",
    ("secrets", "url"): "Vault or CyberArk API URL.",
    ("secrets", "mount"): "Vault KV mount containing the secrets.",
    ("secrets", "token_file"): "File containing the Vault token.",
    ("retention", "keep_versions"): "Keep the newest N backups per device; "
        "older ones become prunable. 0 = unlimited.",
    ("retention", "keep_days"): "Keep backups newer than N days. 0 = "
        "unlimited.",
    ("retention", "large_file_threshold"): "Artifacts bigger than this are "
        "offloaded to the deduplicated blob store instead of the git repo.",
    ("retention", "lock_days"): "WORM window: the last N days of backups "
        "can never be pruned, whatever the other rules say.",
    ("alerts", "stale_days"): "Alert when a device has had no successful "
        "backup for N days. 0 = off.",
    ("alerts", "min_interval"): "Rate limit: identical alerts are "
        "suppressed within this window (seconds).",
    ("alerts", "webhooks"): "Chat/incident webhook URLs (Slack/Teams/"
        "generic JSON), comma-separated.",
    ("alerts", "email.smtp_host"): "SMTP relay used for alert email.",
    ("alerts", "email.from"): "From address on alert email.",
    ("alerts", "email.to"): "Alert recipients, comma-separated.",
    ("retry", "attempts"): "Total attempts per device per run; transient "
        "network errors are retried, permanent ones are not. 1 = no retry.",
    ("retry", "backoff"): "Seconds before the first retry; doubles each "
        "further retry.",
    ("hooks", "pre"): "Shell command before each device backup; "
        "$OTITBUP_DEVICE holds the qualified name. Non-zero exit skips "
        "the device.",
    ("hooks", "post"): "Shell command after each backup; $OTITBUP_OK is "
        "1/0 for success/failure.",
    ("anomaly", "enabled"): "Master switch for behavioural anomaly "
        "detection over run history (see the Anomaly page).",
    ("anomaly", "sigma"): "A run is 'slow' when its duration is this many "
        "standard deviations above the device's own mean.",
    ("anomaly", "duration_floor"): "Ignore runs faster than this — "
        "millisecond jitter on quick devices never counts as a spike.",
    ("anomaly", "change_window"): "How many recent runs the change-storm "
        "detector looks at.",
    ("anomaly", "change_recent"): "Change-storm fires when at least this "
        "fraction of the recent window changed…",
    ("anomaly", "change_baseline"): "…and the device's long-run change "
        "rate is at or below this fraction (a normally-quiet device).",
    ("anomaly", "flap_window"): "How many recent runs the flapping "
        "detector looks at.",
    ("anomaly", "flap_transitions"): "Ok/fail transitions inside the "
        "window that count as flapping (an intermittent device/link).",
    ("anomaly", "trend_window"): "Recent-run window for the gradual "
        "slowdown detector.",
    ("anomaly", "trend_ratio"): "Slow-trend fires when the recent mean "
        "duration is this multiple of the older baseline.",
    ("anomaly", "size_drop"): "Size-drop fires when a capture is smaller "
        "than this fraction of the device's median size (likely "
        "truncated).",
    ("policy", "disable"): "Built-in rule ids to skip (see the Anomaly "
        "page for the active rules). Custom rules are managed below.",
    ("housekeeping", "gc_interval_days"): "Run `git gc` on the backup repo "
        "every N days to repack and prune. 0 = never.",
    ("housekeeping", "gc_aggressive"): "Use --aggressive gc: much slower, "
        "slightly smaller repository.",
    ("desired", "dir"): "Directory of intended 'golden' configs; drift "
        "between desired and captured configs is reported.",
    ("desired", "strip_trailing_ws"): "Ignore trailing whitespace when "
        "comparing desired vs captured.",
    ("reports", "interval"): "Generate a compliance report on this "
        "schedule (e.g. 7d). Empty disables scheduled reports.",
    ("reports", "period_days"): "How many days each report covers.",
    ("reports", "out"): "Where the scheduled report is written.",
    ("strategy", "offsite"): "Declare that the git remote is genuinely "
        "off-site (different building/failure domain) — counts toward "
        "3-2-1.",
    ("strategy", "offline.path"): "Path of the offline/air-gapped export "
        "archive (from `otitbup export`).",
    ("strategy", "offline.max_age_days"): "The offline copy counts only "
        "while younger than this.",
    ("federation", "role"): "Set to 'central' on the roll-up appliance "
        "that polls the site collectors listed below.",
}


def _form_id(spec: dict) -> str:
    return spec.get("id", spec["section"])
