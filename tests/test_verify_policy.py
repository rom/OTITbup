import textwrap

import pytest

from otitbup.blobstore import BlobStore
from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.models import Device
from otitbup.policy import check_all, check_device
from otitbup.verify import verify


@pytest.fixture
def setup(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: plant-a
            zones:
              - name: net
                devices:
                  - {name: sw-01, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    return config, store, tmp_path


def _device(config):
    return config.all_devices()[0]


def test_verify_clean_and_corrupted(setup):
    config, store, _ = setup
    device = _device(config)
    store.write_and_commit(device, [Artifact(name="show_running_config.txt",
                                             data=b"hostname sw-01\n")])
    report = verify(config, store)
    assert report.ok
    assert report.checked_devices == 1

    # Corrupt the working tree artifact and re-commit a bad manifest pairing
    # by tampering the stored file directly is hard; instead verify catches
    # a genuine mismatch via a doctored manifest.
    device_dir = store.root / device.path
    (device_dir / "show_running_config.txt").write_bytes(b"tampered\n")
    store._git("add", "-A")
    store._git("-c", "user.name=t", "-c", "user.email=t@t",
               "commit", "-m", "tamper", "--no-verify")
    report = verify(config, store)
    assert not report.ok


def test_verify_offloaded_blob_missing(setup):
    config, store, tmp_path = setup
    device = _device(config)
    blobs = BlobStore(tmp_path / "blobs")
    store.write_and_commit(
        device, [Artifact(name="big.bin", data=b"X" * 100)],
        blobstore=blobs, threshold=16,
    )
    assert verify(config, store, blobstore=blobs).ok
    # Prune the blob -> verification must flag it.
    for sha in list(blobs.all_blobs()):
        blobs.delete(sha)
    report = verify(config, store, blobstore=blobs)
    assert not report.ok


def test_policy_flags_insecure_config(setup):
    config, store, _ = setup
    device = _device(config)
    store.write_and_commit(device, [Artifact(
        name="show_running_config.txt",
        data=(
            b"hostname sw-01\n"
            b"enable password cisco\n"
            b"snmp-server community public RO\n"
            b"ip http server\n"
            b" transport input telnet ssh\n"
        ),
    )])
    findings = check_device(config, store, device)
    ids = {f.rule_id for f in findings}
    assert "no-telnet" in ids
    assert "no-snmp-public" in ids
    assert "no-http-server" in ids
    assert "no-default-cisco-pw" in ids
    assert any(f.severity == "critical" for f in findings)


def test_policy_custom_and_disable_rules(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        policy:
          disable: [no-snmpv1v2]
          rules:
            - id: require-aaa
              description: no AAA configured
              severity: medium
              absent: "aaa new-model"
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {name: d, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    device = config.all_devices()[0]
    store.write_and_commit(device, [Artifact(
        name="show_running_config.txt",
        data=b"hostname d\nsnmp-server community public RO\n",
    )])
    findings = {f.rule_id for f in check_device(config, store, device)}
    assert "require-aaa" in findings        # custom absent-rule fired
    assert "no-snmpv1v2" not in findings     # disabled


def test_annotations(setup):
    config, store, _ = setup
    device = _device(config)
    store.write_and_commit(device, [Artifact(name="c.txt", data=b"v1")])
    commit = store.last_commit_hash(device)
    store.set_annotation(commit, "MOC-1234: firmware upgrade")
    assert "MOC-1234" in store.get_annotation(commit)
    assert commit in store.annotated_commits()
