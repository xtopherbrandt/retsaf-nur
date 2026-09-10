"""T078 -- the shared IANA zone validator (F005).

`validate_zone(name)` is the one place a configured `athlete_timezone` is
turned into a `ZoneInfo`. It exists because `zoneinfo.ZoneInfoNotFoundError`
subclasses `KeyError` and is raised *identically* for "not a real zone" and
"no time-zone database installed" (Windows stdlib ships no tzdb; the venv
had zero available zones before this task added `tzdata`). A validator that
merely caught it would call `Pacific/Auckland` invalid on a tzdb-less host,
and F005's "an invalid timezone fails at load" criterion would be satisfied
by a check that is wrong in exactly the case it exists for (risk R2).

The tzdb-less branch is unreachable on Linux/macOS, where the stdlib finds
`/usr/share/zoneinfo`, so it is driven by a mock. Per
`.claude/rules/project-testing.md` the mock was checked against the vendored
source before being trusted: `zoneinfo/_tzpath.py::available_timezones`
returns an empty set when neither the `tzdata` package nor any `TZPATH`
root exists, and `zoneinfo/_common.py::load_tzdata` raises
`ZoneInfoNotFoundError` when the `tzdata.zoneinfo` package cannot be
imported. Those two behaviours are what the mock reproduces.
"""

from __future__ import annotations

import zoneinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from runcoach_api.config import validate_zone


def test_a_valid_zone_resolves() -> None:
    """The canary: red until both the helper and a tzdb exist in the venv."""
    zone = validate_zone("Pacific/Auckland")

    assert isinstance(zone, ZoneInfo)
    assert zone.key == "Pacific/Auckland"


def test_a_half_hour_offset_zone_resolves() -> None:
    zone = validate_zone("Asia/Kolkata")

    assert isinstance(zone, ZoneInfo)
    assert zone.key == "Asia/Kolkata"


def test_an_unknown_zone_is_rejected_as_a_value_error() -> None:
    """A `ValueError`, never a bare `KeyError`.

    Inside a pydantic `@field_validator` a `ValueError` surfaces as
    `type='value_error'` naming the field; a `KeyError` escaping would
    surface as an internal error with no field attached.
    """
    with pytest.raises(ValueError) as excinfo:
        validate_zone("Mars/Phobos")

    assert not isinstance(excinfo.value, KeyError)
    assert "Mars/Phobos" in str(excinfo.value)
    assert "IANA" in str(excinfo.value)


def test_a_missing_tzdb_is_reported_differently_from_a_bad_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a host with no tzdb at all, a *valid* zone must not be called invalid.

    The mock reproduces a tzdb-less host as measured against the vendored
    stdlib (see the module docstring): `available_timezones()` is empty and
    `ZoneInfo(...)` raises `ZoneInfoNotFoundError` for every key.
    """
    with pytest.raises(ValueError) as bad_zone:
        validate_zone("Mars/Phobos")

    def _no_tzdb(key: str) -> ZoneInfo:
        raise ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(zoneinfo, "available_timezones", lambda: set())
    monkeypatch.setattr(zoneinfo, "ZoneInfo", _no_tzdb)

    with pytest.raises(ValueError) as missing_tzdb:
        validate_zone("Pacific/Auckland")

    assert not isinstance(missing_tzdb.value, KeyError)
    assert str(missing_tzdb.value) != str(bad_zone.value)
    # The installation fault names itself, and does not blame the zone.
    assert "tzdata" in str(missing_tzdb.value)
    assert "not a recognised" not in str(missing_tzdb.value)
    assert "not a recognised" in str(bad_zone.value)


# ---------------------------------------------------------------------------
# the canonical-key rows (sprint-005 review, M3): what the host's filesystem
# accepts is not what the tz database names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Pacific/Auckland ", "utc", "", "../Etc/UTC"],
    ids=["trailing-space", "lower-case", "empty", "path-like"],
)
def test_a_non_canonical_key_is_rejected_with_the_validators_wording(name: str) -> None:
    """`ZoneInfo(name)` is a file lookup under the tzdb root, so on NTFS a
    trailing space is stripped and the lookup is case-insensitive -- both keys
    resolve here and fail on Linux -- while `''` and a path-like key raise
    `zoneinfo`'s own `ValueError`, bypassing both of the validator's messages.
    Every one must fail with the validator's wording, on every host."""
    with pytest.raises(ValueError) as excinfo:
        validate_zone(name)

    assert not isinstance(excinfo.value, KeyError)
    assert "not a recognised IANA time zone" in str(excinfo.value)
    assert repr(name) in str(excinfo.value)


@pytest.mark.parametrize("name", ["EST", "Etc/GMT+5"])
def test_a_legacy_but_canonical_key_is_accepted(name: str) -> None:
    """The canonical set is `available_timezones()`, which names the legacy
    keys too; the check must not be stricter than the tz database."""
    zone = validate_zone(name)

    assert isinstance(zone, ZoneInfo)
    assert zone.key == name
