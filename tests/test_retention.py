import textwrap

import pytest
import yaml

from otitbup.blobstore import BlobStore, make_pointer, parse_pointer
from otitbup.config import ConfigError, load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.models import Device
from otitbup.restore import export_bundle
from otitbup.retention import apply as retention_apply
from otitbup.retention import describe_policy, plan


@pytest.fixture
def retention_config(tmp_path):
    path = tmp_path / "otitbup.yml"
    path.write_text(textwrap.dedent("""
        data_dir: ./data
        retention:
          keep_versions: 30
        sites:
          - name: plant-a
            retention:
              keep_days: 365
            zones:
              - name: cell-1
                retention:
                  large_file_threshold: 16
                devices:
                  - name: plc-01
                    driver: fake
                    retention:
                      keep_versions: 1
                      keep_days: 0
                  - name: plc-02
                    driver: fake
    """))
    return load_config(path)


def test_policy_hierarchy_and_sources(retention_config):
    config = retention_config
    plc1, plc2 = config.all_devices()

    policy1 = config.retention_for(plc1)
    assert policy1 == {
        "keep_versions": 1, "keep_days": 0, "large_file_threshold": 16,
    }
    sources1 = config.retention_sources(plc1)
    assert sources1["keep_versions"] == "device"
    assert sources1["keep_days"] == "device"
    assert sources1["large_file_threshold"] == "zone"

    policy2 = config.retention_for(plc2)
    assert policy2["keep_versions"] == 30      # global
    assert policy2["keep_days"] == 365         # site
    assert config.retention_sources(plc2)["keep_versions"] == "global"


def test_unknown_retention_key_rejected(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("""
data_dir: ./data
retention: {keep_forever: 1}
sites: []
""")
    with pytest.raises(ConfigError, match="keep_forever"):
        load_config(path)


def test_blobstore_roundtrip_and_pointer(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    sha = store.put(b"BIGDATA")
    assert store.has(sha)
    assert store.get(sha) == b"BIGDATA"
    assert store.put(b"BIGDATA") == sha           # dedup, idempotent
    assert store.all_blobs() == {sha: 7}

    pointer = make_pointer(sha, 7)
    assert parse_pointer(pointer) == (sha, 7)
    assert parse_pointer(b"just a config file") is None

    assert store.delete(sha) == 7
    assert not store.has(sha)
    assert store.delete(sha) == 0


def test_write_and_commit_offloads_large_artifacts(tmp_path):
    store = GitStore(tmp_path / "data")
    store.ensure_repo()
    blobs = BlobStore(tmp_path / "blobs")
    device = Device(name="d", driver="fake", site="s", zone="z")

    big, small = b"X" * 100, b"tiny"
    store.write_and_commit(
        device,
        [Artifact(name="project.bin", data=big),
         Artifact(name="config.txt", data=small)],
        blobstore=blobs, threshold=16,
    )
    device_dir = store.root / device.path
    pointer = parse_pointer((device_dir / "project.bin").read_bytes())
    assert pointer is not None
    assert blobs.get(pointer[0]) == big           # content in the blob store
    assert (device_dir / "config.txt").read_bytes() == small  # inline

    manifest = yaml.safe_load((device_dir / "manifest.yml").read_text())
    artifacts = manifest["artifacts"]
    assert artifacts["project.bin"]["offloaded"] is True
    assert artifacts["project.bin"]["size"] == 100
    import hashlib
    assert artifacts["project.bin"]["sha256"] == hashlib.sha256(big).hexdigest()
    # Provenance travels in the manifest (stable fields) + commit trailers.
    assert manifest["provenance"]["driver"] == device.driver
    assert manifest["provenance"]["device_guid"]
    assert "offloaded" not in artifacts["config.txt"]


@pytest.fixture
def pruning_setup(retention_config):
    config = retention_config
    store = GitStore(config.data_dir)
    store.ensure_repo()
    blobs = BlobStore(config.data_dir + "/../blobs")
    plc1, plc2 = config.all_devices()
    # Three versions for plc-01 (keep_versions: 1), threshold 16 from zone.
    shas = []
    for version in (b"A" * 32, b"B" * 32, b"C" * 32):
        store.write_and_commit(
            plc1, [Artifact(name="prog.bin", data=version)],
            blobstore=blobs, threshold=16,
        )
        shas.append(blobs.put(version))
    # plc-02 (unlimited) shares the middle blob.
    store.write_and_commit(
        plc2, [Artifact(name="prog.bin", data=b"B" * 32)],
        blobstore=blobs, threshold=16,
    )
    return config, store, blobs, shas


def test_prune_respects_keep_versions_and_sharing(pruning_setup):
    config, store, blobs, (sha_a, sha_b, sha_c) = pruning_setup
    prune_plan = plan(config, store, blobs)

    by_name = {d.device: d for d in prune_plan.devices}
    assert by_name["plant-a/cell-1/plc-01"].backups == 3
    assert by_name["plant-a/cell-1/plc-01"].kept_backups == 1
    assert by_name["plant-a/cell-1/plc-02"].kept_backups == 1  # unlimited... kept

    # A is only referenced by plc-01's expired backups -> deletable.
    # B is expired for plc-01 but retained by plc-02 (unlimited policy).
    # C is plc-01's newest backup -> kept.
    assert set(prune_plan.deletable) == {sha_a}

    freed = retention_apply(prune_plan, blobs)
    assert freed == 32
    assert not blobs.has(sha_a)
    assert blobs.has(sha_b) and blobs.has(sha_c)


def test_restore_resolves_offloaded_content(pruning_setup, tmp_path):
    config, store, blobs, _ = pruning_setup
    plc1 = config.all_devices()[0]
    out = tmp_path / "bundle"
    _, mismatches = export_bundle(store, plc1, out, blobstore=blobs)
    assert mismatches == []
    assert (out / "artifacts/prog.bin").read_bytes() == b"C" * 32


def test_restore_flags_expired_content(pruning_setup, tmp_path):
    config, store, blobs, (_, _, sha_c) = pruning_setup
    plc1 = config.all_devices()[0]
    blobs.delete(sha_c)  # simulate over-eager pruning of the latest blob
    out = tmp_path / "bundle"
    _, mismatches = export_bundle(store, plc1, out, blobstore=blobs)
    assert mismatches and "expired by retention" in mismatches[0]


def test_describe_policy():
    text = describe_policy(
        {"keep_versions": 5, "keep_days": 0, "large_file_threshold": 1048576}
    )
    assert "5" in text and "unlimited" in text and "1024 KiB" in text
