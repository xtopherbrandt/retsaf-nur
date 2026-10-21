"""``ingestion.overnight.map_night``: an HRV_STATUS file is a night or ``no_night``.

The real fixtures under ``fixtures/nights/`` (provenance rows in
``fixtures/README.md``: real nights from the wellness FIT, run proof "no")
pin the mapping; every number below is read from a decode of the fixture
and written as a literal, so the test cannot agree with itself. The faults
the export lacks (an average of 0, negative or non-finite; a truncated
series; two values at one timestamp; a value with no timestamp; two
summaries) are built from ``night_recent.fit`` with ``support/fit_patch.py``.

The mapper decodes through ``fit_parser.decode``, so a truncated file and
an out-of-range ``date_time`` are ``FitParseFailure`` (a 400) here as on
every other route. Nothing here touches the store: the mapper returns a
``Night`` or a ``NoNight`` and writes nothing.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

from runcoach_api.ingestion import overnight
from runcoach_api.ingestion.exceptions import (
    FitParseFailure,
    MissingCanonicalFieldError,
    OvernightSeriesError,
    http_status_detail,
)
from runcoach_api.ingestion.mapping import derive_session_id

NIGHTS = Path(__file__).parent / "fixtures" / "nights"
SUPPORT = Path(__file__).parent / "support"

# FIT global message numbers and field numbers (fitdecode's profile): the
# summary's ``last_night_average`` is field 1, every message's timestamp is
# field 253, an ``hrv_value``'s value is field 0.
HRV_STATUS_SUMMARY, HRV_VALUE = 370, 371
LAST_NIGHT_AVERAGE, TIMESTAMP, VALUE = 1, 253, 0
# FIT base types: a signed 16-bit integer and a 32-bit float.
SINT16, FLOAT32 = 0x83, 0x88
BEYOND_TIME_T = (2**40, 2**63 + 5)


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


def _night(name: str) -> bytes:
    return (NIGHTS / f"{name}.fit").read_bytes()


def _recent() -> bytes:
    return _night("night_recent")


# ---------------------------------------------------------------------------
# a real night maps to the reference columns
# ---------------------------------------------------------------------------


def test_a_recent_night_maps_to_the_reference_columns() -> None:
    night = overnight.map_night(_recent())

    assert isinstance(night, overnight.Night)
    session = night.session
    assert session.start_time == "2026-10-10T11:33:29+00:00"
    assert session.sport == "other"
    assert session.activity_tag == "overnight_hrv"
    assert session.name == "Overnight HRV"
    assert session.source_vendor == "garmin"
    assert session.source_device == "fr945_lte fw17.4"
    assert session.session_id == derive_session_id("fr945_lte fw17.4", "2026-10-10T11:33:29+00:00")
    assert session.hr_source == "wrist_ppg"
    assert session.hr_sensor_serial is None
    assert session.rmssd_precomputed == 43.0
    assert session.resting_rmssd_ms == 43.0
    assert session.hrv_source_tier == "health_api_overnight"
    assert session.rr_source == "overnight_ppg"
    assert session.quality_flags == []
    assert session.summary == {
        "duration_s": 16800.0,
        "series_start": "2026-10-10T04:16:00+00:00",
        "last_night_5_min_high": 82.0,
        "hrv_values": 57,
        "hrv_values_valid": 57,
    }
    assert session.context is not None
    assert session.context.provenance == {"raw_file_type": 68}

    assert len(night.values) == 57
    assert night.values[0] == ("2026-10-10T04:16:00+00:00", 34.0)
    assert night.values[-1] == ("2026-10-10T08:56:00+00:00", 34.0)
    assert all(isinstance(t, str) and t.endswith("+00:00") for t, _ in night.values)

    assert night.sidecar == {
        "status": "balanced",
        "weekly_average": "40.0",
        "baseline_low_upper": "34.0",
        "baseline_balanced_lower": "36.0",
        "baseline_balanced_upper": "46.0",
    }


def test_the_short_night_is_stored_as_is() -> None:
    night = overnight.map_night(_night("night_short"))

    assert isinstance(night, overnight.Night)
    assert night.session.start_time == "2024-05-31T12:37:24+00:00"
    assert night.session.resting_rmssd_ms == 57.0
    assert night.session.summary["hrv_values"] == 39
    assert night.session.summary["duration_s"] == 11400.0
    assert night.session.summary["series_start"] == "2024-05-31T09:15:48+00:00"
    assert night.session.summary["last_night_5_min_high"] == 65.0
    assert night.session.quality_flags == []


def test_the_resummary_pair_shares_its_series_start_and_differs_in_summary_time() -> None:
    earlier = overnight.map_night(_night("night_resummary_earlier"))
    later = overnight.map_night(_night("night_resummary_later"))

    assert isinstance(earlier, overnight.Night) and isinstance(later, overnight.Night)
    assert earlier.session.start_time == "2024-01-01T14:52:53+00:00"
    assert later.session.start_time == "2024-01-01T15:30:53+00:00"
    assert earlier.session.summary["series_start"] == later.session.summary["series_start"]
    assert earlier.session.summary["series_start"] == "2024-01-01T06:03:39+00:00"
    assert earlier.session.source_device == later.session.source_device == "fr945_lte fw16.18"
    assert earlier.session.session_id != later.session.session_id
    assert earlier.session.resting_rmssd_ms == later.session.resting_rmssd_ms == 39.0
    assert (earlier.session.summary["hrv_values"], earlier.session.summary["hrv_values_valid"]) == (106, 104)
    assert (later.session.summary["hrv_values"], later.session.summary["hrv_values_valid"]) == (104, 102)
    assert earlier.session.summary["duration_s"] == 31500.0
    assert later.session.summary["duration_s"] == 30900.0


# ---------------------------------------------------------------------------
# no_night: no summary, or an average that is absent, 0, negative or non-finite
# ---------------------------------------------------------------------------


def test_midnight_and_lone_morning_files_are_no_night() -> None:
    for name in ("night_midnight_no_average", "night_lone_morning_no_average"):
        outcome = overnight.map_night(_night(name))

        assert isinstance(outcome, overnight.NoNight), name
        assert outcome.reason == "no_night"


def test_a_file_with_no_summary_is_no_night() -> None:
    outcome = overnight.map_night(fit_patch.remove_messages(_recent(), HRV_STATUS_SUMMARY))

    assert outcome == overnight.NoNight(reason="no_night")


def _average_set_to(raw_uint16: int) -> bytes:
    return fit_patch.set_field(_recent(), HRV_STATUS_SUMMARY, LAST_NIGHT_AVERAGE, raw_uint16)


def _average_retyped(base_type: int, value: int | float) -> bytes:
    return fit_patch.retype_field(_recent(), HRV_STATUS_SUMMARY, LAST_NIGHT_AVERAGE, base_type, value)


def _average_float_bits(bits: int) -> bytes:
    """The average declared ``float32`` and holding exactly ``bits``."""
    retyped = _average_retyped(FLOAT32, 1.0)
    return fit_patch.set_field(retyped, HRV_STATUS_SUMMARY, LAST_NIGHT_AVERAGE, bits)


@pytest.mark.parametrize(
    ("label", "build"),
    [
        ("uint16 invalid sentinel (absent)", lambda: _average_set_to(0xFFFF)),
        ("zero", lambda: _average_set_to(0)),
        ("sint16 -128 (-1.0 ms)", lambda: _average_retyped(SINT16, -128)),
        ("sint16 -1 (-0.0078 ms)", lambda: _average_retyped(SINT16, -1)),
        ("float32 -0.0", lambda: _average_retyped(FLOAT32, -0.0)),
        ("float32 +inf", lambda: _average_retyped(FLOAT32, math.inf)),
        ("float32 -inf", lambda: _average_retyped(FLOAT32, -math.inf)),
        ("float32 inf bits 0x7F800000", lambda: _average_float_bits(0x7F800000)),
        ("float32 invalid sentinel 0xFFFFFFFF", lambda: _average_float_bits(0xFFFFFFFF)),
        ("float32 non-sentinel NaN 0x7FC00001", lambda: _average_float_bits(0x7FC00001)),
        ("float32 packed NaN", lambda: _average_retyped(FLOAT32, math.nan)),
    ],
)
def test_zero_negative_and_non_finite_averages_are_no_night(label: str, build) -> None:
    outcome = overnight.map_night(build())

    assert outcome == overnight.NoNight(reason="no_night"), label


def test_a_retyped_finite_positive_average_still_maps() -> None:
    # The retype itself is not what makes the files above no_night: the same
    # declaration holding a positive finite value is a night of that value.
    night = overnight.map_night(_average_retyped(FLOAT32, 5504.0))

    assert isinstance(night, overnight.Night)
    assert night.session.resting_rmssd_ms == 43.0


# ---------------------------------------------------------------------------
# the series: nulls counted, a value with no timestamp dropped, two at one
# timestamp refused
# ---------------------------------------------------------------------------


def test_an_invalid_value_is_kept_as_null_and_counted() -> None:
    night = overnight.map_night(fit_patch.blank_field_at(_recent(), HRV_VALUE, 10, VALUE))

    assert isinstance(night, overnight.Night)
    assert night.values[10] == ("2026-10-10T05:06:00+00:00", None)
    assert night.values[9] == ("2026-10-10T05:01:00+00:00", 50.0)
    assert night.session.summary["hrv_values"] == 57
    assert night.session.summary["hrv_values_valid"] == 56
    assert night.session.summary["duration_s"] == 16800.0


def test_a_non_finite_series_value_is_kept_as_null() -> None:
    night = overnight.map_night(fit_patch.retype_field(_recent(), HRV_VALUE, VALUE, FLOAT32, math.inf))

    assert isinstance(night, overnight.Night)
    assert len(night.values) == 57
    assert all(value is None for _, value in night.values)
    assert night.session.summary["hrv_values"] == 57
    assert night.session.summary["hrv_values_valid"] == 0


def test_a_value_with_no_timestamp_is_dropped() -> None:
    night = overnight.map_night(fit_patch.blank_field_at(_recent(), HRV_VALUE, 10, TIMESTAMP))

    assert isinstance(night, overnight.Night)
    assert len(night.values) == 56
    assert night.values[9] == ("2026-10-10T05:01:00+00:00", 50.0)
    assert night.values[10] == ("2026-10-10T05:11:00+00:00", 69.0)
    assert night.session.summary["hrv_values"] == 56
    assert night.session.summary["hrv_values_valid"] == 56
    assert night.session.summary["duration_s"] == 16800.0


def test_every_value_without_a_timestamp_leaves_a_night_with_no_series() -> None:
    night = overnight.map_night(fit_patch.blank_field_all(_recent(), HRV_VALUE, TIMESTAMP))

    assert isinstance(night, overnight.Night)
    assert night.values == []
    assert night.session.resting_rmssd_ms == 43.0
    assert night.session.summary == {
        "duration_s": 0.0,
        "series_start": None,
        "last_night_5_min_high": 82.0,
        "hrv_values": 0,
        "hrv_values_valid": 0,
    }


def test_a_summary_only_file_is_a_night_with_no_series() -> None:
    night = overnight.map_night(fit_patch.remove_messages(_recent(), HRV_VALUE))

    assert isinstance(night, overnight.Night)
    assert night.values == []
    assert night.session.summary["series_start"] is None
    assert night.session.summary["duration_s"] == 0.0
    assert night.session.summary["hrv_values"] == 0


def test_two_values_at_one_timestamp_refuse_the_file() -> None:
    with pytest.raises(OvernightSeriesError, match="2026-10-10T05:01:00\\+00:00"):
        overnight.map_night(fit_patch.duplicate_timestamp(_recent(), HRV_VALUE, 10))


def test_a_byte_copied_value_refuses_the_file_too() -> None:
    with pytest.raises(OvernightSeriesError):
        overnight.map_night(fit_patch.duplicate_message(_recent(), HRV_VALUE, 10))


def test_a_second_summary_refuses_the_file() -> None:
    with pytest.raises(OvernightSeriesError, match="2 hrv_status_summary"):
        overnight.map_night(fit_patch.duplicate_message(_recent(), HRV_STATUS_SUMMARY, 0))


def test_a_summary_with_no_timestamp_has_no_start_time() -> None:
    with pytest.raises(MissingCanonicalFieldError) as caught:
        overnight.map_night(fit_patch.blank_field_at(_recent(), HRV_STATUS_SUMMARY, 0, TIMESTAMP))

    assert caught.value.field == "start_time"


def test_the_series_error_is_a_400() -> None:
    status, detail = http_status_detail(OvernightSeriesError("two hrv_value messages at t"))

    assert status == 400
    assert "two hrv_value messages at t" in detail


# ---------------------------------------------------------------------------
# faults the decoder refuses before the mapper sees a message
# ---------------------------------------------------------------------------


def test_a_file_truncated_inside_the_series_refuses() -> None:
    with pytest.raises(FitParseFailure):
        overnight.map_night(fit_patch.truncate_inside(_recent(), HRV_VALUE, 30))


@pytest.mark.parametrize("value", BEYOND_TIME_T)
def test_a_widened_value_timestamp_refuses(value: int) -> None:
    widened = fit_patch.widen_to_uint64(_recent(), HRV_VALUE, TIMESTAMP)
    raw = fit_patch.set_field(widened, HRV_VALUE, TIMESTAMP, value)

    with pytest.raises(FitParseFailure, match="date_time"):
        overnight.map_night(raw)


def test_bytes_that_are_not_a_fit_file_are_refused_as_today() -> None:
    from runcoach_api.ingestion.exceptions import NotAFitFileError

    with pytest.raises(NotAFitFileError):
        overnight.map_night(b"not a fit file at all, not even close")
