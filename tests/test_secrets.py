import pytest

from otitbup.secrets import (
    EncryptedFileBackend,
    PlainFileBackend,
    SecretsError,
    decrypt_file,
    encrypt_file,
    generate_key,
)

pytest.importorskip("cryptography")


def test_genkey_writes_protected_file(tmp_path):
    key_file = tmp_path / "otitbup.key"
    key = generate_key(key_file)
    assert key_file.read_text().strip() == key
    assert (key_file.stat().st_mode & 0o777) == 0o600


def test_encrypt_decrypt_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("OTITBUP_KEY", raising=False)
    key_file = tmp_path / "otitbup.key"
    generate_key(key_file)

    plain = tmp_path / "secrets.yml"
    plain.write_text("sw-01:\n  username: backup\n  password: hunter2\n")
    enc = tmp_path / "secrets.enc"
    encrypt_file(plain, enc, key_file=str(key_file))

    assert b"hunter2" not in enc.read_bytes()
    assert (enc.stat().st_mode & 0o777) == 0o600
    assert "hunter2" in decrypt_file(enc, key_file=str(key_file))

    backend = EncryptedFileBackend(enc, key_file=str(key_file))
    assert backend.get("sw-01")["password"] == "hunter2"


def test_encrypt_via_env_key(tmp_path, monkeypatch):
    key = generate_key()
    monkeypatch.setenv("OTITBUP_KEY", key)
    plain = tmp_path / "secrets.yml"
    plain.write_text("d: {username: u, password: p}\n")
    enc = tmp_path / "secrets.enc"
    encrypt_file(plain, enc)
    backend = EncryptedFileBackend(enc)
    assert backend.get("d")["username"] == "u"


def test_missing_key_is_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("OTITBUP_KEY", raising=False)
    plain = tmp_path / "secrets.yml"
    plain.write_text("d: {username: u}\n")
    with pytest.raises(SecretsError, match="no encryption key"):
        encrypt_file(plain, tmp_path / "out.enc")


def test_malformed_yaml_rejected_before_encrypting(tmp_path, monkeypatch):
    monkeypatch.setenv("OTITBUP_KEY", generate_key())
    plain = tmp_path / "secrets.yml"
    plain.write_text("- just\n- a list\n")
    with pytest.raises(SecretsError, match="mapping"):
        encrypt_file(plain, tmp_path / "out.enc")


def test_plain_backend_unknown_name(tmp_path):
    path = tmp_path / "secrets.yml"
    path.write_text("a: {username: u}\n")
    with pytest.raises(SecretsError, match="no credentials"):
        PlainFileBackend(path).get("b")
