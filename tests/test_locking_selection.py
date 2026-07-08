import textwrap

import pytest

from otitbup.config import load_config
from otitbup.filelock import FileLock, LockBusy


def test_filelock_is_exclusive(tmp_path):
    lock_path = tmp_path / "otitbup.lock"
    first = FileLock(lock_path)
    first.acquire()
    try:
        with pytest.raises(LockBusy):
            FileLock(lock_path).acquire(blocking=False)
    finally:
        first.release()
    # Released -> acquirable again.
    second = FileLock(lock_path)
    second.acquire()
    second.release()


def test_filelock_context_manager(tmp_path):
    with FileLock(tmp_path / "l"):
        with pytest.raises(LockBusy):
            FileLock(tmp_path / "l").acquire()


@pytest.fixture
def multi_config(tmp_path):
    path = tmp_path / "otitbup.yml"
    path.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: plant-a
            zones:
              - name: cell-1
                devices:
                  - {name: plc-01, driver: cisco_ios}
                  - {name: plc-02, driver: cisco_ios}
              - name: net
                devices:
                  - {name: sw-01, driver: cisco_ios}
          - name: plant-b
            zones:
              - name: cell-1
                devices:
                  - {name: plc-09, driver: cisco_ios}
    """))
    return load_config(path)


def test_select_by_site(multi_config):
    names = [d.qualified_name for d in multi_config.select(sites=["plant-a"])]
    assert names == [
        "plant-a/cell-1/plc-01", "plant-a/cell-1/plc-02", "plant-a/net/sw-01",
    ]


def test_select_by_zone(multi_config):
    # zone name 'cell-1' exists in both sites -> both included.
    names = [d.qualified_name for d in multi_config.select(zones=["cell-1"])]
    assert names == [
        "plant-a/cell-1/plc-01", "plant-a/cell-1/plc-02", "plant-b/cell-1/plc-09",
    ]


def test_select_site_and_zone(multi_config):
    names = [
        d.qualified_name
        for d in multi_config.select(sites=["plant-a"], zones=["cell-1"])
    ]
    assert names == ["plant-a/cell-1/plc-01", "plant-a/cell-1/plc-02"]


def test_select_names_plus_filter_union(multi_config):
    names = [
        d.qualified_name
        for d in multi_config.select(names=["sw-01"], sites=["plant-b"])
    ]
    assert names == ["plant-a/net/sw-01", "plant-b/cell-1/plc-09"]


def test_select_empty_returns_all(multi_config):
    assert len(multi_config.select()) == 4
