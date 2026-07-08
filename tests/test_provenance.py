import textwrap

import yaml

from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore, manifest_artifacts
from otitbup.models import Device


def test_effective_guid_stable_and_pinnable():
    d = Device(name="p1", driver="cisco_ios", site="s", zone="z")
    g1 = d.effective_guid
    assert g1 == Device(name="p1", driver="cisco_ios", site="s",
                        zone="z").effective_guid          # deterministic
    pinned = Device(name="p1", driver="cisco_ios", site="s", zone="z",
                    guid="fixed-123")
    assert pinned.effective_guid == "fixed-123"


def test_manifest_artifacts_both_shapes():
    flat = {"a.txt": {"sha256": "x"}}
    nested = {"provenance": {"driver": "d"}, "artifacts": flat}
    assert manifest_artifacts(flat) == flat
    assert manifest_artifacts(nested) == flat
    assert manifest_artifacts("nope") == {}


def test_provenance_in_manifest_and_trailers(tmp_path):
    store = GitStore(tmp_path / "data")
    store.ensure_repo()
    dev = Device(name="plc-01", driver="cisco_ios", site="s", zone="z",
                 guid="dev-guid-1")
    commit = store.write_and_commit(
        dev, [Artifact(name="c.txt", data=b"config")],
        appliance="appliance-x", captured_at="2026-07-08T10:00:00+00:00")
    assert commit
    # Manifest carries stable provenance + artifacts.
    raw = store.read_file_at(commit, f"{dev.path}/manifest.yml")
    manifest = yaml.safe_load(raw)
    assert manifest["provenance"] == {"device_guid": "dev-guid-1",
                                      "driver": "cisco_ios"}
    assert "c.txt" in manifest["artifacts"]
    # Commit trailers carry volatile provenance.
    msg = store._git("log", "-1", "--format=%B").strip()
    assert "Device-GUID: dev-guid-1" in msg
    assert "Tool-Version:" in msg
    assert "Appliance: appliance-x" in msg
    assert "Captured-At: 2026-07-08T10:00:00+00:00" in msg


def test_no_spurious_commit_from_provenance(tmp_path):
    store = GitStore(tmp_path / "data")
    store.ensure_repo()
    dev = Device(name="p", driver="cisco_ios", site="s", zone="z")
    art = [Artifact(name="c.txt", data=b"same")]
    assert store.write_and_commit(dev, art) is not None
    # Identical content on a later run must NOT commit, despite provenance.
    assert store.write_and_commit(dev, art) is None


def test_verify_passes_with_nested_manifest(tmp_path):
    store = GitStore(tmp_path / "data")
    store.ensure_repo()
    dev = Device(name="p", driver="cisco_ios", site="s", zone="z")
    commit = store.write_and_commit(dev, [Artifact(name="c.txt", data=b"x")])
    assert store.verify_commit(dev, commit) == []


def test_guids_assign_persists(tmp_path):
    from otitbup import configedit
    p = tmp_path / "otitbup.yml"
    p.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: s
            zones: [{name: z, devices: [{name: d1, driver: cisco_ios}]}]
    """))
    config = load_config(p)
    dev = config.all_devices()[0]
    assert dev.guid == ""                       # not pinned
    import uuid
    configedit.set_device(p, "s", "z", "d1", {"guid": str(uuid.uuid4())})
    assert load_config(p).all_devices()[0].guid                # now pinned
