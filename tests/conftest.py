import textwrap

import pytest


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "otitbup.yml"
    path.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: plant-a
            zones:
              - name: cell-1
                maintenance_window: "22:00-06:00"
                max_concurrent: 1
                devices:
                  - name: plc-01
                    driver: fake
                    schedule: 1h
              - name: network
                max_concurrent: 2
                devices:
                  - name: sw-01
                    driver: fake
                    schedule: 30m
    """))
    return path
