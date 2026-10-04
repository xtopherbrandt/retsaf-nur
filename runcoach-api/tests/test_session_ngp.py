"""``metrics/ngp.py``: normalized graded speed over contiguous blocks of device speed (F013 AC6).

Pure unit tests at the module seam, over hand-built row dicts run through
``segments.screen`` and ``segments.build_time_base``. The module computes no
grade, so every test either passes g = 1 or injects g values directly.

Each expected value is computed by hand in the test body from the reference's
rule alone: the series is ``record.speed * g``; blocks split at a break
(dt > 5 s) and at an absent speed; a record reached by a dt = 0 segment is
dropped and does not split; a block of n samples yields n - 29 trailing
30-sample windows; the window means are raised to the fourth power, pooled
over every block, averaged, and the fourth root is taken.
"""

from __future__ import annotations

import pytest
from runcoach_api.metrics import ngp as N
from runcoach_api.metrics import segments as S


def _rows(n: int, speed: float | None = 3.0, t0: float = 0.0, d0: float = 0.0) -> list[dict]:
    """``n`` records on a 1 s grid from ``t0``, distance advancing 3 m per record, at ``speed``."""
    return [{"t": t0 + k, "distance": d0 + 3.0 * k, "speed": speed} for k in range(n)]


def _tb(rows: list[dict]) -> S.TimeBase:
    return S.build_time_base(S.screen(rows))


def _ngp(rows: list[dict], g: list[float] | None = None) -> S.Feature:
    tb = _tb(rows)
    return N.ngp(tb, [1.0] * len(tb.records) if g is None else g)


def _value(rows: list[dict], g: list[float] | None = None) -> float:
    feature = _ngp(rows, g)
    assert feature.unavailable is None, feature
    return feature.value


# -- The 30-sample window ------------------------------------------------------------------------


def test_a_29_record_block_is_no_30s_block_and_30_records_have_a_value():
    assert _ngp(_rows(29)) == S.Feature(None, "no_30s_block")
    thirty = _ngp(_rows(30))
    assert thirty.unavailable is None
    assert abs(thirty.value - 3.0) < 1e-12


def test_the_window_is_thirty_samples_as_spec_03_section_3_3_3_states():
    assert N.NGP_WINDOW == 30


def test_a_constant_speed_flat_session_gives_ngp_equal_to_that_speed():
    for n in (30, 31, 120, 1000):
        assert abs(_value(_rows(n, speed=2.75)) - 2.75) < 1e-12, n


def test_no_30s_block_takes_precedence_over_no_motion_when_no_window_is_full():
    assert _ngp(_rows(29, speed=0.0)) == S.Feature(None, "no_30s_block")
    assert _ngp(_rows(1, speed=0.0)) == S.Feature(None, "no_30s_block")
    assert _ngp([]) == S.Feature(None, "no_30s_block")


def test_all_speeds_zero_over_a_full_window_is_no_motion():
    assert _ngp(_rows(45, speed=0.0)) == S.Feature(None, "no_motion")


# -- Device speed, not distance --------------------------------------------------------------------


def test_a_52_m_one_second_distance_jump_with_continuous_speed_leaves_ngp_unchanged():
    plain = _rows(60)
    jumped = [dict(row) for row in plain]
    for row in jumped[20:]:
        row["distance"] += 52.0  # a GPS-acquisition jump: one 55 m stride, speed still 3.0
    assert _tb(jumped).D == _tb(plain).D + 52.0  # the jump reaches the time base...
    assert _ngp(jumped) == _ngp(plain)  # ...and not NGP
    assert abs(_value(jumped) - 3.0) < 1e-12


def test_ngp_reads_nothing_from_distance_so_an_absent_distance_stream_still_has_a_value():
    rows = [{"t": float(k), "speed": 4.0} for k in range(40)]
    tb = _tb(rows)
    assert tb.D == 0.0
    assert abs(N.ngp(tb, [1.0] * 40).value - 4.0) < 1e-12


# -- Blocks ----------------------------------------------------------------------------------------


def _two_blocks(n_first: int, a: float, n_second: int, b: float, pause_s: float = 600.0) -> list[dict]:
    first = _rows(n_first, speed=a)
    second = _rows(n_second, speed=b, t0=(n_first - 1) + pause_s, d0=3.0 * (n_first - 1))
    return first + second


def test_two_blocks_split_by_a_pause_pool_their_windows_by_fourth_power_mean():
    a, b = 3.0, 4.0
    rows = _two_blocks(40, a, 35, b)
    tb = _tb(rows)
    assert sum(seg.is_break for seg in tb.segments) == 1
    # 40 records give 11 full windows at a; 35 give 6 at b; no window spans the pause.
    expected = ((11 * a**4 + 6 * b**4) / 17) ** 0.25
    assert abs(_value(rows) - expected) < 1e-12
    # Had the pause not split the block, 75 samples would give 46 windows, 29 of them mixing a and b.
    series = [a] * 40 + [b] * 35
    unsplit_means = [sum(series[e - 29 : e + 1]) / 30 for e in range(29, 75)]
    unsplit = (sum(m**4 for m in unsplit_means) / 46) ** 0.25
    assert abs(expected - unsplit) > 1e-3


def test_no_window_spans_a_pause_so_two_29_record_blocks_are_no_30s_block():
    rows = _two_blocks(29, 3.0, 29, 3.0)
    assert len(_tb(rows).records) == 58
    assert _ngp(rows) == S.Feature(None, "no_30s_block")


def test_a_pause_of_exactly_five_seconds_does_not_split_a_block():
    rows = _two_blocks(29, 3.0, 1, 3.0, pause_s=5.0)
    assert abs(_value(rows) - 3.0) < 1e-12


def test_an_absent_speed_splits_a_block_exactly_like_a_break():
    a, b = 3.0, 4.0
    rows = _rows(40, speed=a) + _rows(1, speed=None, t0=40.0, d0=120.0) + _rows(35, speed=b, t0=41.0, d0=123.0)
    tb = _tb(rows)
    assert not any(seg.is_break for seg in tb.segments)
    assert tb.records[40].speed is None
    expected = ((11 * a**4 + 6 * b**4) / 17) ** 0.25
    assert abs(_value(rows) - expected) < 1e-12


def test_an_absent_speed_inside_a_block_of_59_leaves_no_full_window():
    rows = _rows(59)
    rows[29]["speed"] = float("nan")  # screened to absent
    assert _ngp(rows) == S.Feature(None, "no_30s_block")


# -- The dt = 0 duplicate --------------------------------------------------------------------------


def test_a_dt_zero_duplicate_is_dropped_from_the_series_and_does_not_split_the_block():
    plain = _rows(30)
    duplicated = plain[:12] + [{**plain[11], "speed": 0.0}] + plain[12:]
    tb = _tb(duplicated)
    assert len(tb.records) == 31 and tb.segments[11].dt == 0.0
    assert _ngp(duplicated) == _ngp(plain)
    assert abs(_value(duplicated) - 3.0) < 1e-12


def test_a_dt_zero_duplicate_does_not_make_a_29_record_block_reach_30():
    rows = _rows(29)
    duplicated = rows[:10] + [dict(rows[9])] + rows[10:]
    assert len(_tb(duplicated).records) == 30
    assert _ngp(duplicated) == S.Feature(None, "no_30s_block")


# -- Injected g ------------------------------------------------------------------------------------


def test_injected_g_of_1_2_on_every_record_scales_speed_3_to_3_6():
    rows = _rows(50)
    assert abs(_value(rows, [1.2] * 50) - 3.6) < 1e-12


def test_g_is_applied_per_record_to_the_record_it_indexes():
    rows = _rows(30)
    g = [1.0] * 30
    g[29] = 2.0  # the one window holds 29 samples at 3.0 and one at 6.0
    assert abs(_value(rows, g) - (29 * 3.0 + 6.0) / 30) < 1e-12


def test_g_per_record_must_match_the_record_count():
    tb = _tb(_rows(30))
    with pytest.raises(ValueError):
        N.ngp(tb, [1.0] * 29)
    with pytest.raises(ValueError):
        N.ngp(tb, [1.0] * 31)


# -- The block numbering the per-segment stream reads --------------------------------------------


def test_record_block_ids_number_the_blocks_ngp_pools_and_mark_left_out_records_none():
    rows = _rows(12)
    rows[4]["speed"] = None  # closes block 0 after record 3; record 5 opens block 1
    rows = rows[:8] + [dict(rows[7])] + rows[8:]  # a dt = 0 duplicate at index 8: left out, no split
    for row in rows[10:]:
        row["t"] += 100.0  # a break before index 10 opens block 2
    tb = _tb(rows)
    ids = N.record_block_ids(tb)
    assert len(ids) == len(tb.records) == 13
    assert ids == [0, 0, 0, 0, None, 1, 1, 1, None, 1, 2, 2, 2]
    assert [len(block) for block in N._blocks(tb, [1.0] * 13)] == [4, 4, 3]


def test_record_block_ids_skip_no_number_when_a_block_would_be_empty():
    rows = _rows(6)
    rows[2]["speed"] = None
    rows[3]["speed"] = None  # two absent speeds in a row open no empty block between them
    assert N.record_block_ids(_tb(rows)) == [0, 0, None, None, 1, 1]
    assert N.record_block_ids(_tb([])) == []
