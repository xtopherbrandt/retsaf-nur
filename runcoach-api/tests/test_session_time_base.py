"""``metrics/segments.py``: the finite screen, the segments and the recorded-time base (F013 AC5).

Pure unit tests over hand-built row dicts, at the module seam. Upload cannot
deliver a non-finite value (SQLite stores ``nan`` as NULL and FIT altitude and
HR are integer-encoded), so the screen is pinned here, where the pure function
is called with constructed records, and never through the route.

The table of segment cases was authored from the reference's rule alone,
before the module existed: a segment is counted when ``0 < dt <= 5`` s, is a
break when ``dt > 5`` s, and is neither when ``dt = 0``. A counted segment
whose distance decreases contributes 0 m and raises ``distance_regressed``.
"""

from __future__ import annotations

import dataclasses
import math
import sqlite3

import pytest
from runcoach_api.metrics import segments as S

# A short run at 3 m/s: t 0..5, distance 0, 3, 6, ... 15.
RUN = [{"t": float(k), "distance": 3.0 * k} for k in range(6)]


def _with_pause(rows: list[dict], after_index: int, pause_s: float) -> list[dict]:
    """Insert a dt = ``pause_s``, dd = 0 segment after ``after_index``.

    A record is added ``pause_s`` later at the same distance, and every later
    record is shifted by ``pause_s``, so the strides on either side are intact.
    """
    out = []
    for k, row in enumerate(rows):
        shifted = dict(row)
        if k > after_index:
            shifted["t"] = row["t"] + pause_s
        out.append(shifted)
        if k == after_index:
            out.append({**row, "t": row["t"] + pause_s})
    return out


def _with_duplicate(rows: list[dict], at_index: int) -> list[dict]:
    """Insert a dt = 0 duplicate of record ``at_index`` right after it."""
    return rows[: at_index + 1] + [dict(rows[at_index])] + rows[at_index + 1 :]


def _pace(tb: S.TimeBase) -> float:
    return 1000.0 * tb.T / tb.D


# -- AC5: the time base is recorded time -------------------------------------------------------


def test_a_600_s_pause_leaves_recorded_time_and_distance_unchanged():
    run = S.build_time_base(S.screen(RUN))
    paused = S.build_time_base(S.screen(_with_pause(RUN, 2, 600.0)))
    assert paused.T == run.T == 5.0
    assert paused.D == run.D == 15.0
    assert _pace(paused) == _pace(run)


def test_a_dt_zero_duplicate_leaves_recorded_time_and_distance_unchanged():
    run = S.build_time_base(S.screen(RUN))
    duplicated = S.build_time_base(S.screen(_with_duplicate(RUN, 2)))
    assert duplicated.T == run.T == 5.0
    assert duplicated.D == run.D == 15.0
    assert _pace(duplicated) == _pace(run)
    # The duplicate record is kept, with a non-counted, non-break segment on each side.
    assert len(duplicated.records) == len(RUN) + 1
    seg_into, seg_out_of = duplicated.segments[2], duplicated.segments[3]
    assert seg_into.dt == 0.0 and not seg_into.counted and not seg_into.is_break
    assert seg_into.contributed_m == 0.0 and seg_into.v_actual is None
    assert seg_out_of.dt == 1.0 and seg_out_of.counted


def test_pause_and_duplicate_together_give_the_same_pace_as_the_plain_run():
    both = _with_duplicate(_with_pause(RUN, 1, 600.0), 3)
    plain = S.build_time_base(S.screen(RUN))
    perturbed = S.build_time_base(S.screen(both))
    assert (perturbed.T, perturbed.D) == (plain.T, plain.D)
    assert _pace(perturbed) == _pace(plain) == 1000.0 / 3.0


def test_a_regressing_distance_contributes_zero_and_flags_the_session():
    rows = [
        {"t": 0.0, "distance": 0.0},
        {"t": 1.0, "distance": 3.0},
        {"t": 2.0, "distance": 2.0},  # dd = -1: contributes 0 m
        {"t": 3.0, "distance": 5.0},
    ]
    tb = S.build_time_base(S.screen(rows))
    assert tb.distance_regressed is True
    seg = tb.segments[1]
    assert seg.counted and seg.regressed and seg.contributed_m == 0.0 and seg.v_actual == 0.0
    assert tb.T == 3.0
    assert tb.D == 6.0
    # s stays monotone and contiguous: the regression leaves no gap and no step back.
    assert tb.s == [0.0, 3.0, 3.0, 6.0]
    assert all(b >= a for a, b in zip(tb.s, tb.s[1:]))


def test_a_run_without_regression_has_the_flag_down():
    tb = S.build_time_base(S.screen(RUN))
    assert tb.distance_regressed is False
    assert not any(seg.regressed for seg in tb.segments)


def test_sum_of_v_actual_times_dt_equals_distance():
    rows = [
        {"t": 0.0, "distance": 0.0},
        {"t": 0.5, "distance": 1.7},
        {"t": 1.5, "distance": 1.2},  # regression
        {"t": 4.5, "distance": 9.9},
        {"t": 20.0, "distance": 50.0},  # break
        {"t": 21.0, "distance": 52.3},
        {"t": 22.0, "distance": None},  # absent distance, counted for time
        {"t": 23.0, "distance": 60.0},
    ]
    tb = S.build_time_base(S.screen(rows))
    total = sum(seg.v_actual * seg.dt for seg in tb.segments if seg.counted)
    assert tb.D > 0
    assert math.isclose(total, tb.D, rel_tol=1e-9)
    assert tb.T == 0.5 + 1.0 + 3.0 + 1.0 + 1.0 + 1.0


# -- The dt boundaries ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dt", "counted", "is_break"),
    [
        (0.0, False, False),
        (0.5, True, False),
        (1.0, True, False),
        (5.0, True, False),
        (5.0001, False, True),
        (600.0, False, True),
    ],
    ids=["dt0", "dt0.5", "dt1", "dt5", "dt5.0001", "dt600"],
)
def test_segment_dt_boundaries(dt, counted, is_break):
    tb = S.build_time_base(S.screen([{"t": 10.0, "distance": 0.0}, {"t": 10.0 + dt, "distance": 4.0}]))
    (seg,) = tb.segments
    assert seg.dt == pytest.approx(dt)
    assert (seg.counted, seg.is_break) == (counted, is_break)
    if counted:
        assert seg.contributed_m == 4.0 and seg.v_actual == pytest.approx(4.0 / dt)
        assert (tb.T, tb.D) == (pytest.approx(dt), 4.0)
    else:
        assert seg.contributed_m == 0.0 and seg.v_actual is None
        assert (tb.T, tb.D) == (0.0, 0.0)
    assert tb.s == [0.0, tb.D]


def test_segment_bound_restates_the_resampling_gap_constant():
    assert S.SEGMENT_MAX_DT_S == 5.0


# -- The finite screen ---------------------------------------------------------------------------

NUMERIC_FIELDS = ["distance", "speed", "altitude", "heart_rate", "cadence", "power"]
PRESENT_ROW = {
    "t": 7.0,
    "distance": 12.5,
    "speed": 3.1,
    "altitude": 101.2,
    "heart_rate": 150.0,
    "cadence": 172.0,
    "power": 280.0,
    "power_model": "stryd",
    "gps_degraded": 0,
    "sample_quality": ["interpolation_gap", "cadence_lock"],
}


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
@pytest.mark.parametrize(
    "bad",
    [None, float("nan"), float("inf"), float("-inf"), "missing"],
    ids=["None", "nan", "inf", "-inf", "missing-key"],
)
def test_screen_makes_every_non_finite_or_missing_numeric_field_absent(field, bad):
    row = dict(PRESENT_ROW)
    if bad == "missing":
        del row[field]
    else:
        row[field] = bad
    (rec,) = S.screen([row])
    assert getattr(rec, field) is None
    # Every other field survives untouched.
    for other in NUMERIC_FIELDS:
        if other != field:
            assert getattr(rec, other) == PRESENT_ROW[other]


def test_screen_keeps_finite_values_and_normalises_the_non_numeric_fields():
    (rec,) = S.screen([PRESENT_ROW])
    assert rec.t == 7.0
    assert rec.distance == 12.5 and rec.speed == 3.1 and rec.altitude == 101.2
    assert rec.heart_rate == 150.0 and rec.cadence == 172.0 and rec.power == 280.0
    assert rec.power_model == "stryd"
    assert rec.gps_degraded is False
    assert rec.sample_quality == ("interpolation_gap", "cadence_lock")


@pytest.mark.parametrize("hr", [0, 0.0, -1, -40.5], ids=["0", "0.0", "-1", "-40.5"])
def test_screen_makes_non_positive_heart_rate_absent(hr):
    (rec,) = S.screen([{**PRESENT_ROW, "heart_rate": hr}])
    assert rec.heart_rate is None


def test_screen_makes_nan_heart_rate_absent_before_the_sign_test():
    # ``nan <= 0`` is False, so the finiteness screen must run first.
    (rec,) = S.screen([{**PRESENT_ROW, "heart_rate": float("nan")}])
    assert rec.heart_rate is None


def test_screen_leaves_a_positive_heart_rate_present():
    (rec,) = S.screen([{**PRESENT_ROW, "heart_rate": 1}])
    assert rec.heart_rate == 1.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, False), (1, True), (False, False), (True, True), (None, None), ("missing", None)],
    ids=["0", "1", "False", "True", "None", "missing-key"],
)
def test_screen_normalises_gps_degraded_to_bool_or_none(raw, expected):
    row = dict(PRESENT_ROW)
    if raw == "missing":
        del row["gps_degraded"]
    else:
        row["gps_degraded"] = raw
    (rec,) = S.screen([row])
    assert rec.gps_degraded is expected


@pytest.mark.parametrize("raw", [None, "missing", []], ids=["None", "missing-key", "empty-list"])
def test_screen_gives_an_empty_sample_quality_tuple_when_none_is_recorded(raw):
    row = dict(PRESENT_ROW)
    if raw == "missing":
        del row["sample_quality"]
    else:
        row["sample_quality"] = raw
    (rec,) = S.screen([row])
    assert rec.sample_quality == ()


def test_screen_keeps_the_callers_sample_quality_order_and_does_not_sort():
    (rec,) = S.screen([{**PRESENT_ROW, "sample_quality": ["z_flag", "a_flag", "m_flag"]}])
    assert rec.sample_quality == ("z_flag", "a_flag", "m_flag")


@pytest.mark.parametrize("raw", [None, "missing"], ids=["None", "missing-key"])
def test_screen_leaves_power_model_absent_when_not_recorded(raw):
    row = dict(PRESENT_ROW)
    if raw == "missing":
        del row["power_model"]
    else:
        row["power_model"] = raw
    (rec,) = S.screen([row])
    assert rec.power_model is None


def test_screen_handles_a_row_with_only_t():
    (rec,) = S.screen([{"t": 3.0}])
    assert rec.t == 3.0
    for field in NUMERIC_FIELDS:
        assert getattr(rec, field) is None
    assert rec.power_model is None and rec.gps_degraded is None and rec.sample_quality == ()


@pytest.mark.parametrize(
    "bad_t",
    [None, float("nan"), float("inf"), float("-inf"), "missing"],
    ids=["None", "nan", "inf", "-inf", "missing-key"],
)
def test_screen_drops_a_row_whose_t_is_absent_or_non_finite(bad_t):
    row = dict(PRESENT_ROW)
    if bad_t == "missing":
        del row["t"]
    else:
        row["t"] = bad_t
    recs = S.screen([{"t": 1.0, "distance": 0.0}, row, {"t": 2.0, "distance": 3.0}])
    assert [rec.t for rec in recs] == [1.0, 2.0]


def test_screen_reads_sqlite_rows_by_key():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE r (t REAL, distance REAL, heart_rate INTEGER, gps_degraded INTEGER)")
    conn.execute("INSERT INTO r VALUES (1.0, 10.0, 0, 1)")
    conn.execute("INSERT INTO r VALUES (2.0, NULL, 140, 0)")
    rows = conn.execute("SELECT * FROM r ORDER BY t").fetchall()
    first, second = S.screen(rows)
    assert (first.t, first.distance, first.heart_rate, first.gps_degraded) == (1.0, 10.0, None, True)
    assert (second.t, second.distance, second.heart_rate, second.gps_degraded) == (2.0, None, 140.0, False)
    # Columns the table does not carry are absent, not an error.
    assert first.speed is None and first.power_model is None and first.sample_quality == ()


def test_screen_keeps_equal_t_records_in_input_order():
    rows = [
        {"t": 5.0, "distance": 1.0, "power_model": "first"},
        {"t": 5.0, "distance": 2.0, "power_model": "second"},
        {"t": 5.0, "distance": 3.0, "power_model": "third"},
    ]
    recs = S.screen(rows)
    assert [rec.power_model for rec in recs] == ["first", "second", "third"]
    assert [rec.distance for rec in recs] == [1.0, 2.0, 3.0]


def test_screened_record_is_frozen():
    (rec,) = S.screen([PRESENT_ROW])
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.distance = 1.0  # type: ignore[misc]


# -- Absent distances inside the time base -------------------------------------------------------


def test_a_segment_with_either_distance_absent_contributes_zero_and_is_counted_for_time():
    rows = [
        {"t": 0.0, "distance": 0.0},
        {"t": 1.0, "distance": float("nan")},
        {"t": 2.0, "distance": 6.0},
        {"t": 3.0, "distance": 9.0},
    ]
    tb = S.build_time_base(S.screen(rows))
    into, out_of, after = tb.segments
    assert into.counted and into.contributed_m == 0.0 and into.v_actual == 0.0 and not into.regressed
    assert out_of.counted and out_of.contributed_m == 0.0 and out_of.v_actual == 0.0
    assert after.contributed_m == 3.0
    assert tb.T == 3.0
    assert tb.D == 3.0
    assert tb.s == [0.0, 0.0, 0.0, 3.0]
    assert tb.distance_regressed is False


def test_a_break_contributes_neither_time_nor_distance():
    rows = [{"t": 0.0, "distance": 0.0}, {"t": 100.0, "distance": 300.0}, {"t": 101.0, "distance": 303.0}]
    tb = S.build_time_base(S.screen(rows))
    brk, stride = tb.segments
    assert brk.is_break and not brk.counted and brk.contributed_m == 0.0 and brk.v_actual is None
    assert stride.counted and stride.contributed_m == 3.0
    assert (tb.T, tb.D) == (1.0, 3.0)
    assert tb.s == [0.0, 0.0, 3.0]


# -- Shape of the time base ----------------------------------------------------------------------


def test_time_base_shape_one_segment_per_consecutive_pair_and_s_starts_at_zero():
    recs = S.screen(RUN)
    tb = S.build_time_base(recs)
    assert tb.records == recs
    assert len(tb.segments) == len(recs) - 1
    assert [seg.k for seg in tb.segments] == list(range(len(recs) - 1))
    assert len(tb.s) == len(recs)
    assert tb.s[0] == 0.0
    assert tb.s == [0.0, 3.0, 6.0, 9.0, 12.0, 15.0]


def test_time_base_of_a_single_record_has_no_segments():
    tb = S.build_time_base(S.screen([{"t": 4.0, "distance": 100.0}]))
    assert tb.segments == []
    assert tb.s == [0.0]
    assert (tb.T, tb.D, tb.distance_regressed) == (0.0, 0.0, False)


def test_time_base_of_no_records_is_empty():
    tb = S.build_time_base([])
    assert tb.records == [] and tb.segments == [] and tb.s == []
    assert (tb.T, tb.D, tb.distance_regressed) == (0.0, 0.0, False)


def test_a_constant_distance_gives_time_but_no_distance():
    rows = [{"t": float(k), "distance": 50.0} for k in range(4)]
    tb = S.build_time_base(S.screen(rows))
    assert (tb.T, tb.D, tb.distance_regressed) == (3.0, 0.0, False)
    assert tb.s == [0.0, 0.0, 0.0, 0.0]


# -- The shared Feature type ---------------------------------------------------------------------


def test_feature_holds_a_value_or_a_reason():
    assert S.Feature(1.0, None).value == 1.0
    assert S.Feature(1.0, None).unavailable is None
    reason = S.Feature(None, "no_distance")
    assert reason.value is None and reason.unavailable == "no_distance"
    assert S.Feature(value=0.0, unavailable=None) == (0.0, None)


@pytest.mark.parametrize(
    ("value", "unavailable"), [(None, None), (1.0, "no_distance")], ids=["neither", "both"]
)
def test_feature_rejects_anything_but_exactly_one_of_value_and_reason(value, unavailable):
    with pytest.raises(ValueError):
        S.Feature(value, unavailable)
