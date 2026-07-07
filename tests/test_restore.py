import pytest

from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.models import Device
from otitbup.restore import RestoreError, export_bundle


@pytest.fixture
def store(tmp_path):
    s = GitStore(tmp_path / "data")
    s.ensure_repo()
    return s


@pytest.fixture
def device():
    return Device(
        name="plc-01", driver="siemens_s7", site="plant-a", zone="cell-1"
    )


def _backup(store, device, payload):
    return store.write_and_commit(device, [
        Artifact(name="blocks/OB_1.mc7", data=payload, kind="logic"),
        Artifact(name="cpu_info.yml", data=b"ModuleTypeName: CPU 315\n",
                 kind="metadata"),
    ])


def test_export_latest_bundle(store, device, tmp_path):
    _backup(store, device, b"v1")
    commit2 = _backup(store, device, b"v2")

    out = tmp_path / "bundle"
    commit, mismatches = export_bundle(store, device, out)
    assert commit == commit2
    assert mismatches == []
    assert (out / "artifacts/blocks/OB_1.mc7").read_bytes() == b"v2"
    assert (out / "artifacts/manifest.yml").exists()

    text = (out / "RESTORE.md").read_text()
    assert "plant-a/cell-1/plc-01" in text
    assert "All artifact hashes match" in text
    assert "MC7" in text  # siemens-specific instructions present
    assert "no device writes" in text


def test_export_older_commit(store, device, tmp_path):
    commit1 = _backup(store, device, b"v1")
    _backup(store, device, b"v2")

    out = tmp_path / "bundle-old"
    commit, mismatches = export_bundle(store, device, out, commit=commit1)
    assert commit == commit1
    assert mismatches == []
    assert (out / "artifacts/blocks/OB_1.mc7").read_bytes() == b"v1"


def test_no_backups_is_clear_error(store, device, tmp_path):
    with pytest.raises(RestoreError, match="no backups"):
        export_bundle(store, device, tmp_path / "bundle")


def test_refuses_nonempty_output_dir(store, device, tmp_path):
    _backup(store, device, b"v1")
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "precious.txt").write_text("do not clobber")
    with pytest.raises(RestoreError, match="not empty"):
        export_bundle(store, device, out)
    assert (out / "precious.txt").exists()
