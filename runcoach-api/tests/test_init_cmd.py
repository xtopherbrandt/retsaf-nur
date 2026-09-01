import tomllib

import pytest

from runcoach_api import init_cmd


@pytest.fixture(autouse=True)
def isolated_config_path(tmp_path, monkeypatch):
    """Point CONFIG_PATH at a throwaway location for every test in this module."""
    config_path = tmp_path / ".runcoach" / "api.toml"
    monkeypatch.setattr(init_cmd, "CONFIG_PATH", config_path)
    return config_path


def test_api_init_writes_config_and_refuses_overwrite_without_force(isolated_config_path):
    config_path = isolated_config_path

    # No existing config: init writes the file with the given values.
    init_cmd.main(["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x"])

    assert config_path.exists()
    written = tomllib.loads(config_path.read_text())
    assert written == {"host": "127.0.0.1", "port": 8123, "data_dir": "/tmp/x"}

    # Re-running without --force refuses to overwrite and leaves the file untouched.
    with pytest.raises(SystemExit) as exc_info:
        init_cmd.main(["--host", "0.0.0.0", "--port", "9999", "--data-dir", "/tmp/y"])

    assert exc_info.value.code != 0
    unchanged = tomllib.loads(config_path.read_text())
    assert unchanged == {"host": "127.0.0.1", "port": 8123, "data_dir": "/tmp/x"}


def test_api_init_overwrites_with_force(isolated_config_path):
    config_path = isolated_config_path

    init_cmd.main(["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x"])
    init_cmd.main(["--host", "0.0.0.0", "--port", "9999", "--data-dir", "/tmp/y", "--force"])

    written = tomllib.loads(config_path.read_text())
    assert written == {"host": "0.0.0.0", "port": 9999, "data_dir": "/tmp/y"}


def test_api_init_creates_parent_directory(isolated_config_path):
    config_path = isolated_config_path
    assert not config_path.parent.exists()

    init_cmd.main(["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x"])

    assert config_path.parent.exists()
    assert config_path.exists()
