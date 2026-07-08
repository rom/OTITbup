"""Content-addressed blob store for large artifacts.

Git keeps every version of every file forever, which is perfect for text
configs and fatal for multi-megabyte PLC project files. Artifacts at or
above the retention `large_file_threshold` are therefore stored here —
content-addressed by sha256, deduplicated across devices and versions —
and a small pointer file goes into git in their place:

    version otitbup-blob-v1
    sha256 <hex>
    size <bytes>

Retention (`otitbup retention`) can then delete expired blobs as plain
files, without rewriting git history: old commits keep their pointer
files, and a pruned blob simply reads as "expired by retention".
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

_POINTER_VERSION = b"version otitbup-blob-v1"


def make_pointer(sha256: str, size: int) -> bytes:
    return b"%s\nsha256 %s\nsize %d\n" % (
        _POINTER_VERSION, sha256.encode(), size,
    )


def parse_pointer(data: bytes) -> tuple[str, int] | None:
    """Return (sha256, size) if `data` is a pointer file, else None."""
    if not data.startswith(_POINTER_VERSION):
        return None
    fields = {}
    for line in data.decode(errors="replace").splitlines()[1:]:
        key, _, value = line.partition(" ")
        fields[key] = value
    try:
        return fields["sha256"], int(fields["size"])
    except (KeyError, ValueError):
        return None


class BlobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def put(self, data: bytes) -> str:
        sha256 = hashlib.sha256(data).hexdigest()
        path = self._path(sha256)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        return sha256

    def get(self, sha256: str) -> bytes:
        path = self._path(sha256)
        if not path.exists():
            raise KeyError(sha256)
        return path.read_bytes()

    def has(self, sha256: str) -> bool:
        return self._path(sha256).exists()

    def delete(self, sha256: str) -> int:
        """Delete a blob; returns the bytes freed (0 if absent)."""
        path = self._path(sha256)
        if not path.exists():
            return 0
        size = path.stat().st_size
        path.unlink()
        try:
            path.parent.rmdir()  # drop the fan-out dir if now empty
        except OSError:
            pass
        return size

    def all_blobs(self) -> dict[str, int]:
        """sha256 -> size for every stored blob."""
        blobs: dict[str, int] = {}
        if not self.root.exists():
            return blobs
        for path in self.root.glob("??/*"):
            if path.is_file() and not path.name.endswith(".tmp"):
                blobs[path.name] = path.stat().st_size
        return blobs

    def total_size(self) -> int:
        return sum(self.all_blobs().values())
