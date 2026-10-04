"""``metrics/grade.py``: the gradient over +-25 m of reconstructed distance (F013 AC3, AC4's seam).

Pure unit tests over hand-built row dicts through ``segments.screen``, at the
module seam. Three things are pinned here that a happy-path ramp cannot pin:

- **The window's width, three-valued.** The 4 m step inside 3 m of distance
  reads |i| <= 0.09 at +-25 m (green on the shipped code) and |i| > 0.45 when
  ``half_width_m`` shrinks to about one sample, the retired "same smoothing
  window" claim (red if the shipped width were shrunk). Both are asserted, on
  the raw step and on the step after the gate's 3-sample centred mean, which
  is written out below rather than imported.
- **The window walks ``s``, never raw distance or time.** A 600 s pause and a
  distance regression leave every grade exactly where it was, because ``s``
  does not advance across either.
- **The sweep against an independent oracle.** ``_reference_grades`` is the
  O(n^2) reading of the reference's rule, authored from the rule alone before
  the module existed, and compared with the two-pointer sweep on 200 random
  sessions that vary speed, pauses, regressions, absent and non-finite
  altitude and duplicate instants.

Nothing here clamps: the raw grade is the module's output, and a 50 % ramp
reads 0.50.
"""

from __future__ import annotations

import math
import random

import pytest
from runcoach_api.metrics import grade
from runcoach_api.metrics import segments as S

HALF_WIDTH_M = 25.0  # restated, not imported (independent-oracle rule)
ONE_SAMPLE_HALF_WIDTH_M = 1.5  # at 1 m/s and 1 Hz, about one sample each side of the midpoint


def _rows(altitudes: list[float | None], *, speed_m_s: float = 1.0) -> list[dict]:
    """One record per second at ``speed_m_s``; ``altitudes[k]`` is record k's altitude."""
    return [{"t": float(k), "distance": speed_m_s * k, "altitude": a} for k, a in enumerate(altitudes)]


def _time_base(rows: list[dict]) -> S.TimeBase:
    return S.build_time_base(S.screen(rows))


def _ramp(grade_i: float, length_m: int) -> list[dict]:
    """A ramp at 1 m/s rising ``grade_i`` per metre, with unquantized altitude, ``length_m`` segments."""
    return _rows([grade_i * k for k in range(length_m + 1)])


def _step(total_m: int = 200, step_at_m: int = 100, rise_m: float = 4.0, over_m: int = 3) -> list[float]:
    """Flat ground with ``rise_m`` of altitude spread linearly over ``over_m`` metres at ``step_at_m``."""
    out = []
    for k in range(total_m + 1):
        if k <= step_at_m:
            out.append(0.0)
        elif k >= step_at_m + over_m:
            out.append(rise_m)
        else:
            out.append(rise_m * (k - step_at_m) / over_m)
    return out


def _centred_mean_3(values: list[float]) -> list[float]:
    """The gate's altitude smoothing, written out: a 3-sample centred mean, clipped at the ends."""
    out = []
    for k in range(len(values)):
        window = values[max(0, k - 1) : k + 2]
        out.append(sum(window) / len(window))
    return out


def _reference_grades(tb: S.TimeBase, half_width_m: float) -> list[float | None]:
    """The reference's rule, read literally and quadratically: the oracle the sweep is checked against."""
    out: list[float | None] = []
    for seg in tb.segments:
        if not seg.counted:
            out.append(None)
            continue
        m = (tb.s[seg.k] + tb.s[seg.k + 1]) / 2
        window = [
            j
            for j, rec in enumerate(tb.records)
            if rec.altitude is not None and m - half_width_m <= tb.s[j] <= m + half_width_m
        ]
        if len(window) < 2:
            out.append(None)
            continue
        f, l = window[0], window[-1]
        ds = tb.s[l] - tb.s[f]
        if ds <= 0:
            out.append(None)
            continue
        out.append((tb.records[l].altitude - tb.records[f].altitude) / ds)  # type: ignore[operator]
    return out


def _finite_grades(grades: list[float | None]) -> list[float]:
    present = [i for i in grades if i is not None]
    assert all(math.isfinite(i) for i in present)
    return present


# -- AC3: the constant ramp ---------------------------------------------------------------------


def test_a_constant_10_percent_ramp_reads_0_10_inside_the_ramp():
    tb = _time_base(_ramp(0.10, 200))
    grades = grade.segment_grades(tb)
    interior = [i for k, i in enumerate(grades) if 25 <= tb.s[k] and tb.s[k + 1] <= 175]
    assert len(interior) == 150
    assert all(i is not None and abs(i - 0.10) < 1e-9 for i in interior)


def test_the_ramp_gives_one_entry_per_segment_and_the_clipped_end_windows_still_read_0_10():
    tb = _time_base(_ramp(0.10, 200))
    grades = grade.segment_grades(tb)
    assert len(grades) == len(tb.segments) == 200
    # On a pure linear ramp a one-sided window reads the same slope.
    assert all(i is not None and abs(i - 0.10) < 1e-9 for i in grades)


def test_the_raw_grade_is_not_clamped_here():
    grades = _finite_grades(grade.segment_grades(_time_base(_ramp(0.50, 100))))
    assert len(grades) == 100
    assert all(abs(i - 0.50) < 1e-9 for i in grades)


def test_a_downhill_ramp_reads_a_negative_grade():
    grades = _finite_grades(grade.segment_grades(_time_base(_ramp(-0.08, 100))))
    assert all(abs(i + 0.08) < 1e-9 for i in grades)


# -- AC3: the step is diluted, not clamped and not dropped; the width pinned three-valued -------

STEP_PROFILES = {"raw": _step(), "smoothed": _centred_mean_3(_step())}


@pytest.mark.parametrize("profile", sorted(STEP_PROFILES), ids=sorted(STEP_PROFILES))
def test_a_4_m_step_inside_3_m_is_diluted_below_0_09_at_25_m(profile: str):
    # The module's default width, deliberately: shrinking GRADE_HALF_WIDTH_M must turn this red.
    tb = _time_base(_rows(STEP_PROFILES[profile]))
    grades = grade.segment_grades(tb)
    present = _finite_grades(grades)
    assert len(present) == len(tb.segments) == 200, "the step is not dropped"
    assert max(abs(i) for i in present) <= 0.09
    # Diluted, not flattened: the 4 m rise is still visible across a 49 m span.
    assert max(present) > 0.07


@pytest.mark.parametrize("profile", sorted(STEP_PROFILES), ids=sorted(STEP_PROFILES))
def test_the_same_step_exceeds_0_45_when_the_window_shrinks_to_about_one_sample(profile: str):
    """State 2 of the three-valued pin: the retired "same smoothing window" width goes red."""
    tb = _time_base(_rows(STEP_PROFILES[profile]))
    narrow = _finite_grades(grade.segment_grades(tb, half_width_m=ONE_SAMPLE_HALF_WIDTH_M))
    assert max(abs(i) for i in narrow) > 0.45
    shipped = _finite_grades(grade.segment_grades(tb))  # the module default, not a restated width
    assert max(abs(i) for i in shipped) <= 0.09


def test_the_default_half_width_is_25_m_of_reconstructed_distance():
    assert grade.GRADE_HALF_WIDTH_M == HALF_WIDTH_M
    tb = _time_base(_rows(STEP_PROFILES["raw"]))
    assert grade.segment_grades(tb) == grade.segment_grades(tb, half_width_m=HALF_WIDTH_M)


# -- AC4 (seam): non-finite altitude is in no window --------------------------------------------


def test_non_finite_altitude_samples_are_in_no_window_and_no_grade_is_non_finite():
    altitudes: list[float | None] = [0.10 * k for k in range(101)]
    poisoned = list(altitudes)
    poisoned[30] = math.nan
    poisoned[31] = math.inf
    poisoned[32] = -math.inf
    poisoned[60] = None
    tb = _time_base(_rows(poisoned))
    grades = grade.segment_grades(tb)
    assert len(grades) == 100
    present = _finite_grades(grades)
    # Every window still has two finite samples on a linear ramp, so the slope is unchanged.
    assert len(present) == 100
    assert all(abs(i - 0.10) < 1e-9 for i in present)
    # The screened records are absent, so the result equals the ramp with those samples never recorded.
    clean = _time_base([row for k, row in enumerate(_rows(altitudes)) if k not in (30, 31, 32, 60)])
    assert set(present) == set(_finite_grades(grade.segment_grades(clean)))


def test_a_window_whose_only_samples_are_non_finite_has_no_grade():
    altitudes: list[float | None] = [math.nan] * 201
    altitudes[0] = 0.0
    altitudes[200] = 20.0
    tb = _time_base(_rows(altitudes))
    grades = grade.segment_grades(tb)
    # The midpoint of segment 100 is 100.5; its window [75.5, 125.5] holds no present altitude.
    assert grades[100] is None
    # Segment 0's window [-24.5, 25.5] holds only record 0: one sample, no grade.
    assert grades[0] is None
    assert set(grades) == {None}


def test_no_altitude_at_all_gives_none_for_every_segment():
    tb = _time_base([{"t": float(k), "distance": float(k), "altitude": None} for k in range(50)])
    assert grade.segment_grades(tb) == [None] * 49


# -- Too few samples and s[l] <= s[f] -----------------------------------------------------------


def test_a_single_altitude_sample_in_the_whole_session_grades_nothing():
    altitudes: list[float | None] = [None] * 60
    altitudes[30] = 12.0
    assert set(grade.segment_grades(_time_base(_rows(altitudes)))) == {None}


def test_standing_still_puts_every_sample_at_one_s_so_nothing_is_graded():
    # Distance never advances, altitude drifts: s[l] == s[f] for every window, which is no grade,
    # not a division by zero.
    rows = [{"t": float(k), "distance": 100.0, "altitude": 300.0 + 0.2 * k} for k in range(40)]
    tb = _time_base(rows)
    assert all(seg.counted for seg in tb.segments)
    assert grade.segment_grades(tb) == [None] * 39


def test_two_samples_at_distinct_s_are_enough_for_a_grade():
    tb = _time_base(_rows([10.0, 10.5]))
    assert grade.segment_grades(tb) == [0.5]


def test_an_empty_or_one_record_session_has_no_segments_and_no_grades():
    assert grade.segment_grades(_time_base([])) == []
    assert grade.segment_grades(_time_base(_rows([5.0]))) == []


# -- End clipping, pauses and regressions ------------------------------------------------------


def test_windows_clip_one_sided_at_the_session_ends():
    # altitude = 0.001 * s^2, so a window's chord slope reveals exactly which records it spanned.
    tb = _time_base(_rows([0.001 * k * k for k in range(201)]))
    grades = grade.segment_grades(tb)
    first, last = grades[0], grades[-1]
    assert first is not None and last is not None
    # Segment 0: midpoint 0.5, window [-24.5, 25.5] -> records 0..25.
    assert abs(first - (0.001 * 25**2 - 0.0) / 25) < 1e-9
    # Segment 199: midpoint 199.5, window [174.5, 224.5] -> records 175..200.
    assert abs(last - (0.001 * 200**2 - 0.001 * 175**2) / 25) < 1e-9
    # An interior segment spans the full 49 m: records k-24..k+25 around midpoint k+0.5.
    k = 100
    assert grades[k] is not None
    assert abs(grades[k] - (0.001 * 125**2 - 0.001 * 76**2) / 49) < 1e-9


def _with_pause(rows: list[dict], after_index: int, pause_s: float) -> list[dict]:
    """Insert a dt = ``pause_s``, dd = 0 segment after ``after_index`` and shift the later records."""
    out = []
    for k, row in enumerate(rows):
        shifted = dict(row)
        if k > after_index:
            shifted["t"] = row["t"] + pause_s
        out.append(shifted)
        if k == after_index:
            out.append({**row, "t": row["t"] + pause_s})
    return out


def test_a_600_s_pause_does_not_break_the_window():
    plain = _time_base(_ramp(0.10, 200))
    paused = _time_base(_with_pause(_ramp(0.10, 200), 100, 600.0))
    grades = grade.segment_grades(paused)
    assert len(grades) == len(paused.segments) == 201
    assert paused.segments[100].is_break and grades[100] is None
    # Every counted segment still reads the ramp: the window spans the pause because s does not move.
    counted = [i for k, i in enumerate(grades) if paused.segments[k].counted]
    assert len(counted) == 200
    assert all(i is not None and abs(i - 0.10) < 1e-9 for i in counted)
    assert counted == grade.segment_grades(plain)


def test_a_distance_regression_leaves_the_window_on_s_not_on_raw_distance():
    # The device distance jumps back 30 m at record 100 and carries on; the runner kept moving, so
    # altitude follows s (the reconstructed distance), which treats the regression as 0 m.
    rows = []
    for k in range(201):
        distance = float(k) if k < 100 else float(k - 30)
        rows.append({"t": float(k), "distance": distance})
    tb_for_s = _time_base(rows)
    assert tb_for_s.distance_regressed
    for k, row in enumerate(rows):
        row["altitude"] = 0.10 * tb_for_s.s[k]
    tb = _time_base(_with_pause(rows, 50, 600.0))
    grades = grade.segment_grades(tb)
    counted = [i for k, i in enumerate(grades) if tb.segments[k].counted]
    assert len(counted) == 200
    assert all(i is not None and abs(i - 0.10) < 1e-9 for i in counted)


def test_non_counted_segments_have_no_grade_and_keep_their_place():
    rows = _ramp(0.10, 60)
    rows = rows[:31] + [dict(rows[30])] + rows[31:]  # a dt = 0 duplicate of record 30
    tb = _time_base(_with_pause(rows, 10, 600.0))
    grades = grade.segment_grades(tb)
    assert len(grades) == len(tb.segments)
    for seg, i in zip(tb.segments, grades, strict=True):
        if seg.counted:
            assert i is not None and abs(i - 0.10) < 1e-9
        else:
            assert i is None
    assert sum(i is None for i in grades) == 2  # the break and the duplicate


# -- The half-width argument --------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_a_non_positive_or_non_finite_half_width_is_refused(bad: float):
    tb = _time_base(_ramp(0.10, 10))
    with pytest.raises(ValueError):
        grade.segment_grades(tb, half_width_m=bad)


# -- The sweep against the brute-force reference -------------------------------------------------


def _random_session(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    t = 0.0
    distance = rng.uniform(0.0, 50.0)
    altitude = rng.uniform(0.0, 500.0)
    for _ in range(rng.randint(2, 120)):
        dt = rng.choice([0.0, 0.5, 1.0, 1.0, 1.0, 2.0, 5.0, 5.5, 7.0, 600.0])
        t += dt
        speed = rng.choice([0.0, 0.5, 1.0, 2.5, 4.0, 6.0])
        dd = speed * dt
        if rng.random() < 0.08:
            dd = -rng.uniform(0.0, 40.0)  # a regression
        distance += dd
        altitude += rng.gauss(0.0, 0.6)
        row: dict = {"t": t, "distance": distance, "altitude": altitude}
        roll = rng.random()
        if roll < 0.06:
            row["altitude"] = None
        elif roll < 0.09:
            row["altitude"] = rng.choice([math.nan, math.inf, -math.inf])
        if rng.random() < 0.04:
            row["distance"] = rng.choice([None, math.nan])
        rows.append(row)
    return rows


@pytest.mark.parametrize("half_width_m", [25.0, 5.0, 1.5])
def test_the_sweep_agrees_with_the_brute_force_reference_on_200_random_sessions(half_width_m: float):
    rng = random.Random(0)
    graded = 0
    for _ in range(200):
        tb = _time_base(_random_session(rng))
        got = grade.segment_grades(tb, half_width_m=half_width_m)
        assert got == _reference_grades(tb, half_width_m)
        assert len(got) == len(tb.segments)
        graded += sum(i is not None for i in got)
        _finite_grades(got)
    assert graded > 1000, "the random sessions must actually exercise graded windows"
