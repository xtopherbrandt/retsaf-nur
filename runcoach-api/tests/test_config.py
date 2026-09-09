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
    _assert_field_value_error(exc_info)


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


def test_port_precedes_the_remediation_bearing_fields():
    """Field-definition order is load-bearing: pydantic v2 orders errors by
    it, and ``cli.serve()`` renders only ``errors()[0]``.

    Rewritten by T088 from "``resting_hrv_profile_names`` is declared last"
    into the invariant that assertion actually protected: ``test_cli_startup``
    builds a real error from an out-of-range ``port`` and asserts "port"
    reaches stderr, so ``port`` must precede every field whose missing-value
    remediation the CLI renders. The second half pins F005's "declared last"
    note: a pre-amendment ``api.toml`` -- missing both ``resting_hrv_profile_names``
    and ``athlete_timezone`` -- must keep receiving the profile-names remediation
    first (``test_cli_startup.py:259``) until T081 removes the order-dependence.
    """
    order = list(AppConfig.model_fields)
    remediation_bearing = ("resting_hrv_profile_names", "athlete_timezone")

    for name in remediation_bearing:
        assert order.index("port") < order.index(name), order
    assert order[-1] == "athlete_timezone", order


# ---------------------------------------------------------------------------
# athlete_timezone -- required, no default (F005, T088)
#
# The IANA zone every local-day bucket in the resting-HRV trend is computed
# in. F001's ratified no-defaults rule applies with unusual force here: a
# silent ``"UTC"`` would bucket a 06:00 capture at UTC+13 onto the previous
# day and shift which readings fall in the 7-day window, with no error. The
# zone is validated at config load (``validate_zone``, T078), never per
# request, so an unresolvable zone is a startup failure rather than a 500.
#
# Every test that asserts the field is *missing* clears
# ``RUNCOACH_ATHLETE_TIMEZONE`` first: env overrides the file, so a developer
# with the variable exported would otherwise pass vacuously
# (``test_cli_startup.py``'s ``delenv`` precedent).
# ---------------------------------------------------------------------------

_ATHLETE_TIMEZONE_ENV = "RUNCOACH_ATHLETE_TIMEZONE"


def test_athlete_timezone_is_required_and_has_no_default(monkeypatch):
    """Constructing without ``athlete_timezone`` is a ``missing`` error."""
    monkeypatch.delenv(_ATHLETE_TIMEZONE_ENV, raising=False)

    with pytest.raises(pydantic.ValidationError) as exc_info:
        AppConfig(
            host="127.0.0.1",
            port=8000,
            data_dir=Path("/tmp/data"),
            resting_hrv_profile_names=[],
        )

    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("athlete_timezone",) and error["type"] == "missing"
        for error in errors
    ), errors
    assert AppConfig.model_fields["athlete_timezone"].is_required()


def test_load_config_without_athlete_timezone_is_rejected(tmp_path, monkeypatch):
    """A pre-F005 ``api.toml`` fails to load rather than assuming a zone."""
    monkeypatch.delenv(_ATHLETE_TIMEZONE_ENV, raising=False)
    path = _toml(tmp_path, "resting_hrv_profile_names = []\n")

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load_config(path=path)

    assert any(
        error["loc"] == ("athlete_timezone",) and error["type"] == "missing"
        for error in exc_info.value.errors()
    ), exc_info.value.errors()


def test_load_config_valid_athlete_timezone_is_accepted(tmp_path, monkeypatch):
    monkeypatch.delenv(_ATHLETE_TIMEZONE_ENV, raising=False)
    path = _toml(
        tmp_path,
        'resting_hrv_profile_names = []\nathlete_timezone = "Pacific/Auckland"\n',
    )

    config = load_config(path=path)

    assert config.athlete_timezone == "Pacific/Auckland"


def test_load_config_invalid_athlete_timezone_is_rejected_at_load(tmp_path, monkeypatch):
    """An unrecognised zone is a ``value_error`` naming the field, at load.

    Rejected *as a bad value* -- ``type == "value_error"`` -- so the test
    cannot be satisfied by ``extra="forbid"`` with the field deleted, nor by
    a validator that lets ``ZoneInfoNotFoundError`` (a ``KeyError``) escape as
    an internal error with no field attached.
    """
    monkeypatch.delenv(_ATHLETE_TIMEZONE_ENV, raising=False)
    path = _toml(
        tmp_path,
        'resting_hrv_profile_names = []\nathlete_timezone = "Mars/Phobos"\n',
    )

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load_config(path=path)

    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("athlete_timezone",) and error["type"] == "value_error"
        for error in errors
    ), errors
    assert "Mars/Phobos" in str(exc_info.value)
    assert "IANA" in str(exc_info.value)


def test_athlete_timezone_env_overrides_the_file(tmp_path, monkeypatch):
    """``RUNCOACH_ATHLETE_TIMEZONE`` wins over the TOML value, like every
    other field; the F005 demo probe relies on exactly this."""
    monkeypatch.setenv(_ATHLETE_TIMEZONE_ENV, "Asia/Kolkata")
    path = _toml(
        tmp_path,
        'resting_hrv_profile_names = []\nathlete_timezone = "Pacific/Auckland"\n',
    )

    config = load_config(path=path)

    assert config.athlete_timezone == "Asia/Kolkata"
