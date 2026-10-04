"""The adversarial probe table for the session features, driven through upload (F013 AC12, and the
upload clauses of AC4 to AC8).

Every row of the F013 reference's probe seed (section 10) is a synthetic session pushed through
the real path, ``POST /sessions`` -> ``mapping.to_canonical`` -> ``quality_gates.apply`` ->
``db.persist`` -> ``GET /sessions/{id}/features``, and read back through the route. Each row's
expected outcome is restated here from the reference and from the user's rulings (the AC2
boundary is strict, so 0.45 is in range; NGP leaves out dt = 0 records; a power sample with a
null model still counts); none was derived by running the module. Where a row compares two
uploads (rows 5, 6, 7 and 20) the second carries a start time one hour later, because
``(source_device, start_time)`` is unique.

**Only the decoder is replaced.** ``pipeline.fit_parser.decode`` is monkeypatched to return a
list of message stand-ins, because the repository has no FIT encoder. That replaces I/O, not the
behaviour under test, so ``project-testing.md``'s source check for load-bearing mocks does not
apply: mapping, the gates, persistence, the reader and the pure module all run for real.
``_UploadMsg`` is a local stand-in rather than ``conftest``'s ``_FakeMsg`` because
``quarantine.extract`` calls ``message.has_field(name)`` on every ``session`` message, which
``_FakeMsg`` lacks, and the hrv suites reason about ``has_field`` semantics on that class.

What the upload path does to the inputs before the module sees them, and why the rows are shaped
as they are: mapping doubles cadence (raw 85 becomes 170 spm); the gate resamples 1-5 s gaps onto
a 1 s grid, tags the record after a longer gap ``interpolation_gap`` and leaves a dt = 0 duplicate
in place; it classifies 2 s spacing as smart recording; it smooths altitude **by index** over 3
samples, which is why rows 6 and 7 use flat altitude (a duplicate or a jump on graded ground would
move neighbouring grades; the graded equality is pinned at the module seam in the assembly tests);
and it tags ``cadence_lock`` on a wrist session where HR sits within 3 of cadence for 30 or more
consecutive records.

Row 18's bound, derived. At 1 m/s on an unquantized 10 % ramp the gate's 3-sample mean leaves
every interior altitude on the line and pulls the two end records in by half a step (0.05 m).
A segment whose +-25 m window reaches an end record therefore reads ``i = 0.1 - 0.05 / (k + 25)``
with ``k`` the segment index from that end (0 <= k <= 24), so the smallest grade in the session is
``0.1 - 0.05 / 25 = 0.098``; every other segment reads 0.1 to rounding. The distance-weighted mean
g (which is what ``avg_pace / gap_avg_pace`` equals, reference section 5) therefore lies in
``[g(0.098), g(0.10)]``, and g(0.098) is about 1.643, far above the task's ``0.9 * g(0.10)`` of
about 1.492. Both bounds are asserted.

The table. ``returned`` is what the route served under the assertion; ``why intended`` is the
reference rule that makes it the right answer.

| # | input | returned | why intended |
|---|---|---|---|
| 1 | altitude absent on every sample | both paces equal; ``gap_coverage`` 0.0; ``flags == ['gap_unavailable']``; ascent and descent ``no_altitude`` | no window has two altitude samples, so every segment takes g = 1 (section 3's raw-pace fallback); coverage measures it and the flag names it (section 8); hysteresis needs two samples (section 7) |
| 2 | distance absent on every sample | ``distance_m`` and both paces ``no_distance``; ``ngp_speed_m_s`` 3.0; coverage and fractions null; ``flags == []`` | D = 0 is reported as unavailable, never 0.0 (section 7); NGP reads device speed, not distance (section 6); ``gap_unavailable`` is not raised when D = 0 (section 8) |
| 3 | distance constant (treadmill, no footpod) | ``distance_m == {'value': None, 'unavailable': 'no_distance'}``; ``duration_s`` 59.0 | every dd is 0 so D = 0; recorded time is unaffected (section 2) |
| 4 | distance decreases once (87 m to 80 m) | ``flags == ['distance_regressed']``; ``distance_m`` 184.0 = the sum of max(dd, 0) | the regressing segment contributes 0 m and raises the flag; the 13 m segment after it counts in full (section 2) |
| 5 | a 600 s pause (dt = 600, dd = 0) | ``duration_s``, ``distance_m`` and both paces equal the run without it | a segment with dt > 5 s is a break and contributes neither time nor distance (section 2) |
| 6 | a dt = 0 duplicate record, flat altitude | the same four equal the run without it | a dt = 0 segment contributes nothing and is not a break (section 2) |
| 7 | a 52 m one-second distance jump, speed continuous, flat altitude | ``ngp_speed_m_s`` 3.0, equal to the baseline's; ``distance_m`` 319.0, the baseline's 267 plus 52; and a finding: ``gap_coverage`` 264/319 with ``flags == ['gap_unavailable']``, where the baseline has 1.0 and no flag | NGP's series is ``record.speed * g`` (section 6); D is the sum of the raw increments, jump included (section 2 and the Negative Class row); the 55 m stride's +-25 m midpoint window holds no record, so that one stride has no grade and leaves coverage (section 3), which raises the flag (section 8); both paces still agree because g = 1 on flat ground and on the ungraded stride |
| 8 | a session shorter than 30 s (20 records) | ``ngp_speed_m_s`` and ``ngp_pace_s_per_km`` ``no_30s_block`` | a block of n records yields n - 29 full windows; 20 yields none (section 6) |
| 9 | a block of exactly 30 records | ``ngp_speed_m_s`` 3.0 | 30 records yield one full window, which counts (section 6) |
| 10 | a 1-record session | ``duration_s`` ``no_counted_segments``; ``distance_m`` ``no_distance``; coverage null | no segment exists, so T = 0 and D = 0 (sections 2 and 7) |
| 11 | no records | 201 on upload; every feature ``no_records``; coverage null | the second session-wide override (section 7); an all-unavailable session is an answer, not an error (section 8) |
| 12 | HR 0 throughout | ``avg_hr_bpm`` ``no_heart_rate`` | ``heart_rate <= 0`` is absent at the finite screen (section 1) |
| 13 | HR absent throughout | ``avg_hr_bpm`` ``no_heart_rate`` | no HR present on any counted segment (section 7) |
| 14 | an all-``cadence_lock`` wrist session (HR 170, cadence 170 spm, 60 records) | ``avg_hr_bpm`` ``cadence_lock`` | a ``cadence_lock`` sample has no usable HR; when every candidate is excluded the gate's flag is the reason (sections 1 and 7) |
| 15 | cadence 0 throughout | ``avg_cadence_spm`` ``no_cadence`` | only cadence > 0 qualifies (section 7) |
| 16 | ``sport`` other, full streams | ``sport`` ``other``; every feature ``sport_not_running``; coverage null | the first session-wide override, ahead of every row (section 7) |
| 17 | 2 s spacing, no altitude | ``flags == ['smart_recording', 'gap_unavailable']`` | the session flag is copied first, then the derived flags in their order (section 8) |
| 18 | an unquantized 10 % ramp, 400 m at 1 m/s | ``avg_pace / gap_avg_pace`` 1000 / 603.68 = 1.6565, inside ``[g(0.098), g(0.10)]`` = [1.6426, 1.6578]; ``gap_coverage`` 1.0; ``flags == []``; ascent 39.25 m | the ratio is the distance-weighted mean g (section 5); the window bound is derived above; 0.1 is inside Minetti's domain (section 4) |
| 19 | a ramp rising 25 m over 50 m (i = 0.5) | 200; ``flags == ['grade_clamped']``; ``grade_clamped_fraction`` 0.04 (the 10 strides whose +-25 m window overlaps the ramp by more than 45 m); GAP pace 539.9 s/km against 1000 s/km raw | i = 0.5 is above the strict 0.45 boundary, so ``clamp_grade`` clamps and the segment counts (section 4, the user's ruling) |
| 20 | ``gps_accuracy`` 20 on records 30-59 of 90 | ``gps_degraded_fraction`` 90/267; both paces equal the untagged run's, whose fraction is 0.0 | ``gps_degraded`` samples are counted in the fraction and not excluded (section 1); the fraction is contributed distance over D (section 5) |
| seam | altitude, HR and distance as nan/inf; two ``power_model`` values; g(+-0.46), g(nan) | pinned at the module seam, upload cannot deliver them (section 1) | ``test_screen_makes_every_non_finite_or_missing_numeric_field_absent``, ``test_screen_makes_nan_heart_rate_absent_before_the_sign_test`` and ``test_non_finite_altitude_samples_are_in_no_window_and_no_grade_is_non_finite`` (nan/inf); ``test_two_distinct_power_models_give_mixed_power_models``; ``test_g_raises_outside_the_measured_domain`` and ``test_g_raises_on_nan_so_the_finiteness_check_comes_first`` |
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient
from runcoach_api.ingestion import pipeline
from runcoach_api.main import app

START = datetime(2026, 3, 1, 6, 0, tzinfo=UTC)
LATER = START + timedelta(hours=1)  # a second upload in one test needs its own start_time
SPEED = 3.0  # m/s; the default device speed and the distance increment per 1 s record
FLAT = 100.0  # m; a flat altitude whose 3-sample mean is exact
HR = 150
RAW_CADENCE = 85  # mapping doubles it to 170 spm

NO_DISTANCE = {"value": None, "unavailable": "no_distance"}
PACES = ("avg_pace_s_per_km", "gap_avg_pace_s_per_km")
AC5_FEATURES = ("duration_s", "distance_m", *PACES)


class _UploadMsg:
    """A ``fitdecode.FitDataMessage`` stand-in with the four members the ingestion path reads.

    ``name``, ``get_value`` and ``fields`` are what ``mapping`` and ``hrv_classification`` read;
    ``has_field`` is what ``quarantine.extract`` calls on every ``session`` message.
    """

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)

    def has_field(self, name) -> bool:
        return name in self._values


# ---------------------------------------------------------------------------
# the oracle for g, from the published coefficients (reference section 4), never from the module
# ---------------------------------------------------------------------------


def _cost(i: float) -> float:
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def _g(i: float) -> float:
    return _cost(i) / 3.6


# ---------------------------------------------------------------------------
# building a synthetic upload
# ---------------------------------------------------------------------------


def _session(sport: str = "running", start: datetime = START) -> _UploadMsg:
    return _UploadMsg("session", {"sport": sport, "start_time": start})


def _record(
    t: float,
    *,
    start: datetime = START,
    distance: float | None,
    speed: float | None = SPEED,
    altitude: float | None = FLAT,
    heart_rate: int | None = HR,
    cadence: int | None = RAW_CADENCE,
    gps_accuracy: int | None = None,
) -> _UploadMsg:
    """One ``record`` message at ``start + t`` seconds; a ``None`` field is left off the message."""
    values = {
        "timestamp": start + timedelta(seconds=t),
        "distance": distance,
        "enhanced_speed": speed,
        "enhanced_altitude": altitude,
        "heart_rate": heart_rate,
        "cadence": cadence,
        "gps_accuracy": gps_accuracy,
    }
    return _UploadMsg("record", {k: v for k, v in values.items() if v is not None})


def _run(
    n: int,
    *,
    start: datetime = START,
    spacing: float = 1.0,
    speed: float | None = SPEED,
    distance_of: Callable[[int], float | None] | None = None,
    altitude_of: Callable[[int], float | None] | None = None,
    heart_rate: int | None = HR,
    cadence: int | None = RAW_CADENCE,
    degraded: Callable[[int], bool] = lambda k: False,
) -> list[_UploadMsg]:
    """``n`` records at ``spacing`` seconds.

    By default distance advances ``SPEED`` per second on flat ground.
    """
    if distance_of is None:
        distance_of = lambda k: SPEED * k * spacing
    if altitude_of is None:
        altitude_of = lambda k: FLAT
    return [
        _record(
            k * spacing,
            start=start,
            distance=distance_of(k),
            speed=speed,
            altitude=altitude_of(k),
            heart_rate=heart_rate,
            cadence=cadence,
            gps_accuracy=20 if degraded(k) else None,
        )
        for k in range(n)
    ]


def _features(client: TestClient, monkeypatch: pytest.MonkeyPatch, messages: list[_UploadMsg]) -> dict:
    """Upload ``messages`` through the real path and return the served features body."""
    monkeypatch.setattr(pipeline.fit_parser, "decode", lambda _raw: messages)
    created = client.post("/sessions", files={"file": ("x.fit", b"irrelevant", "application/octet-stream")})
    assert created.status_code == 201, created.text
    response = client.get(f"/sessions/{created.json()['session_id']}/features")
    assert response.status_code == 200, response.text
    return response.json()


def _value(body: dict, name: str) -> float:
    feature = body["features"][name]
    assert feature["unavailable"] is None, (name, feature)
    return feature["value"]


def _reason(body: dict, name: str) -> str:
    feature = body["features"][name]
    assert feature["value"] is None, (name, feature)
    return feature["unavailable"]


def _every_reason(body: dict) -> set[str]:
    return {f["unavailable"] for f in body["features"].values()}


def _assert_same(a: dict, b: dict, names: tuple[str, ...]) -> None:
    for name in names:
        assert _value(a, name) == pytest.approx(_value(b, name), rel=1e-9), name


# ---------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------


def _row_01_no_altitude(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(60, altitude_of=lambda k: None)])
    assert _value(body, "avg_pace_s_per_km") == pytest.approx(_value(body, "gap_avg_pace_s_per_km"), rel=1e-9)
    assert body["gap_coverage"] == 0.0
    assert body["flags"] == ["gap_unavailable"]
    assert _reason(body, "total_ascent_m") == "no_altitude"
    assert _reason(body, "total_descent_m") == "no_altitude"
    return body


def _row_02_no_distance(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(60, distance_of=lambda k: None)])
    assert body["features"]["distance_m"] == NO_DISTANCE
    assert _reason(body, "avg_pace_s_per_km") == "no_distance"
    assert _reason(body, "gap_avg_pace_s_per_km") == "no_distance"
    assert _value(body, "ngp_speed_m_s") == pytest.approx(SPEED, rel=1e-9)
    assert _value(body, "ngp_pace_s_per_km") == pytest.approx(1000.0 / SPEED, rel=1e-9)
    assert body["gap_coverage"] is None
    assert body["grade_clamped_fraction"] is None
    assert body["gps_degraded_fraction"] is None
    assert body["flags"] == []
    return body


def _row_03_constant_distance(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(60, distance_of=lambda k: 500.0)])
    assert body["features"]["distance_m"] == NO_DISTANCE
    assert _value(body, "duration_s") == pytest.approx(59.0)
    assert body["flags"] == []
    return body


def _row_04_distance_regresses_once(client, monkeypatch):
    distances = [SPEED * k for k in range(60)]
    distances[30] = 80.0  # 87 -> 80 -> 93: one drop of 7 m, then a 13 m stride
    body = _features(client, monkeypatch, [_session(), *_run(60, distance_of=lambda k: distances[k])])
    # reference section 2: D = sum max(dd, 0)
    expected_d = sum(max(b - a, 0.0) for a, b in pairwise(distances))
    assert body["flags"] == ["distance_regressed"]
    assert _value(body, "distance_m") == pytest.approx(expected_d, rel=1e-9)
    # 59 strides of 3 m, less the two strides the drop replaces, plus the 13 m stride
    assert expected_d == 184.0
    return body


def _row_05_pause(client, monkeypatch):
    baseline = _features(client, monkeypatch, [_session(), *_run(90)])
    first = _run(45, start=LATER)
    paused = _record(644.0, start=LATER, distance=SPEED * 44)  # dt = 600 from record 44, dd = 0
    rest = [
        _record(k + 600.0, start=LATER, distance=SPEED * k) for k in range(45, 90)
    ]  # dt = 1 from the pause record, dd = 3
    with_pause = _features(client, monkeypatch, [_session(start=LATER), *first, paused, *rest])
    _assert_same(baseline, with_pause, AC5_FEATURES)
    assert _value(baseline, "duration_s") == pytest.approx(89.0)
    return baseline, with_pause


def _row_06_dt_zero_duplicate(client, monkeypatch):
    baseline = _features(client, monkeypatch, [_session(), *_run(90)])
    records = _run(90, start=LATER)
    records.insert(46, _record(45.0, start=LATER, distance=SPEED * 45))  # the same instant as record 45
    with_duplicate = _features(client, monkeypatch, [_session(start=LATER), *records])
    _assert_same(baseline, with_duplicate, AC5_FEATURES)
    # The served NGP is unchanged by the duplicate; the input cannot tell a series that drops the
    # dt = 0 record from one that keeps it, so that rule is pinned by the ngp module tests, not here.
    assert _value(baseline, "ngp_speed_m_s") == pytest.approx(SPEED, rel=1e-9)
    assert _value(with_duplicate, "ngp_speed_m_s") == pytest.approx(SPEED, rel=1e-9)
    _assert_same(baseline, with_duplicate, ("ngp_speed_m_s",))
    return baseline, with_duplicate


def _row_07_distance_jump_speed_continuous(client, monkeypatch):
    baseline = _features(client, monkeypatch, [_session(), *_run(90)])
    jumped = _features(
        client,
        monkeypatch,
        [
            _session(start=LATER),
            *_run(90, start=LATER, distance_of=lambda k: SPEED * k + (52.0 if k >= 45 else 0.0)),
        ],
    )
    assert _value(jumped, "ngp_speed_m_s") == pytest.approx(_value(baseline, "ngp_speed_m_s"), rel=1e-9)
    assert _value(jumped, "distance_m") == pytest.approx(_value(baseline, "distance_m") + 52.0, rel=1e-9)
    # The 55 m stride's midpoint window, +-25 m on s, holds no record, so that stride alone has no grade
    # (reference section 3); it is counted out of coverage, which raises gap_unavailable (section 8).
    assert jumped["gap_coverage"] == pytest.approx((267.0 + 52.0 - 55.0) / (267.0 + 52.0), rel=1e-9)
    assert jumped["flags"] == ["gap_unavailable"]
    assert baseline["gap_coverage"] == 1.0
    return baseline, jumped


def _row_08_shorter_than_30_s(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(20)])
    assert _reason(body, "ngp_speed_m_s") == "no_30s_block"
    assert _reason(body, "ngp_pace_s_per_km") == "no_30s_block"
    return body


def _row_09_exactly_30_records(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(30)])
    assert _value(body, "ngp_speed_m_s") == pytest.approx(SPEED, rel=1e-9)
    return body


def _row_10_one_record(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(1)])
    assert _reason(body, "duration_s") == "no_counted_segments"
    assert body["features"]["distance_m"] == NO_DISTANCE
    assert body["gap_coverage"] is None
    return body


def _row_11_no_records(client, monkeypatch):
    body = _features(client, monkeypatch, [_session()])
    assert body["sport"] == "running"
    assert _every_reason(body) == {"no_records"}
    assert body["gap_coverage"] is None
    return body


def _row_12_hr_zero(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(60, heart_rate=0)])
    assert _reason(body, "avg_hr_bpm") == "no_heart_rate"
    return body


def _row_13_hr_absent(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(60, heart_rate=None)])
    assert _reason(body, "avg_hr_bpm") == "no_heart_rate"
    return body


def _row_14_all_cadence_lock(client, monkeypatch):
    # HR equal to the doubled cadence on every one of 60 consecutive records, no hrv messages: wrist_ppg.
    body = _features(client, monkeypatch, [_session(), *_run(60, heart_rate=2 * RAW_CADENCE)])
    assert _reason(body, "avg_hr_bpm") == "cadence_lock"
    return body


def _row_15_cadence_zero(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(60, cadence=0)])
    assert _reason(body, "avg_cadence_spm") == "no_cadence"
    return body


def _row_16_sport_other(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(sport="other"), *_run(60)])
    assert body["sport"] == "other"
    assert _every_reason(body) == {"sport_not_running"}
    assert body["gap_coverage"] is None
    return body


def _row_17_smart_recording_no_altitude(client, monkeypatch):
    body = _features(client, monkeypatch, [_session(), *_run(60, spacing=2.0, altitude_of=lambda k: None)])
    assert body["flags"] == ["smart_recording", "gap_unavailable"]
    return body


def _row_18_ten_percent_ramp(client, monkeypatch):
    # 1 m/s for 400 m, altitude 0.1 * s unquantized: 401 records.
    body = _features(
        client,
        monkeypatch,
        [_session(), *_run(401, speed=1.0, distance_of=lambda k: float(k), altitude_of=lambda k: 0.1 * k)],
    )
    ratio = _value(body, "avg_pace_s_per_km") / _value(body, "gap_avg_pace_s_per_km")
    assert 0.9 * _g(0.10) <= ratio <= _g(0.10) * (1 + 1e-9)  # the task's bound
    assert ratio >= _g(0.098)  # the bound derived in the module docstring
    assert body["gap_coverage"] == pytest.approx(1.0)
    assert "grade_clamped" not in body["flags"]
    assert 38.0 <= _value(body, "total_ascent_m") <= 40.0  # 1 m hysteresis over a 39.9 m smoothed rise
    assert _value(body, "total_descent_m") == 0.0
    return body


def _row_19_half_grade_ramp(client, monkeypatch):
    # flat 100 m, then 25 m up over 50 m (i = 0.5), then flat 100 m, at 1 m/s.
    def altitude(k: int) -> float:
        return 0.5 * min(max(k - 100, 0), 50)

    body = _features(
        client,
        monkeypatch,
        [_session(), *_run(251, speed=1.0, distance_of=lambda k: float(k), altitude_of=altitude)],
    )
    assert "grade_clamped" in body["flags"]
    assert body["grade_clamped_fraction"] > 0.0
    assert _value(body, "gap_avg_pace_s_per_km") > 0.0
    return body


def _row_20_gps_degraded_on_part(client, monkeypatch):
    untagged = _features(client, monkeypatch, [_session(), *_run(90)])
    tagged = _features(
        client, monkeypatch, [_session(start=LATER), *_run(90, start=LATER, degraded=lambda k: 30 <= k < 60)]
    )
    assert untagged["gps_degraded_fraction"] == 0.0
    # 30 counted segments start on a tagged record, 3 m each, over D = 89 * 3 m (reference section 5).
    assert tagged["gps_degraded_fraction"] == pytest.approx(30 * SPEED / (89 * SPEED), rel=1e-9)
    _assert_same(untagged, tagged, PACES)
    return untagged, tagged


ROWS = {
    "01-no-altitude": _row_01_no_altitude,
    "02-no-distance": _row_02_no_distance,
    "03-constant-distance": _row_03_constant_distance,
    "04-distance-regresses-once": _row_04_distance_regresses_once,
    "05-pause-600s": _row_05_pause,
    "06-dt-zero-duplicate": _row_06_dt_zero_duplicate,
    "07-distance-jump-speed-continuous": _row_07_distance_jump_speed_continuous,
    "08-shorter-than-30s": _row_08_shorter_than_30_s,
    "09-exactly-30-records": _row_09_exactly_30_records,
    "10-one-record": _row_10_one_record,
    "11-no-records": _row_11_no_records,
    "12-hr-zero": _row_12_hr_zero,
    "13-hr-absent": _row_13_hr_absent,
    "14-all-cadence-lock": _row_14_all_cadence_lock,
    "15-cadence-zero": _row_15_cadence_zero,
    "16-sport-other": _row_16_sport_other,
    "17-smart-recording-no-altitude": _row_17_smart_recording_no_altitude,
    "18-ten-percent-ramp": _row_18_ten_percent_ramp,
    "19-half-grade-ramp-clamped": _row_19_half_grade_ramp,
    "20-gps-degraded-on-part": _row_20_gps_degraded_on_part,
}


@pytest.mark.parametrize("row", list(ROWS), ids=list(ROWS))
def test_probe_row(row: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        ROWS[row](client, monkeypatch)
