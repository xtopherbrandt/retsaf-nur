"""Tests for runcoach_api.config: AppConfig + load_config().

Covers the three distinct, typed failure modes required by F001:
missing config file, corrupt/unparseable TOML, and an invalid field
value (bad port).
"""

from __future__ import annotations

from pathlib import Path

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
        'host = "127.0.0.1"\nport = "abc"\ndata_dir = "/tmp/data"\n'
        "resting_hrv_profile_names = []\n",
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
        f'host = "127.0.0.1"\nport = 8000\ndata_dir = "{data_dir.as_posix()}"\n'
        'resting_hrv_profile_names = ["HRV Snapshot"]\n',
        encoding="utf-8",
    )

    config = load_config(path=good_path)

    assert isinstance(config, AppConfig)
    assert config.host == "127.0.0.1"
    assert config.port == 8000


# ---------------------------------------------------------------------------
# resting_hrv_profile_names -- required, no default (F004 amendment 2026-09-06)
#
# The field carries the athlete's Tier-1 declaration. It has no default of any
# kind, including ``[]``: a default would reinstate the behavioural default
# "Tier 1 derives nothing" under the guise of a value, which is the exact
# failure this feature exists to prevent. An ``api.toml`` written before the
# amendment must therefore fail startup rather than silently assume one.
#
# The degenerate-input rows below were generated as an adversarial probe while
# writing this task rather than found later in review: blank, several flavours
# of whitespace-only, a non-list scalar, and a wrong-typed element.
# ---------------------------------------------------------------------------


def _toml(tmp_path, body: str):
    path = tmp_path / "api.toml"
    data_dir = tmp_path / "data"
    path.write_text(
        f'host = "127.0.0.1"\nport = 8000\n'
        f'data_dir = "{data_dir.as_posix()}"\n{body}',
        encoding="utf-8",
    )
    return path


def test_app_config_requires_resting_hrv_profile_names():
    """The field is required: constructing without it is a ValidationError."""
    with pytest.raises(pydantic.ValidationError) as exc_info:
        AppConfig(host="127.0.0.1", port=8000, data_dir=Path("/tmp/data"))

    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("resting_hrv_profile_names",) and error["type"] == "missing"
        for error in errors
    ), errors


def test_load_config_without_resting_hrv_profile_names_is_rejected(tmp_path):
    """A pre-amendment api.toml fails to load rather than defaulting."""
    path = _toml(tmp_path, "")

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load_config(path=path)

    assert any(
        error["loc"] == ("resting_hrv_profile_names",)
        for error in exc_info.value.errors()
    )


def test_app_config_has_no_default_for_resting_hrv_profile_names():
    """Guards against a ``default_factory=list`` creeping back in."""
    field = AppConfig.model_fields["resting_hrv_profile_names"]
    assert field.is_required(), "resting_hrv_profile_names must have no default"


def test_load_config_empty_resting_hrv_profile_list_is_accepted(tmp_path):
    """``[]`` is the explicit "Tier 2 only" declaration and must load."""
    path = _toml(tmp_path, "resting_hrv_profile_names = []\n")

    config = load_config(path=path)

    assert config.resting_hrv_profile_names == []


def test_load_config_populated_resting_hrv_profile_list_is_accepted(tmp_path):
    path = _toml(tmp_path, 'resting_hrv_profile_names = ["HRV Snapshot"]\n')

    config = load_config(path=path)

    assert config.resting_hrv_profile_names == ["HRV Snapshot"]


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param('""', id="empty"),
        pytest.param('"   "', id="spaces"),
        pytest.param(r'"\t"', id="tab"),
        pytest.param(r'"\n"', id="newline"),
        pytest.param(r'"\u00a0"', id="non-breaking-space"),
    ],
)
def test_load_config_blank_resting_hrv_profile_entry_is_rejected(tmp_path, entry):
    """A blank entry would match any file whose profile name is empty,
    because matching is exact and case-sensitive."""
    path = _toml(tmp_path, f"resting_hrv_profile_names = [{entry}]\n")

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load_config(path=path)

    # Must be rejected *as a bad value*, not incidentally by `extra="forbid"`
    # -- the latter would pass even with the validator deleted.
    assert any(
        error["loc"][:1] == ("resting_hrv_profile_names",)
        and error["type"] != "extra_forbidden"
        for error in exc_info.value.errors()
    ), exc_info.value.errors()


def _assert_field_value_error(exc_info):
    assert any(
        error["loc"][:1] == ("resting_hrv_profile_names",)
        and error["type"] != "extra_forbidden"
        for error in exc_info.value.errors()
    ), exc_info.value.errors()


def test_blank_entry_rejected_even_beside_a_valid_one(tmp_path):
    path = _toml(tmp_path, 'resting_hrv_profile_names = ["HRV Snapshot", "  "]\n')

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load_config(path=path)

    _assert_field_value_error(exc_info)


def test_load_config_non_list_resting_hrv_profile_names_is_rejected(tmp_path):
    """A bare string is not a one-element list."""
    path = _toml(tmp_path, 'resting_hrv_profile_names = "HRV Snapshot"\n')

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load_config(path=path)

    _assert_field_value_error(exc_info)


def test_load_config_wrong_element_type_is_rejected(tmp_path):
    path = _toml(tmp_path, "resting_hrv_profile_names = [1]\n")

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load_config(path=path)

    _assert_field_value_error(exc_info)


def test_resting_hrv_profile_names_is_declared_last():
    """Field-definition order is load-bearing: pydantic v2 orders errors by
    it, and ``test_cli_startup`` asserts the ``port`` error renders first."""
    assert list(AppConfig.model_fields)[-1] == "resting_hrv_profile_names"
