"""The moving-gap rule of the time base (IDEA-126, ruled 2026-10-10): a gap over 5 s is counted when the runner moved.

A segment with ``dt > SEGMENT_MAX_DT_S`` across which the device distance
advanced at ``MOVING_GAP_MIN_SPEED_M_S`` (0.5 m/s) or more is a **moving
gap**: its ``dt`` counts toward T and its ``dd`` toward D, so duration,
distance, pace and GAP include it, and the session carries the flag
``moving_gap``. A gap below that speed, or with a distance absent on either
side, stays a break and contributes nothing, as every pause always has. The
gap is still a break for NGP, whose blocks split at every gap over 5 s.

Pure unit tests over hand-built rows at the module seams, plus one walk over
the real running fixtures (provenance table in ``fixtures/README.md``, run
proof ``yes``) through the upload path: no fixture has a gap over 5 s faster
than 0.035 m/s, so none carries the flag and none changes value. The
corpus walk prints the fastest over-5 s gap it saw per fixture, so the
probe's ``-rA`` output shows the slice compared.

**A moving gap carries the value at its start** (IDEA-126, ruled 2026-10-10
against the recommendation). ``avg_hr_bpm``, ``avg_cadence_spm``,
``avg_power_w`` and the load's ``hr_time_s`` walk the counted segments: a
moving gap weighs its start record's value by its whole ``dt``, exactly as a
stride weighs its start record, so the gapped run and the gap-free 1 Hz run
read the same ``hr_time_s`` and the same means when the dropped records
carried the start value. The gap end's value is never used across the gap
(the carry test puts a different HR there), nothing is interpolated, and a
gap whose start HR is unusable adds no ``hr_time_s``. A stationary pause is
not counted and adds nothing, as before. ``gps_degraded_fraction`` keeps its
numerator and denominator on the same counted segments, so it is not
understated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.main import app
from runcoach_api.metrics import segments as S
from runcoach_api.metrics import session_features as SF
from runcoach_api.metrics import session_load as SL

FIXTURES = Path(__file__).parent / "fixtures"
# Every fixture the provenance table gives run proof ``yes``.
RUNNING_FIXTURES = (
    "sample_run.fit",
    "dev_fields_run.fit",
    "wrist_ppg_run.fit",
    "strap_run_hrv.fit",
    "hilly_run_8k_fr945.fit",
    "hilly_long_run_17k_fr945.fit",
)


def _session(flags: list[str] | None = None) -> dict:
    return {"session_id": "s1", "sport": "running", "quality_flags": flags or [], "context": {}}


def _ramp(n: int, speed: float, grade: float, **extra) -> list[dict]:
    """``n`` records on a 1 s grid at ``speed`` m/s, altitude rising ``grade`` per metre."""
    rows = []
    for k in range(n):
        row = {"t": float(k), "distance": speed * k, "speed": speed, "altitude": grade * speed * k}
        row.update({name: value[k] if isinstance(value, list) else value for name, value in extra.items()})
        rows.append(row)
    return rows


def _value(response: dict, name: str) -> float:
    feature = response["features"][name]
    assert feature["unavailable"] is None, (name, feature)
    return feature["value"]


def _with_moving_gap(rows: list[dict], first_dropped: int, n_dropped: int) -> list[dict]:
    """Drop ``n_dropped`` consecutive records: the runner kept going, the watch wrote nothing."""
    return rows[:first_dropped] + rows[first_dropped + n_dropped :]


def _with_pause(rows: list[dict], after_index: int, pause_s: float) -> list[dict]:
    """Insert a dt = ``pause_s``, dd = 0 segment after ``after_index``: a true standstill."""
    out = []
    for k, row in enumerate(rows):
        shifted = dict(row)
        if k > after_index:
            shifted["t"] = row["t"] + pause_s
        out.append(shifted)
        if k == after_index:
            out.append({**row, "t": row["t"] + pause_s})
    return out


# A 3 m/s run on a 7 % ramp: 60 s of it is 180 m and 12.6 m of climb.
PLAIN = _ramp(300, 3.0, grade=0.07, heart_rate=150, cadence=170)
GAPPED = _with_moving_gap(PLAIN, 101, 59)  # records 101..159 dropped: t 100 -> 160, 180 m, 12.6 m


def _gap_segment(rows: list[dict]) -> S.Segment:
    (seg,) = [seg for seg in S.build_time_base(S.screen(rows)).segments if seg.dt > S.SEGMENT_MAX_DT_S]
    return seg


# -- the rule ----------------------------------------------------------------------------------------


def test_a_sixty_second_gap_at_running_speed_counts_in_full():
    seg = _gap_segment(GAPPED)
    assert seg.dt == 60.0 and seg.counted and seg.moving_gap and seg.is_break and not seg.sampled
    assert seg.contributed_m == 180.0 and seg.v_actual == pytest.approx(3.0)
    plain = SF.compute_session_features(_session(), PLAIN)
    gapped = SF.compute_session_features(_session(), GAPPED)
    assert _value(gapped, "duration_s") == pytest.approx(_value(plain, "duration_s"), rel=1e-9) == 299.0
    assert _value(gapped, "distance_m") == pytest.approx(_value(plain, "distance_m"), rel=1e-9) == 897.0
    assert _value(gapped, "avg_pace_s_per_km") == pytest.approx(_value(plain, "avg_pace_s_per_km"), rel=1e-9)
    assert "moving_gap" in gapped["flags"]
    assert "moving_gap" not in plain["flags"]
    # The one primitive: the lean reader counts the gap too.
    assert S.recorded_totals(GAPPED) == (299.0, 897.0)


def test_a_stationary_pause_stays_a_break():
    paused = _with_pause(PLAIN, 100, 600.0)
    seg = _gap_segment(paused)
    assert seg.dt == 600.0 and seg.is_break and not seg.counted and not seg.moving_gap and not seg.sampled
    assert seg.contributed_m == 0.0 and seg.v_actual is None
    plain = SF.compute_session_features(_session(), PLAIN)
    out = SF.compute_session_features(_session(), paused)
    assert _value(out, "duration_s") == _value(plain, "duration_s") == 299.0
    assert _value(out, "distance_m") == _value(plain, "distance_m") == 897.0
    assert "moving_gap" not in out["flags"]
    assert S.recorded_totals(paused) == (299.0, 897.0)


@pytest.mark.parametrize(
    ("d0", "d1", "counted"),
    [
        (0.0, 5.0, True),  # exactly 0.5 m/s: counted
        (0.0, 4.999, False),  # just under: a break
        (None, 5.0, False),  # a distance absent on either side: a break
        (0.0, None, False),
        (10.0, 7.0, False),  # distance going back across a gap is not motion
    ],
    ids=["0.5", "0.4999", "d0-absent", "d1-absent", "regressing"],
)
def test_the_moving_gap_threshold_is_strict_at_half_a_metre_per_second(d0, d1, counted):
    assert S.MOVING_GAP_MIN_SPEED_M_S == 0.5
    rows = [{"t": 0.0, "distance": d0}, {"t": 10.0, "distance": d1}]
    tb = S.build_time_base(S.screen(rows))
    (seg,) = tb.segments
    assert seg.is_break and not seg.sampled
    assert (seg.counted, seg.moving_gap) == (counted, counted)
    if counted:
        assert seg.contributed_m == 5.0 and seg.v_actual == 0.5
        assert (tb.T, tb.D) == (10.0, 5.0) and tb.s == [0.0, 5.0]
    else:
        assert seg.contributed_m == 0.0 and seg.v_actual is None and not seg.regressed
        assert (tb.T, tb.D) == (0.0, 0.0) and tb.s == [0.0, 0.0]
    assert S.recorded_totals(rows) == (tb.T, tb.D)
    assert S._contribution(10.0, d0, d1) == (counted, 5.0 if counted else 0.0, False)


def test_gps_drift_across_a_stationary_gap_counts_as_ruled():
    """A distance-only rule cannot tell a drift from a stride: 8.6 m over 6 s at speed 0 is counted.

    The jump seen in ``wrist_ppg_hrv_snapshot``, restated on a constructed
    run. This is the ruled behaviour (IDEA-126, 2026-10-10), pinned so a
    change to it is deliberate.
    """
    rows = _ramp(120, 3.0, grade=0.0)
    for row in rows[60:]:
        row["t"] += 5.0  # the gap from record 59 to 60 is 6 s
        row["distance"] += 5.6  # and its distance is 3.0 + 5.6 = 8.6 m
    rows[59]["speed"] = 0.0
    rows[60]["speed"] = 0.0
    seg = _gap_segment(rows)
    assert seg.dt == 6.0 and seg.counted and seg.moving_gap
    assert seg.contributed_m == pytest.approx(8.6) and seg.v_actual == pytest.approx(8.6 / 6.0)
    out = SF.compute_session_features(_session(), rows)
    assert _value(out, "duration_s") == 124.0
    assert _value(out, "distance_m") == pytest.approx(119 * 3.0 + 5.6)
    assert "moving_gap" in out["flags"]


# -- the per-segment stream, the means and the fractions --------------------------------------------


def test_segment_rows_mark_the_moving_gap_and_the_means_carry_it():
    stream = SF.segment_rows(_session(), GAPPED)
    gap_rows = [row for row in stream if row["moving_gap"]]
    assert len(stream) == 299 - 59  # 240 sampled strides and the one gap
    assert len(gap_rows) == 1 and gap_rows[0]["dt"] == 60.0 and gap_rows[0]["t_start"] == 100.0
    assert gap_rows[0]["contributed_m"] == 180.0 and gap_rows[0]["s_start"] == 300.0
    assert gap_rows[0]["heart_rate"] == 150.0 and not gap_rows[0]["hr_excluded"]
    assert all(row["dt"] == 1.0 for row in stream if not row["moving_gap"])
    # avg_hr and hr_time_s walk the counted segments, the gap weighted at its start: the gapped
    # run reads the gap-free run's figures.
    plain = SF.compute_session_features(_session(), PLAIN)
    gapped = SF.compute_session_features(_session(), GAPPED)
    assert _value(gapped, "avg_hr_bpm") == _value(plain, "avg_hr_bpm") == 150.0
    assert _value(gapped, "avg_cadence_spm") == 170.0
    assert SL._hr_time_s(stream) == 299.0
    assert SL._hr_time_s(SF.segment_rows(_session(), PLAIN)) == 299.0


# The gap starts at record 100 (HR 150, cadence 170, power 250) and ends at record 160 (HR 170,
# cadence 180, power 300): a carry-end or interpolating rule reads a different mean.
CARRY_HR = [150.0] * 101 + [170.0] * 199
CARRY_CADENCE = [170.0] * 101 + [180.0] * 199
CARRY_POWER = [250.0] * 101 + [300.0] * 199
# The gap-free walk: 100 strides at the first value, the 60 s gap at the first value, 139 strides
# at the second, over 299 s.
OLD_RULE_HR_TIME_S = 239.0  # the gap left out: the rule before IDEA-126's carry ruling


def _carry_rows(**extra) -> list[dict]:
    rows = _ramp(300, 3.0, grade=0.0, heart_rate=list(CARRY_HR), cadence=CARRY_CADENCE, power=CARRY_POWER, **extra)
    return _with_moving_gap(rows, 101, 59)


def test_a_moving_gap_carries_the_hr_before_it_into_hr_time_and_the_mean():
    rows = _carry_rows()
    out = SF.compute_session_features(_session(), rows)
    stream = SF.segment_rows(_session(), rows)
    hr_time_s = SL._hr_time_s(stream)
    carry_start = (100 * 150.0 + 60 * 150.0 + 139 * 170.0) / 299
    carry_end = (100 * 150.0 + 60 * 170.0 + 139 * 170.0) / 299
    interpolated = (100 * 150.0 + 60 * 160.0 + 139 * 170.0) / 299
    old_rule = (100 * 150.0 + 139 * 170.0) / OLD_RULE_HR_TIME_S
    print(f"hr_time_s {hr_time_s}; avg_hr {_value(out, 'avg_hr_bpm')} carry-start {carry_start} carry-end {carry_end} interpolated {interpolated} old rule {old_rule}")
    assert hr_time_s == OLD_RULE_HR_TIME_S + 60.0 == 299.0
    assert hr_time_s == SL._hr_time_s(SF.segment_rows(_session(), _ramp(300, 3.0, grade=0.0, heart_rate=150.0)))
    assert _value(out, "avg_hr_bpm") == pytest.approx(carry_start)
    assert _value(out, "avg_hr_bpm") != pytest.approx(carry_end, rel=1e-6)
    assert _value(out, "avg_hr_bpm") != pytest.approx(interpolated, rel=1e-6)
    assert _value(out, "avg_hr_bpm") != pytest.approx(old_rule, rel=1e-6)
    assert _value(out, "avg_cadence_spm") == pytest.approx((100 * 170.0 + 60 * 170.0 + 139 * 180.0) / 299)
    assert _value(out, "avg_power_w") == pytest.approx((100 * 250.0 + 60 * 250.0 + 139 * 300.0) / 299)
    # A gap whose start HR is unusable adds no hr_time_s: absent, or carrying the gate's cadence_lock.
    absent = _carry_rows()
    absent[100]["heart_rate"] = None
    assert SL._hr_time_s(SF.segment_rows(_session(), absent)) == OLD_RULE_HR_TIME_S
    assert _value(SF.compute_session_features(_session(), absent), "avg_hr_bpm") == pytest.approx(old_rule)
    locked = _carry_rows(sample_quality=[["cadence_lock"] if k == 100 else [] for k in range(300)])
    locked_stream = SF.segment_rows(_session(), locked)
    assert [row["hr_excluded"] for row in locked_stream if row["moving_gap"]] == [True]
    assert SL._hr_time_s(locked_stream) == OLD_RULE_HR_TIME_S


def test_hr_on_the_moving_gap_start_alone_is_present_hr():
    """HR that sits only on the gap's start record is HR that was there: the gap counts it, and when
    the gate locked that one record the reason is ``cadence_lock``, not ``no_heart_rate``."""
    heart_rate = [150.0 if k == 100 else None for k in range(300)]
    rows = _with_moving_gap(_ramp(300, 3.0, grade=0.0, heart_rate=heart_rate), 101, 59)
    out = SF.compute_session_features(_session(), rows)
    assert _value(out, "avg_hr_bpm") == 150.0
    assert SL._hr_time_s(SF.segment_rows(_session(), rows)) == 60.0
    tags = [["cadence_lock"] if k == 100 else [] for k in range(300)]
    locked = _with_moving_gap(_ramp(300, 3.0, grade=0.0, heart_rate=heart_rate, sample_quality=tags), 101, 59)
    out = SF.compute_session_features(_session(), locked)
    assert out["features"]["avg_hr_bpm"] == {"value": None, "unavailable": "cadence_lock"}
    assert SL._hr_time_s(SF.segment_rows(_session(), locked)) == 0.0
    no_hr = _with_moving_gap(_ramp(300, 3.0, grade=0.0, heart_rate=[None] * 300), 101, 59)
    assert SF.compute_session_features(_session(), no_hr)["features"]["avg_hr_bpm"] == {"value": None, "unavailable": "no_heart_rate"}


def test_gps_degraded_fraction_counts_the_gap_in_numerator_and_denominator():
    tags = [["gps_degraded"] if k == 100 else [] for k in range(300)]
    rows = _with_moving_gap(_ramp(300, 3.0, grade=0.0, sample_quality=tags), 101, 59)
    out = SF.compute_session_features(_session(), rows)
    assert _value(out, "distance_m") == 897.0
    assert out["gps_degraded_fraction"] == pytest.approx(180.0 / 897.0)
    untagged = _with_moving_gap(_ramp(300, 3.0, grade=0.0), 101, 59)
    assert SF.compute_session_features(_session(), untagged)["gps_degraded_fraction"] == 0.0


def test_the_moving_gap_flag_comes_after_grade_clamped_and_once():
    # At 1 m/s the 60 s gap spans 60 m, so no record lies within +-25 m of its midpoint: the gap
    # itself is ungraded (gap_unavailable), while every stride on the 0.5 ramp is clamped.
    rows = _with_moving_gap(_ramp(300, 1.0, grade=0.5), 101, 59)
    out = SF.compute_session_features(_session(flags=["moving_gap", "rr_artefact_burst"]), rows)
    assert out["flags"] == ["moving_gap", "rr_artefact_burst", "gap_unavailable", "grade_clamped"]
    out = SF.compute_session_features(_session(), rows)
    assert out["flags"] == ["gap_unavailable", "grade_clamped", "moving_gap"]
    assert out["gap_coverage"] == pytest.approx((299.0 - 60.0) / 299.0)


def test_the_gap_still_splits_the_ngp_blocks():
    """NGP under sparse recording is IDEA-144's: the gap is a break for the block rule."""
    stream = SF.segment_rows(_session(), GAPPED)
    before = [row["block_id"] for row in stream if row["t_start"] < 100.0]
    after = [row["block_id"] for row in stream if row["t_start"] >= 160.0]
    assert set(before) == {0} and set(after) == {1}


# -- the corpus: no running fixture moves across a gap over 5 s -------------------------------------


def _upload(client: TestClient, filename: str) -> str:
    created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


@pytest.mark.parametrize("filename", RUNNING_FIXTURES)
def test_no_running_fixture_has_a_moving_gap_so_its_features_are_unchanged(filename):
    with TestClient(app) as client:
        session_id = _upload(client, filename)
        body = client.get(f"/sessions/{session_id}/features").json()
    conn = db_module.get_connection()
    try:
        inputs = db_module.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()
    assert inputs is not None
    _session_row, rows = inputs
    tb = S.build_time_base(S.screen(rows))
    gaps = [seg for seg in tb.segments if seg.is_break]
    speeds = [
        (tb.records[seg.k + 1].distance - tb.records[seg.k].distance) / seg.dt
        for seg in gaps
        if tb.records[seg.k].distance is not None and tb.records[seg.k + 1].distance is not None
    ]
    fastest = max(speeds, default=None)
    print(f"{filename}: {len(gaps)} gaps over 5 s, fastest {fastest} m/s")
    assert not any(seg.moving_gap for seg in gaps), filename
    assert "moving_gap" not in body["flags"], (filename, body["flags"])
    assert fastest is None or fastest < 0.05, (filename, fastest)
    # Every gap sits below the threshold, so the time base is the sampled one (accumulated in the
    # module's order: ``sum`` compensates float rounding since Python 3.12 and would not compare exactly).
    T = D = 0.0
    for seg in tb.segments:
        if seg.sampled:
            T += seg.dt
            D += seg.contributed_m
    assert (tb.T, tb.D) == (T, D)
