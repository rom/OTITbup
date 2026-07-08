"""Detached digital signatures for reports (Ed25519).

Reports that go to auditors need to be tamper-evident. `otitbup report
--sign` produces the report plus a detached signature file
(`<report>.sig`, base64) and a companion `<report>.pubkey`. Anyone can
verify with `otitbup report-verify <report> --sig <sig> --pubkey <key>`.

Ed25519 keys are small and fast; keys are generated on demand and written
0600 if they don't already exist, so a first `--sign` is turnkey.
"""
from __future__ import annotations

import base64
from pathlib import Path


class SigningError(Exception):
    pass


def _load_crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        return serialization, Ed25519PrivateKey, Ed25519PublicKey
    except ImportError as exc:
        raise SigningError(
            "signing requires the 'cryptography' package "
            "(pip install otitbup[crypto])"
        ) from exc


def ensure_keypair(key_file: str | Path) -> Path:
    """Return the private-key path, generating an Ed25519 key (0600) and a
    sibling <key>.pub if absent."""
    serialization, Ed25519PrivateKey, _ = _load_crypto()
    key_file = Path(key_file)
    if not key_file.exists():
        key = Ed25519PrivateKey.generate()
        key_file.touch(mode=0o600)
        key_file.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        key_file.chmod(0o600)
        pub = key_file.with_suffix(key_file.suffix + ".pub")
        pub.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
    return key_file


def sign_file(path: str | Path, key_file: str | Path) -> Path:
    """Sign `path`, writing `<path>.sig` (base64 detached signature) and
    `<path>.pubkey` (the public key). Returns the .sig path."""
    serialization, _, _ = _load_crypto()
    ensure_keypair(key_file)
    private = serialization.load_pem_private_key(
        Path(key_file).read_bytes(), password=None)
    data = Path(path).read_bytes()
    signature = private.sign(data)
    sig_path = Path(str(path) + ".sig")
    sig_path.write_text(base64.b64encode(signature).decode() + "\n")
    pub_path = Path(str(path) + ".pubkey")
    pub_path.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return sig_path


def verify_file(
    path: str | Path, sig_file: str | Path, pubkey_file: str | Path
) -> bool:
    serialization, _, _ = _load_crypto()
    from cryptography.exceptions import InvalidSignature
    public = serialization.load_pem_public_key(
        Path(pubkey_file).read_bytes())
    signature = base64.b64decode(Path(sig_file).read_text().strip())
    try:
        public.verify(signature, Path(path).read_bytes())
        return True
    except InvalidSignature:
        return False
