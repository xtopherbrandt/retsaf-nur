import tomllib

import pytest
from typer.testing import CliRunner

from runcoach_cli import config
from runcoach_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_config_path(tmp_path, monkeypatch):
    """Point CONFIG_PATH at a throwaway location for every test in this module.

    Never touches the real ``~/.runcoach/`` directory.
    """
    config_path = tmp_path / ".runcoach" / "cli.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    return config_path


def test_init_writes_config_and_refuses_overwrite_without_force(isolated_config_path):
    config_path = isolated_config_path

    # No existing config: init writes the file with the given API URL.
    result = runner.invoke(app, ["init", "--api-url", "http://x"])
    assert result.exit_code == 0
    assert config_path.exists()
    written = tomllib.loads(config_path.read_text())
    assert written == {"api_url": "http://x"}

    original_bytes = config_path.read_bytes()

    # Re-running without --force refuses to overwrite and leaves the file
    # byte-for-byte unchanged.
    result = runner.invoke(app, ["init", "--api-url", "http://y"])
    assert result.exit_code != 0
    assert "--force" in result.output
    assert config_path.read_bytes() == original_bytes


def test_init_overwrites_with_force(isolated_config_path):
    config_path = isolated_config_path

    runner.invoke(app, ["init", "--api-url", "http://x"])
    result = runner.invoke(app, ["init", "--api-url", "http://y", "--force"])

    assert result.exit_code == 0
    written = tomllib.loads(config_path.read_text())
    assert written == {"api_url": "http://y"}


def test_init_creates_parent_directory(isolated_config_path):
    config_path = isolated_config_path
    assert not config_path.parent.exists()

    result = runner.invoke(app, ["init", "--api-url", "http://x"])

    assert result.exit_code == 0
    assert config_path.parent.exists()
    assert config_path.exists()
