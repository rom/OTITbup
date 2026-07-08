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

import gzip
import hashlib
import os
from pathlib import Path

_POINTER_VERSION = b"version otitbup-blob-v1"

# Prefix marking a gzip-compressed blob payload. Legacy blobs have no prefix
# and are returned verbatim, so the format stays backward compatible.
_COMPRESS_MAGIC = b"\x00OTBZ1\n"


def _frame(data: bytes, compress: bool) -> bytes:
    """Wrap plaintext for storage: gzip + magic prefix when compressing."""
    if compress:
        return _COMPRESS_MAGIC + gzip.compress(data)
    return data


def _unframe(payload: bytes) -> bytes:
    """Reverse _frame: decompress if the payload carries the magic prefix."""
    if payload.startswith(_COMPRESS_MAGIC):
        return gzip.decompress(payload[len(_COMPRESS_MAGIC):])
    return payload


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
    def __init__(self, root: str | Path, key: str | bytes | None = None,
                 compress: bool = False):
        self.root = Path(root)
        # Optional encryption at rest: blobs are content-addressed by the
        # PLAINTEXT sha256 (so dedup and manifest hashes are unchanged) but
        # written to disk Fernet-encrypted. Protects large artifacts if the
        # appliance disk/snapshot leaks. Optional gzip compression is applied
        # to new blobs before encryption. Both are transparent to callers and
        # backward compatible with existing (raw / encrypted-only) blobs.
        self.compress = compress
        self._fernet = _make_fernet(key)

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def put(self, data: bytes) -> str:
        sha256 = hashlib.sha256(data).hexdigest()   # over plaintext
        path = self._path(sha256)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = _frame(data, self.compress)
            stored = self._fernet.encrypt(payload) if self._fernet else payload
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(stored)
            os.replace(tmp, path)
        return sha256

    def get(self, sha256: str) -> bytes:
        path = self._path(sha256)
        if not path.exists():
            raise KeyError(sha256)
        raw = path.read_bytes()
        payload = self._fernet.decrypt(raw) if self._fernet else raw
        return _unframe(payload)

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


def _make_fernet(key: str | bytes | None):
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key if isinstance(key, bytes) else key.encode())


def rotate_key(root: str | Path, old_key, new_key) -> tuple[int, int]:
    """Re-encrypt every blob under `root` from `old_key` to `new_key`.

    Keys may be None to mean "not encrypted": None->key encrypts a
    plaintext store, key->None decrypts it, key->key' rotates. Blobs stay
    content-addressed by their plaintext sha256, so names never change; only
    the on-disk encryption layer is rewritten (compression framing inside is
    left untouched). Each file is rewritten atomically. Returns
    (rotated, skipped)."""
    root = Path(root)
    old_fernet = _make_fernet(old_key)
    new_fernet = _make_fernet(new_key)
    rotated = skipped = 0
    for path in sorted(root.glob("??/*")):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        raw = path.read_bytes()
        try:
            payload = old_fernet.decrypt(raw) if old_fernet else raw
        except Exception as exc:
            raise BlobStoreError(
                f"cannot decrypt {path.name} with the old key: {exc}"
            ) from exc
        # Integrity guard: the plaintext must still hash to the blob's name.
        if hashlib.sha256(_unframe(payload)).hexdigest() != path.name:
            skipped += 1
            continue
        stored = new_fernet.encrypt(payload) if new_fernet else payload
        tmp = path.with_suffix(".rotate-tmp")
        tmp.write_bytes(stored)
        os.replace(tmp, path)
        rotated += 1
    return rotated, skipped


class BlobStoreError(Exception):
    pass
