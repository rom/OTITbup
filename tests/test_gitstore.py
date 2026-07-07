import pytest

from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore, GitStoreError
from otitbup.models import Device


@pytest.fixture
def store(tmp_path):
    s = GitStore(tmp_path / "data")
    s.ensure_repo()
    return s


@pytest.fixture
def device():
    return Device(name="plc-01", driver="fake", site="plant-a", zone="cell-1")


def test_commit_and_no_change_detection(store, device):
    artifacts = [Artifact(name="config.txt", data=b"hello")]
    commit = store.write_and_commit(device, artifacts)
    assert commit

    # Same content again: no new commit.
    assert store.write_and_commit(device, artifacts) is None

    # Changed content: new commit and a visible diff.
    commit2 = store.write_and_commit(
        device, [Artifact(name="config.txt", data=b"changed")]
    )
    assert commit2 and commit2 != commit
    assert "changed" in store.last_diff(device)
    assert "backup(plant-a/cell-1/plc-01)" in store.history(device)


def test_removed_artifacts_are_deleted(store, device):
    store.write_and_commit(device, [
        Artifact(name="a.txt", data=b"a"),
        Artifact(name="b.txt", data=b"b"),
    ])
    store.write_and_commit(device, [Artifact(name="a.txt", data=b"a")])
    device_dir = store.root / device.path
    assert (device_dir / "a.txt").exists()
    assert not (device_dir / "b.txt").exists()


def test_manifest_contains_hashes(store, device):
    store.write_and_commit(device, [Artifact(name="x.bin", data=b"\x00\x01")])
    manifest = (store.root / device.path / "manifest.yml").read_text()
    assert "sha256" in manifest and "x.bin" in manifest


def test_path_traversal_rejected(store, device):
    with pytest.raises(GitStoreError, match="escapes"):
        store.write_and_commit(
            device, [Artifact(name="../../evil.txt", data=b"x")]
        )
