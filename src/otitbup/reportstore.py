"""Archive of generated compliance reports.

Reports are written into a dedicated `reports/` subdirectory next to the
data directory, each under a timestamped name
(`compliance-YYYYMMDD-HHMMSS.<ext>`) so every generated report is retained
as a versioned archive rather than overwriting the last one. Signatures
(`.sig` / `.pubkey`) sit beside their report. The web UI lists and serves
them from here.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

_NAME_RE = re.compile(
    r"^compliance-\d{8}-\d{6}\.(html|csv|pdf|docx)(\.sig|\.pubkey)?$")

_CONTENT_TYPE = {
    "html": "text/html; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
    "docx": ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document"),
    "sig": "application/octet-stream",
    "pubkey": "application/octet-stream",
}


@dataclass
class ReportFile:
    name: str
    fmt: str
    size: int
    mtime: float
    signed: bool


def reports_dir(config) -> Path:
    return Path(config.data_dir).parent / "reports"


def archive_name(fmt: str, when: datetime.datetime | None = None) -> str:
    when = when or datetime.datetime.now(datetime.UTC)
    return f"compliance-{when.strftime('%Y%m%d-%H%M%S')}.{fmt}"


def store_report(config, fmt: str, data: bytes,
                 when: datetime.datetime | None = None) -> Path:
    """Write a report into the archive and return its path."""
    directory = reports_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / archive_name(fmt, when)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def list_reports(config) -> list[ReportFile]:
    """Archived reports (not signatures), newest first."""
    directory = reports_dir(config)
    if not directory.is_dir():
        return []
    out: list[ReportFile] = []
    for path in directory.iterdir():
        if not path.is_file() or not _NAME_RE.match(path.name):
            continue
        if path.name.endswith((".sig", ".pubkey")):
            continue
        stat = path.stat()
        out.append(ReportFile(
            name=path.name,
            fmt=path.suffix.lstrip("."),
            size=stat.st_size,
            mtime=stat.st_mtime,
            signed=(directory / (path.name + ".sig")).exists(),
        ))
    out.sort(key=lambda r: r.mtime, reverse=True)
    return out


def report_path(config, name: str) -> Path | None:
    """Resolve a report file name to a path inside the archive, or None if
    the name is unsafe or the file is missing (path-traversal guarded)."""
    if not _NAME_RE.match(name):
        return None
    directory = reports_dir(config).resolve()
    path = (directory / name).resolve()
    if path.parent != directory or not path.is_file():
        return None
    return path


def content_type(name: str) -> str:
    if name.endswith(".sig"):
        return _CONTENT_TYPE["sig"]
    if name.endswith(".pubkey"):
        return _CONTENT_TYPE["pubkey"]
    ext = name.rsplit(".", 1)[-1]
    return _CONTENT_TYPE.get(ext, "application/octet-stream")
