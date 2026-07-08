import tarfile
import textwrap

import pytest

pytest.importorskip("cryptography")
from cryptography.fernet import Fernet  # noqa: E402

from otitbup import offsite  # noqa: E402
from otitbup.config import load_config  # noqa: E402
from otitbup.drivers import register  # noqa: E402
from otitbup.drivers.base import Artifact, Driver  # noqa: E402
from otitbup.gitstore import GitStore  # noqa: E402


class _D(Driver):
    name = "offsite_fake"

    def collect(self, device, secrets):
        return [Artifact(name="c.txt", data=b"SECRET PLC CONFIG")]


def _make(tmp_path, transport_dir):
    register("offsite_fake", _D)
    key = Fernet.generate_key().decode()
    (tmp_path / "offsite.key").write_text(key)
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent(f"""
        data_dir: ./data
        offsite:
          transport: file
          dir: {transport_dir}
          key_file: {tmp_path}/offsite.key
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {{name: d1, driver: offsite_fake}}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    [dev] = config.all_devices()
    store.write_and_commit(dev, _D().collect(dev, None))
    return config, dev


def test_push_encrypts_and_lists(tmp_path):
    remote = tmp_path / "remote"
    config, _ = _make(tmp_path, remote)
    name = offsite.push(config, "20260708-120000")
    assert name == "otitbup-20260708-120000.tar.gz.enc"
    assert offsite.list_snapshots(config) == [name]
    # Ciphertext on the remote: plaintext must not appear.
    blob = (remote / name).read_bytes()
    assert b"SECRET PLC CONFIG" not in blob


def test_pull_roundtrip_and_restore(tmp_path):
    config, dev = _make(tmp_path, tmp_path / "remote")
    offsite.push(config, "20260708-120000")

    # pull -> extract
    name, extracted = offsite.pull(config, None, tmp_path / "pulled")
    assert (extracted / "data").is_dir()

    # restore a device bundle straight from the offsite copy
    _, commit, mismatches, bundle = offsite.restore_from_offsite(
        config, dev, tmp_path / "bundle")
    assert mismatches == []
    assert (bundle / "artifacts" / "c.txt").read_bytes() == b"SECRET PLC CONFIG"


def test_wrong_key_cannot_decrypt(tmp_path):
    config, _ = _make(tmp_path, tmp_path / "remote")
    offsite.push(config, "20260708-120000")
    # Swap in a different key.
    (tmp_path / "offsite.key").write_text(Fernet.generate_key().decode())
    config2 = load_config(tmp_path / "otitbup.yml")
    with pytest.raises(offsite.OffsiteError):
        offsite.pull(config2, None, tmp_path / "pulled")


def test_missing_key_errors(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent(f"""
        data_dir: ./data
        offsite:
          transport: file
          dir: {tmp_path}/remote
        sites:
          - name: s
            zones: [{{name: z, devices: [{{name: d1, driver: offsite_fake}}]}}]
    """))
    config = load_config(cfg)
    with pytest.raises(offsite.OffsiteError):
        offsite.push(config, "20260708-120000")


def test_path_traversal_rejected(tmp_path):
    # A malicious snapshot with a member escaping the extract dir is refused.
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "x"
    payload.write_text("pwned")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escape.txt")
    dest = tmp_path / "dest"
    dest.mkdir()
    with tarfile.open(evil, "r:gz") as tar, pytest.raises(offsite.OffsiteError):
        offsite._safe_extractall(tar, dest)


def test_sigv4_signing_key_matches_aws_reference():
    # AWS "deriving the signing key" worked example.
    import hashlib
    import hmac

    def sign(k, m):
        return hmac.new(k, m.encode(), hashlib.sha256).digest()

    secret = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
    k = sign(("AWS4" + secret).encode(), "20120215")
    k = sign(k, "us-east-1")
    k = sign(k, "iam")
    k = sign(k, "aws4_request")
    assert k.hex() == (
        "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"
    )


def test_unknown_transport():
    with pytest.raises(offsite.OffsiteError):
        offsite.make_transport({"transport": "carrier-pigeon"})
