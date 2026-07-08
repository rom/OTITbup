import textwrap

import pytest

from otitbup.config import load_config
from otitbup.drivers import register
from otitbup.drivers.base import Artifact, Driver
from otitbup.gitstore import GitStore
from otitbup.runner import Runner


def _driver(name, data):
    class _D(Driver):
        def collect(self, device, secrets):
            return [Artifact(name="c.txt", data=data)] if data is not None else []
    _D.name = name
    register(name, _D)
    return name


def _runner(tmp_path, cfg_yaml, driver):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent(cfg_yaml))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    return Runner(config, store, force=True), config


def test_min_bytes_rejects_truncated(tmp_path):
    _driver("tiny_cap", b"x")
    runner, config = _runner(tmp_path, """
        data_dir: ./data
        capture: {min_bytes: 100}
        sites:
          - name: s
            zones: [{name: z, devices: [{name: d1, driver: tiny_cap}]}]
    """, "tiny_cap")
    [res] = runner.backup_devices(config.all_devices())
    assert not res.ok and "too small" in res.message


def test_empty_capture_rejected(tmp_path):
    _driver("empty_cap", None)
    runner, config = _runner(tmp_path, """
        data_dir: ./data
        sites:
          - name: s
            zones: [{name: z, devices: [{name: d1, driver: empty_cap}]}]
    """, "empty_cap")
    [res] = runner.backup_devices(config.all_devices())
    assert not res.ok and "no artifacts" in res.message


def test_expect_match_content_guard(tmp_path):
    _driver("login_cap", b"<html>please log in</html>")
    runner, config = _runner(tmp_path, """
        data_dir: ./data
        sites:
          - name: s
            zones:
              - name: z
                devices:
                  - {name: d1, driver: login_cap,
                     options: {expect_match: "hostname"}}
    """, "login_cap")
    [res] = runner.backup_devices(config.all_devices())
    assert not res.ok and "content check" in res.message


def test_good_capture_records_size(tmp_path):
    _driver("good_cap", b"hostname router-1\n" * 20)
    runner, config = _runner(tmp_path, """
        data_dir: ./data
        capture: {min_bytes: 10}
        sites:
          - name: s
            zones: [{name: z, devices: [{name: d1, driver: good_cap}]}]
    """, "good_cap")
    [res] = runner.backup_devices(config.all_devices())
    assert res.ok and res.size_bytes == len(b"hostname router-1\n" * 20)
    status = runner.runstore.recent_runs("s/z/d1", limit=1)[0]
    assert status["size_bytes"] == res.size_bytes


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
