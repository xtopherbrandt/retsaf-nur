"""Tests for runcoach_api.config: AppConfig + load_config().

Covers the three distinct, typed failure modes required by F001:
missing config file, corrupt/unparseable TOML, and an invalid field
value (bad port).
"""

from __future__ import annotations

import pydantic
import pytest

from runcoach_api.config import (
    AppConfig,
    ConfigCorruptError,
    ConfigNotFoundError,
    load_config,
)


def test_load_config_missing_raises_not_found(tmp_path):
    missing_path = tmp_path / "does-not-exist.toml"

    with pytest.raises(ConfigNotFoundError) as exc_info:
        load_config(path=missing_path)

    # Message must name the expected path so the operator knows where
    # to create the config file.
    assert str(missing_path) in str(exc_info.value)


def test_load_config_corrupt_raises_corrupt_error(tmp_path):
    corrupt_path = tmp_path / "api.toml"
    corrupt_path.write_text("this is not [valid toml", encoding="utf-8")

    with pytest.raises(ConfigCorruptError) as exc_info:
        load_config(path=corrupt_path)

    message = str(exc_info.value)
    assert str(corrupt_path) in message
    # Must be distinct wording from an "invalid value" validation error.
    assert "invalid" not in message.lower() or "pars" in message.lower()


def test_load_config_invalid_port_raises_validation_error(tmp_path):
    bad_port_path = tmp_path / "api.toml"
    bad_port_path.write_text(
        'host = "127.0.0.1"\nport = "abc"\ndata_dir = "/tmp/data"\n',
        encoding="utf-8",
    )

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load_config(path=bad_port_path)

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("port",) for error in errors)


def test_load_config_valid_returns_app_config(tmp_path):
    good_path = tmp_path / "api.toml"
    data_dir = tmp_path / "data"
    good_path.write_text(
        f'host = "127.0.0.1"\nport = 8000\ndata_dir = "{data_dir.as_posix()}"\n',
        encoding="utf-8",
    )

    config = load_config(path=good_path)

    assert isinstance(config, AppConfig)
    assert config.host == "127.0.0.1"
    assert config.port == 8000
