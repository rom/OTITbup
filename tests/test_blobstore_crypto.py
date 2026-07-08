import hashlib

import pytest

pytest.importorskip("cryptography")
from cryptography.fernet import Fernet  # noqa: E402

from otitbup.blobstore import BlobStore, BlobStoreError, rotate_key  # noqa: E402

_DATA = b"PLC PROJECT BLOB " * 500


def test_compression_roundtrip_and_shrinks(tmp_path):
    store = BlobStore(tmp_path / "b", compress=True)
    sha = store.put(_DATA)
    assert sha == hashlib.sha256(_DATA).hexdigest()          # plaintext-addressed
    on_disk = (tmp_path / "b" / sha[:2] / sha).read_bytes()
    assert len(on_disk) < len(_DATA)                          # compressed
    assert store.get(sha) == _DATA


def test_compression_plus_encryption(tmp_path):
    key = Fernet.generate_key()
    store = BlobStore(tmp_path / "b", key=key, compress=True)
    sha = store.put(_DATA)
    on_disk = (tmp_path / "b" / sha[:2] / sha).read_bytes()
    assert b"PLC PROJECT" not in on_disk
    assert store.get(sha) == _DATA


def test_backward_compatible_with_legacy_blobs(tmp_path):
    # A plain, uncompressed, unencrypted blob (the original format) reads back.
    legacy = BlobStore(tmp_path / "b")
    sha = legacy.put(b"legacy raw config")
    assert BlobStore(tmp_path / "b").get(sha) == b"legacy raw config"


def test_rotate_key_changes_encryption_not_content(tmp_path):
    k1, k2 = Fernet.generate_key(), Fernet.generate_key()
    store = BlobStore(tmp_path / "b", key=k1, compress=True)
    sha = store.put(_DATA)
    rotated, skipped = rotate_key(tmp_path / "b", k1, k2)
    assert (rotated, skipped) == (1, 0)
    assert BlobStore(tmp_path / "b", key=k2, compress=True).get(sha) == _DATA
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        BlobStore(tmp_path / "b", key=k1).get(sha)


def test_rotate_enable_and_disable_encryption(tmp_path):
    key = Fernet.generate_key()
    store = BlobStore(tmp_path / "b")           # plaintext
    sha = store.put(_DATA)
    # None -> key : encrypt in place
    rotate_key(tmp_path / "b", None, key)
    assert BlobStore(tmp_path / "b", key=key).get(sha) == _DATA
    # key -> None : decrypt in place
    rotate_key(tmp_path / "b", key, None)
    assert BlobStore(tmp_path / "b").get(sha) == _DATA


def test_rotate_wrong_old_key_errors(tmp_path):
    k1, wrong = Fernet.generate_key(), Fernet.generate_key()
    BlobStore(tmp_path / "b", key=k1).put(_DATA)
    with pytest.raises(BlobStoreError):
        rotate_key(tmp_path / "b", wrong, Fernet.generate_key())
