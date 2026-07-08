"""Setup ergonomics: `otitbup init` scaffolds a starter config.

Writes a minimal, commented otitbup.yml and a matching secrets.yml so a
first run is edit-two-files rather than read-the-docs. Refuses to
overwrite existing files.
"""
from __future__ import annotations

from pathlib import Path

_CONFIG = """\
# otitbup configuration — the inventory is the source of truth; keep it in
# git. Validate edits with `otitbup validate`; full reference in
# docs/CONFIGURATION.md.
data_dir: ./data

retention:
  keep_versions: 30
  keep_days: 365
  large_file_threshold: 1048576   # artifacts >= 1 MiB -> blob store

secrets:
  backend: plainfile
  path: secrets.yml

webui:
  host: 127.0.0.1
  port: 8080
  # otitbup passwd    # -> webui.auth (single user)
  # otitbup certgen   # -> webui.tls (HTTPS)

alerts:
  stale_days: 7
  min_interval: 3600    # suppress repeat of the same alert within N seconds

sites:
  - name: site-1
    zones:
      - name: network
        devices:
          - name: core-sw-01
            driver: cisco_ios       # `otitbup drivers` lists all drivers
            address: 10.0.0.1
            schedule: 1h
            credentials: core-sw-01
"""

_SECRETS = """\
# Device credentials, keyed by the `credentials:` name on each device.
# Protect with OS permissions, or use `otitbup secrets encrypt`.
core-sw-01:
  username: backup
  password: change-me
  # enable_secret: change-me-too
"""


def init_project(directory: str | Path = ".") -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    targets = {
        directory / "otitbup.yml": _CONFIG,
        directory / "secrets.yml": _SECRETS,
    }
    existing = [p for p in targets if p.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite: " + ", ".join(str(p) for p in existing)
        )
    for path, content in targets.items():
        path.write_text(content)
        if path.name == "secrets.yml":
            path.chmod(0o600)
        written.append(path)
    return written
