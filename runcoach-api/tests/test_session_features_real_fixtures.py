"""The real running fixtures through ``GET /sessions/{id}/features``, against an independent oracle.

F013 AC1 and AC7; F014 AC3, AC4 and AC5 for the two hilly runs.

Every expected value here was authored away from the module under test.

**The GAP ratio table** (``ORACLE_RATIOS``) was measured at discuss time by a
script that shared no code with the metrics package: it read the fixtures, took
the gradient over +-25 m of distance on the gate-smoothed altitude, and priced
each segment with Minetti's polynomial. The rows for ``dev_fields_run``,
``sample_run``, ``strap_run_hrv`` and ``wrist_ppg_run`` come from the F013
reference, measured before the metrics package existed, and the critic
reproduced them independently to within 0.0004. The rows for the two hilly
runs come from the F014 reference (section 2), whose oracle script was written
from the F013 reference alone and reproduced the ``sample_run`` row and the
ascent figures as a calibration. The band is +-0.005, and the figures are not
as-built: the table is not edited to fit the code, and the code is not edited
to fit the table. A miss is a finding, reported, never absorbed.

**The hysteresis oracle** is restated in this file (``_hysteresis``), from the
rule in the F013 reference, and run over the altitudes ``GET /sessions/{id}``
returns in ``t`` order. Nothing is imported from ``metrics.descriptors``; the
1 m threshold is a literal here. The vendor ``total_ascent`` from the session
summary is printed beside the served value as evidence that it was not read,
and so are the discuss-time figures (``DISCUSS_TIME_ASCENT_M``); neither is
asserted on.

**What this table holds constant.** Every ratio fixture is a 1 Hz recording
from the same watch family, an outdoor run, with barometric altitude quantized
at 0.2 m. The chest strap is a Garmin HRM-Pro Plus on all but two: a Polar
strap (with a Stryd) on ``dev_fields_run``'s FR955, and the second device mix,
an FR945 LTE with a Dynastream OEM axh01 HR strap, on
``hilly_long_run_17k_fr945``. The two hilly runs were recorded with Smart selected, but the
watch wrote 1 Hz, which ``test_hilly_runs_were_recorded_at_one_hertz`` pins, so
the table still says nothing about smart recording, treadmill sessions, or
other devices.

**Time and distance against the watch** (F014 AC5) cover the two hilly runs
only. The vendor ``total_timer_time`` and ``total_distance`` are read with
fitdecode from the session message, never from ``GET /sessions/{id}``, whose
summary holds those vendor values themselves.

The tests print the slice they compared (ratio, ascent, descent, duration,
distance) so the acceptance probe's ``-rA`` output shows the measured figures,
not an exit code alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"

# GAP/raw at +-25 m, measured away from the code: the first four before the build (F013 reference,
# "Measured on the fixture corpus"), the two hilly runs at F014 discuss time (F014 reference, section 2).
ORACLE_RATIOS = {
    "dev_fields_run.fit": 1.0132,
    "sample_run.fit": 1.0540,
    "strap_run_hrv.fit": 1.0038,
    "wrist_ppg_run.fit": 1.0817,
    "hilly_run_8k_fr945.fit": 1.0547,
    "hilly_long_run_17k_fr945.fit": 1.0230,
}
RATIO_TOLERANCE = 0.005

# The discuss-time hysteresis ascent on the same ratio fixtures, from the same two references:
# printed as evidence, never asserted.
DISCUSS_TIME_ASCENT_M = {
    "dev_fields_run.fit": 92.0,
    "sample_run.fit": 199.5,
    "strap_run_hrv.fit": 60.4,
    "wrist_ppg_run.fit": 175.9,
    "hilly_run_8k_fr945.fit": 198.5,
    "hilly_long_run_17k_fr945.fit": 197.7,
}

# The two hilly runs (F014): recording mode, and time and distance against the watch's own totals.
HILLY_FIXTURES = ("hilly_long_run_17k_fr945.fit", "hilly_run_8k_fr945.fit")
DURATION_TOLERANCE_S = 1.0
DISTANCE_RELATIVE_TOLERANCE = 0.001

# A 108 m capture on a running profile whose window finds a grade on about half its distance.
PARTIAL_COVERAGE_FIXTURE = "strap_hrv_sample_run.fit"
PARTIAL_COVERAGE = 0.518
PARTIAL_COVERAGE_TOLERANCE = 0.02

SHAPE_FIXTURE = "sample_run.fit"
ENV_FEATURES = ("env_temperature_c", "env_humidity_pct", "env_wind_ms")
FEATURE_NAMES = (
    "duration_s",
    "distance_m",
    "avg_pace_s_per_km",
    "gap_avg_pace_s_per_km",
    "ngp_speed_m_s",
    "ngp_pace_s_per_km",
    "avg_hr_bpm",
    "avg_cadence_spm",
    "avg_power_w",
    "total_ascent_m",
    "total_descent_m",
    *ENV_FEATURES,
)

# The hysteresis rule as the reference states it: the reference altitude moves only on a 1 m step.
HYSTERESIS_THRESHOLD_M = 1.0
HYSTERESIS_ABS_TOLERANCE = 1e-9

RATIO_FIXTURES = sorted(ORACLE_RATIOS)


def _hysteresis(altitudes: list[float]) -> tuple[float, float]:
    """Total ascent and descent over ``altitudes`` in order, restated from the reference.

    Keep a reference altitude starting at the first sample. For each later
    sample ``a``: if ``a - ref >= 1.0`` add ``a - ref`` to ascent and move
    ``ref`` to ``a``; if ``ref - a >= 1.0`` add ``ref - a`` to descent and move
    ``ref`` to ``a``. Anything closer than 1 m leaves both totals alone.
    """
    ascent = 0.0
    descent = 0.0
    ref = altitudes[0]
    for a in altitudes[1:]:
        if a - ref >= HYSTERESIS_THRESHOLD_M:
            ascent += a - ref
            ref = a
        elif ref - a >= HYSTERESIS_THRESHOLD_M:
            descent += ref - a
            ref = a
    return ascent, descent


@contextmanager
def _client() -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def _upload(client: TestClient, filename: str) -> str:
    created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _features(client: TestClient, session_id: str) -> dict:
    served = client.get(f"/sessions/{session_id}/features")
    assert served.status_code == 200, served.text
    return served.json()


def _detail(client: TestClient, session_id: str) -> dict:
    detail = client.get(f"/sessions/{session_id}")
    assert detail.status_code == 200, detail.text
    return detail.json()


def _value(body: dict, name: str) -> float:
    feature = body["features"][name]
    assert feature["unavailable"] is None, (name, feature)
    assert feature["value"] is not None, (name, feature)
    return feature["value"]


# ---------------------------------------------------------------------------
# AC1: the sample_run response shape
# ---------------------------------------------------------------------------


def test_sample_run_serves_every_feature_but_the_environment() -> None:
    """``sample_run.fit``: 200, a value for every feature but the three ``env_*`` (``not_recorded``),
    ``garmin_native`` power and ``gap_coverage`` 1.0."""
    with _client() as client:
        body = _features(client, _upload(client, SHAPE_FIXTURE))

    assert body["sport"] == "running"
    for name in FEATURE_NAMES:
        feature = body["features"][name]
        if name in ENV_FEATURES:
            assert feature == {"value": None, "unavailable": "not_recorded"}, (name, feature)
        else:
            assert feature["unavailable"] is None, (name, feature)
            assert isinstance(feature["value"], (int, float)), (name, feature)
    assert body["features"]["avg_power_w"]["power_model"] == "garmin_native"
    assert body["gap_coverage"] == 1.0
    print(f"{SHAPE_FIXTURE}: gap_coverage={body['gap_coverage']} power_model=garmin_native")


# ---------------------------------------------------------------------------
# AC1 (and F014 AC4 for the hilly runs): every GAP ratio against the discuss-time oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", RATIO_FIXTURES)
def test_gap_ratio_matches_the_independent_oracle(fixture: str) -> None:
    """``avg_pace / gap_avg_pace`` lies within +-0.005 of the ratio measured before the build."""
    with _client() as client:
        body = _features(client, _upload(client, fixture))

    ratio = _value(body, "avg_pace_s_per_km") / _value(body, "gap_avg_pace_s_per_km")
    expected = ORACLE_RATIOS[fixture]
    print(f"{fixture}: ratio={ratio:.4f} oracle={expected:.4f} band=+-{RATIO_TOLERANCE}")
    assert abs(ratio - expected) <= RATIO_TOLERANCE, (
        f"{fixture}: computed ratio {ratio:.4f} misses the oracle {expected:.4f} +- {RATIO_TOLERANCE}"
    )


# ---------------------------------------------------------------------------
# AC7: ascent and descent from the hysteresis restated here, never the vendor total
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", RATIO_FIXTURES)
def test_ascent_and_descent_follow_the_hysteresis_restated_here(fixture: str) -> None:
    """Served ascent and descent equal the 1 m hysteresis over the detail's altitudes.

    The altitudes are taken in ``t`` order; the comparison is to 1e-9.
    """
    with _client() as client:
        session_id = _upload(client, fixture)
        body = _features(client, session_id)
        detail = _detail(client, session_id)

    records = sorted(detail["records"], key=lambda r: r["t"])
    altitudes = [r["altitude"] for r in records if r["altitude"] is not None]
    assert len(altitudes) >= 2, f"{fixture}: too few altitude samples for the oracle to mean anything"
    expected_ascent, expected_descent = _hysteresis(altitudes)

    served_ascent = _value(body, "total_ascent_m")
    served_descent = _value(body, "total_descent_m")
    vendor_ascent = detail["summary"].get("total_ascent_m")
    print(
        f"{fixture}: ascent served={served_ascent:.3f} oracle={expected_ascent:.3f} "
        f"vendor_total_ascent={vendor_ascent} discuss_time={DISCUSS_TIME_ASCENT_M[fixture]}; "
        f"descent served={served_descent:.3f} oracle={expected_descent:.3f}"
    )
    assert abs(served_ascent - expected_ascent) <= HYSTERESIS_ABS_TOLERANCE, (
        fixture,
        served_ascent,
        expected_ascent,
    )
    assert abs(served_descent - expected_descent) <= HYSTERESIS_ABS_TOLERANCE, (
        fixture,
        served_descent,
        expected_descent,
    )


# ---------------------------------------------------------------------------
# coverage on the short capture the window only half grades
# ---------------------------------------------------------------------------


def test_strap_hrv_sample_run_coverage_is_partial() -> None:
    """``strap_hrv_sample_run.fit``: ``gap_coverage`` 0.518 +- 0.02, with ``gap_unavailable`` raised."""
    with _client() as client:
        body = _features(client, _upload(client, PARTIAL_COVERAGE_FIXTURE))

    coverage = body["gap_coverage"]
    print(
        f"{PARTIAL_COVERAGE_FIXTURE}: gap_coverage={coverage} "
        f"oracle={PARTIAL_COVERAGE} band=+-{PARTIAL_COVERAGE_TOLERANCE}"
    )
    assert coverage is not None
    assert abs(coverage - PARTIAL_COVERAGE) <= PARTIAL_COVERAGE_TOLERANCE, (coverage, PARTIAL_COVERAGE)
    assert "gap_unavailable" in body["flags"], body["flags"]


# ---------------------------------------------------------------------------
# F014 AC3: the hilly runs were written at 1 Hz, though Smart was selected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", HILLY_FIXTURES)
def test_hilly_runs_were_recorded_at_one_hertz(fixture: str) -> None:
    """``GET /sessions/{id}`` gives ``recording_interval == "1hz"`` and no ``smart_recording`` flag."""
    with _client() as client:
        detail = _detail(client, _upload(client, fixture))

    interval = detail["recording_interval"]
    flags = detail["quality_flags"]
    print(f"{fixture}: recording_interval={interval} quality_flags={flags}")
    assert interval == "1hz", (fixture, interval)
    assert "smart_recording" not in flags, (fixture, flags)


# ---------------------------------------------------------------------------
# F014 AC5: time and distance against the watch's own session totals
# ---------------------------------------------------------------------------


def _vendor_session_totals(fixture: str) -> dict:
    """The one ``session`` message's totals, decoded with fitdecode and nothing from ``runcoach_api``."""
    sessions = []
    with fitdecode.FitReader(str(FIXTURES / fixture)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "session":
                sessions.append({fd.name: fd.value for fd in frame.fields})
    assert len(sessions) == 1, (fixture, len(sessions))
    return sessions[0]


@pytest.mark.parametrize("fixture", HILLY_FIXTURES)
def test_hilly_run_duration_and_distance_match_the_watch(fixture: str) -> None:
    """Served duration within 1.0 s of ``total_timer_time``; served distance within 0.1 % of
    ``total_distance``. ``hilly_run_8k_fr945``'s auto-pause must be excluded, as the timer excludes it.

    The vendor ``total_ascent`` is printed beside the served ascent, never asserted. Both files also
    grade every metre (``gap_coverage`` 1.0) and clamp none (``grade_clamped_fraction`` 0.0).
    """
    vendor = _vendor_session_totals(fixture)
    with _client() as client:
        body = _features(client, _upload(client, fixture))

    timer_s = vendor["total_timer_time"]
    vendor_distance_m = vendor["total_distance"]
    duration_s = _value(body, "duration_s")
    distance_m = _value(body, "distance_m")
    distance_rel = (distance_m - vendor_distance_m) / vendor_distance_m
    print(
        f"{fixture}: duration served={duration_s:.3f} total_timer_time={timer_s:.3f} "
        f"diff={duration_s - timer_s:+.3f}s band=+-{DURATION_TOLERANCE_S}s; "
        f"distance served={distance_m:.2f} total_distance={vendor_distance_m:.2f} "
        f"diff={distance_rel:+.4%} band=+-{DISTANCE_RELATIVE_TOLERANCE:.1%}; "
        f"ascent served={_value(body, 'total_ascent_m'):.1f} vendor_total_ascent={vendor.get('total_ascent')}; "
        f"gap_coverage={body['gap_coverage']} grade_clamped_fraction={body['grade_clamped_fraction']}"
    )
    assert abs(duration_s - timer_s) <= DURATION_TOLERANCE_S, (fixture, duration_s, timer_s)
    assert abs(distance_rel) <= DISTANCE_RELATIVE_TOLERANCE, (fixture, distance_m, vendor_distance_m)
    assert body["gap_coverage"] == 1.0, (fixture, body["gap_coverage"])
    assert body["grade_clamped_fraction"] == 0.0, (fixture, body["grade_clamped_fraction"])
