import pytest

from otitbup.config import ConfigError, load_config


def test_load_valid_config(config_file):
    config = load_config(config_file)
    devices = config.all_devices()
    assert [d.qualified_name for d in devices] == [
        "plant-a/cell-1/plc-01",
        "plant-a/network/sw-01",
    ]
    assert config.data_dir.endswith("/data")
    zone = config.find_zone(devices[0])
    assert zone.maintenance_window == "22:00-06:00"


def test_find_devices_by_bare_and_qualified_name(config_file):
    config = load_config(config_file)
    assert config.find_devices(["plc-01"])[0].name == "plc-01"
    assert config.find_devices(["plant-a/network/sw-01"])[0].name == "sw-01"
    with pytest.raises(KeyError):
        config.find_devices(["nope"])


def test_duplicate_device_rejected(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("""
data_dir: ./data
sites:
  - name: a
    zones:
      - name: z
        devices:
          - {name: d1, driver: fake}
          - {name: d1, driver: fake}
""")
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(path)


def test_bad_schedule_rejected(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("""
data_dir: ./data
sites:
  - name: a
    zones:
      - name: z
        devices:
          - {name: d1, driver: fake, schedule: whenever}
""")
    with pytest.raises(ValueError):
        load_config(path)


def test_bad_window_rejected(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("""
data_dir: ./data
sites:
  - name: a
    zones:
      - name: z
        maintenance_window: "sometimes"
        devices: []
""")
    with pytest.raises(ValueError):
        load_config(path)
