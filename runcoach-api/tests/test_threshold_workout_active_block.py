"""F026 AC3 and the lap rows of AC2: the active block of a threshold workout, as pure functions.

``threshold_pace.active_laps(laps, steps)`` decides F026 reference section 3 rows 1a and 2 from the
laps and steps alone; ``threshold_pace.active_block(laps, steps, segment_rows)`` adds the active GAP
pace and the window fractions over F013's per-segment stream. No database: laps, steps and segment
rows are plain dicts, except the ``sample_run.fit`` case, which goes through ``mapping.to_laps``,
``mapping.to_canonical``, ``quality_gates.apply`` and ``session_features.segment_rows``.

**Contract table** (rule contract-tables-need-an-independent-oracle): authored from the F026
reference section 3 before the implementation existed, and driven by ``test_contract_table``. Each
row perturbs the one active lap of a base workout (a warm-up lap, then one active lap of 600 s
naming active step 1) and names the result. "otherwise active" means intensity ``active`` and a
step index naming an active step (row 1a).

| # | perturbation of the active lap | result |
|---|---|---|
| 1 | none | active, A 600, 1 block |
| 2 | ``start_t`` None, NaN, inf, -inf, True, "0", 10**400 | ``lap_times_unreadable`` |
| 3 | ``end_t`` None, NaN, inf, True, "600", 10**400 | ``lap_times_unreadable`` |
| 4 | ``timer_s`` None, NaN, inf, False, "600", 10**400 | ``lap_times_unreadable`` |
| 5 | ``timer_s`` negative (-1) | ``lap_times_unreadable`` (a lap time below zero does not read; built-in choice, not a reference row) |
| 5b | ``timer_s`` zero, ``end_t`` after ``start_t`` | active, adding 0 s to ``A`` (only below zero does not read); ``test_a_zero_timer_lap_reads_and_adds_nothing`` |
| 6 | ``end_t == start_t`` | ``lap_times_unreadable`` |
| 7 | ``end_t < start_t`` | ``lap_times_unreadable`` |
| 8 | ``wkt_step_index`` None (with its times null too) | not active: ``no_workout_steps`` (row 1a never reads it) |
| 9 | ``wkt_step_index`` names the warm-up step | not active: ``no_workout_steps`` |
| 10 | ``wkt_step_index`` names no step (99) | not active: ``no_workout_steps`` |
| 11 | ``wkt_step_index`` True, "1" or 1.0 (not an int) | not active: ``no_workout_steps`` (True would otherwise name step 1) |
| 12 | lap intensity ``interval``, None or ``Active`` | not active: ``no_workout_steps`` |
| 13 | two steps share index 1, both active | active, A 600 |
| 14 | two steps share index 1, one active and one recovery | not active: ``no_workout_steps`` (the index does not name an active step unambiguously) |
| 15 | step 1 is a repeat step (``repeat_until_steps_cmplt``) carrying intensity ``active`` | not active: ``no_workout_steps`` (a repeat step's intensity means nothing) |
| 16 | a second, readable active lap beside an unreadable one | ``lap_times_unreadable`` (one unreadable lap excludes, never shrinks the block) |
| 17 | the warm-up lap (not active) has null times | active, A 600 (row 1a reads only otherwise-active laps) |

**The window boundary.** A segment belongs to the window its ``t_start`` lies in, ``[start_t,
end_t)``, and counts there in full: a moving dropout over 5 s that starts inside an active lap
counts all its ``dt`` and metres there even when it ends after the lap; one that starts in a
recovery lap and ends inside an active lap counts nowhere.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import math
import sys
from pathlib import Path

import pytest
from runcoach_api.ingestion import fit_parser, mapping, quality_gates
from runcoach_api.metrics import gap
from runcoach_api.metrics import session_features as SF
from runcoach_api.metrics import threshold_pace as TP

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"


def _pace_speed(pace_s_per_km: float) -> float:
    return 1000.0 / pace_s_per_km


# The synthetic flat workout of AC3: (intensity, seconds, pace s/km).
FLAT_WORKOUT = (
    ("warmup", 600, 360.0),
    ("active", 600, 270.0),
    ("recovery", 120, 390.0),
    ("active", 600, 270.0),
    ("cooldown", 600, 390.0),
)


def _laps(step_indices: list[int | None]) -> list[dict]:
    laps = []
    t = 0.0
    for index, ((intensity, seconds, _pace), step) in enumerate(zip(FLAT_WORKOUT, step_indices, strict=True)):
        laps.append(
            {
                "lap_index": index,
                "start_t": t,
                "end_t": t + seconds,
                "timer_s": float(seconds),
                "intensity": intensity,
                "wkt_step_index": step,
            }
        )
        t += seconds
    return laps


def _target(intensity: str) -> dict:
    """A work step's speed target, as ``sample_run.fit`` writes it; non-work steps carry none."""
    return {"target_type": "speed"} if intensity == "active" else {}


def _simple_steps() -> list[dict]:
    return [
        {"step_index": index, "intensity": intensity, "duration_type": "time", **_target(intensity)}
        for index, (intensity, _s, _p) in enumerate(FLAT_WORKOUT)
    ]


def _repeat_steps() -> list[dict]:
    """The repeat step table of ``tests/support/repeat_workout.py``: the repeat step keeps the
    cool-down's intensity, as that builder writes it. The repeat step carries a target too, so a
    lap naming it is turned away for being a repeat, not for a missing target."""
    return [
        {"step_index": 0, "intensity": "warmup", "duration_type": "time"},
        {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
        {"step_index": 2, "intensity": "recovery", "duration_type": "time"},
        {"step_index": 3, "intensity": "cooldown", "duration_type": "repeat_until_steps_cmplt", "target_type": "speed"},
        {"step_index": 4, "intensity": "cooldown", "duration_type": "time"},
    ]


def _segments(active_g: float = 1.0, active_i: float | None = 0.0) -> list[dict]:
    """1 Hz counted segments over FLAT_WORKOUT; the active steps carry ``active_g``."""
    rows = []
    t = 0.0
    for intensity, seconds, pace in FLAT_WORKOUT:
        v = _pace_speed(pace)
        is_active = intensity == "active"
        for _ in range(seconds):
            rows.append(
                {
                    "t_start": t,
                    "dt": 1.0,
                    "contributed_m": v,
                    "i": active_i if is_active else 0.0,
                    "graded": True,
                    "g": active_g if is_active else 1.0,
                    "moving_gap": False,
                    "gps_degraded": False,
                }
            )
            t += 1.0
    return rows


def _minetti_cost(i: float) -> float:
    """Minetti et al. (2002), J/kg/m, written out here and not imported."""
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


# -- AC3 ----------------------------------------------------------------------------------------


def test_flat_two_step_workout_reads_270_with_1200_s_and_two_blocks():
    block = TP.active_block(_laps([0, 1, 2, 3, 4]), _simple_steps(), _segments())
    print("flat:", block)
    assert block.unavailable is None
    assert block.gap_pace_s_per_km == pytest.approx(270.0, abs=0.01)
    assert block.active_time_s == 1200.0
    assert block.active_blocks == 2
    assert block.windows == ((600.0, 1200.0), (1320.0, 1920.0))
    assert block.gap_coverage == 1.0
    assert block.gps_degraded_fraction == 0.0


def test_repeat_step_workout_gives_the_same_values():
    flat = TP.active_block(_laps([0, 1, 2, 3, 4]), _simple_steps(), _segments())
    repeat = TP.active_block(_laps([0, 1, 2, 1, 4]), _repeat_steps(), _segments())
    print("repeat:", repeat)
    assert repeat.unavailable is None
    assert repeat.gap_pace_s_per_km == pytest.approx(270.0, abs=0.01)
    assert repeat.gap_pace_s_per_km == flat.gap_pace_s_per_km
    assert repeat.active_time_s == flat.active_time_s == 1200.0
    assert repeat.active_blocks == flat.active_blocks == 2
    assert repeat.windows == flat.windows


def test_graded_active_steps_match_a_hand_minetti_value():
    grade = 0.03
    g_hand = _minetti_cost(grade) / _minetti_cost(0.0)
    expected = 270.0 / g_hand
    # The rows carry the g segment_rows would write for a 3 % segment (gap.g), the oracle is the
    # polynomial above; the non-active steps stay flat.
    block = TP.active_block(_laps([0, 1, 2, 3, 4]), _simple_steps(), _segments(gap.g(grade), grade))
    print(f"graded: hand g {g_hand:.6f}, expected {expected:.4f}, got {block.gap_pace_s_per_km:.4f}")
    assert g_hand > 1.0
    assert block.gap_pace_s_per_km == pytest.approx(expected, abs=0.01)
    assert block.active_time_s == 1200.0


def test_a_moving_dropout_counts_in_the_window_it_starts_in():
    rows = _segments()
    v_active = _pace_speed(270.0)
    v_recovery = _pace_speed(390.0)
    # Replace the last 3 s of the first active lap (1197..1199) and the first 3 s of the recovery
    # (1200..1202) with one 6 s moving dropout starting at 1197: it counts in the active window in full.
    first = [row for row in rows if not 1197.0 <= row["t_start"] < 1203.0]
    dropout = {
        "t_start": 1197.0,
        "dt": 6.0,
        "contributed_m": 3 * v_active + 3 * v_recovery,
        "i": None,
        "graded": False,
        "g": 1.0,
        "moving_gap": True,
        "gps_degraded": False,
    }
    # And a 6 s dropout starting in the recovery (1317) that ends inside the second active lap (1323):
    # it starts outside every window, so it counts nowhere.
    second = [row for row in first if not 1317.0 <= row["t_start"] < 1323.0]
    late = dict(dropout, t_start=1317.0, contributed_m=3 * v_recovery + 3 * v_active)
    stream = sorted([*second, dropout, late], key=lambda row: row["t_start"])

    block = TP.active_block(_laps([0, 1, 2, 3, 4]), _simple_steps(), stream)
    # By hand: first window 597 s at 270 plus the 6 s dropout; second window loses 1320..1322 (3 s).
    T = 597.0 + 6.0 + 597.0
    D = 597 * v_active + (3 * v_active + 3 * v_recovery) + 597 * v_active
    print(f"dropout: T {T}, D {D:.3f}, pace {1000 * T / D:.4f}, got {block.gap_pace_s_per_km:.4f}")
    assert block.gap_pace_s_per_km == pytest.approx(1000.0 * T / D, rel=1e-12)
    assert block.active_time_s == 1200.0  # A is the laps' timer_s, not the segments'
    graded_share = (D - (3 * v_active + 3 * v_recovery)) / D
    assert block.gap_coverage == pytest.approx(graded_share, rel=1e-12)


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _sample_run() -> tuple[list[dict], list[dict], list[dict]]:
    messages = fit_parser.decode((FIXTURES / "sample_run.fit").read_bytes())
    laps, steps = mapping.to_laps(messages)
    session, records = mapping.to_canonical(messages)
    quality_gates.apply(session, records)
    stream = SF.segment_rows(
        {"session_id": session.session_id, "sport": "running", "quality_flags": [], "context": {}},
        [dataclasses.asdict(record) for record in records],
    )
    return [dataclasses.asdict(lap) for lap in laps], [dataclasses.asdict(step) for step in steps], stream


def test_sample_run_windows_exclude_the_trailing_lap():
    """``sample_run.fit`` (run proof ``yes``, tests/fixtures/README.md) read as a workout: laps 1-7
    name active steps 1 and 2; lap 9 is the step-less ``session_end`` lap."""
    laps, steps, stream = _sample_run()
    assert laps[9]["wkt_step_index"] is None
    # Make the trailing lap read active too: the null step index alone must keep it out.
    laps[9]["intensity"] = "active"
    block = TP.active_block(laps, steps, stream)
    print("sample_run:", block)
    assert block.unavailable is None
    # Lap 9 runs [2703, 2842): no window of the block may reach into it.
    assert not any(start < 2842 and 2703 < end for start, end in block.windows), block.windows
    # Written by hand from the decoded laps: lap 7 runs [2135, 2400).
    assert block.windows[-1] == (2135.0, 2400.0)
    assert len(block.windows) == 7
    assert block.active_blocks == 1
    assert block.active_time_s == pytest.approx(2100.0, abs=1e-9)
    hand_windows = [(300, 678), (679, 1034), (1035, 1335), (1336, 1500), (1501, 1823), (1824, 2134), (2135, 2400)]
    inside = [row for row in stream if any(a <= row["t_start"] < b for a, b in hand_windows)]
    T = sum(row["dt"] for row in inside)
    gap_m = sum(row["contributed_m"] * row["g"] for row in inside)
    assert block.gap_pace_s_per_km == pytest.approx(1000.0 * T / gap_m, rel=1e-12)
    graded = sum(row["contributed_m"] for row in inside if row["graded"])
    assert block.gap_coverage == pytest.approx(graded / sum(row["contributed_m"] for row in inside), rel=1e-12)


# -- Row 1a and row 2 (AC2's lap rows) ------------------------------------------------------------


def _base() -> tuple[list[dict], list[dict]]:
    laps = [
        {"lap_index": 0, "start_t": 0.0, "end_t": 300.0, "timer_s": 300.0, "intensity": "warmup", "wkt_step_index": 0},
        {"lap_index": 1, "start_t": 300.0, "end_t": 900.0, "timer_s": 600.0, "intensity": "active", "wkt_step_index": 1},
    ]
    steps = [
        {"step_index": 0, "intensity": "warmup", "duration_type": "time"},
        {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
    ]
    return laps, steps


def test_an_active_lap_with_a_null_step_index_is_never_active():
    laps, steps = _base()
    laps[1].update(wkt_step_index=None, start_t=None, end_t=None, timer_s=None)
    result = TP.active_laps(laps, steps)
    print("null step index:", result)
    assert result.unavailable == TP.NO_WORKOUT_STEPS
    assert result.laps == ()
    laps[1].update(start_t=300.0, end_t=900.0, timer_s=600.0)
    assert TP.active_laps(laps, steps).unavailable == TP.NO_WORKOUT_STEPS


def test_an_active_lap_naming_a_non_active_step_is_not_active():
    laps, steps = _base()
    laps[1]["wkt_step_index"] = 0  # the warm-up step
    result = TP.active_laps(laps, steps)
    print("non-active step:", result)
    assert result.unavailable == TP.NO_WORKOUT_STEPS
    assert result.active_time_s is None and result.windows == () and result.active_blocks == 0


def test_a_lap_ending_before_it_starts_is_lap_times_unreadable():
    laps, steps = _base()
    laps[1]["end_t"] = 299.0
    result = TP.active_laps(laps, steps)
    print("end before start:", result)
    assert result.unavailable == TP.LAP_TIMES_UNREADABLE
    assert result.laps == () and result.active_time_s is None


def _perturb(**changes):
    def apply(laps: list[dict], steps: list[dict]) -> None:
        laps[1].update(changes)

    return apply


def _set_steps(new_steps: list[dict]):
    def apply(laps: list[dict], steps: list[dict]) -> None:
        steps[:] = new_steps

    return apply


def _add_readable_active_lap(laps: list[dict], steps: list[dict]) -> None:
    laps[1]["timer_s"] = None
    laps.append(
        {"lap_index": 2, "start_t": 900.0, "end_t": 1500.0, "timer_s": 600.0, "intensity": "active", "wkt_step_index": 1}
    )


def _null_warmup_times(laps: list[dict], steps: list[dict]) -> None:
    laps[0].update(start_t=None, end_t=None, timer_s=None)


ACTIVE_OK = "active"
UNREADABLE = "lap_times_unreadable"  # the reference's reason strings, literally
NONE_ACTIVE = "no_workout_steps"
HUGE = 10**400

CONTRACT = [
    ("1 none", lambda laps, steps: None, ACTIVE_OK),
    *[(f"2 start_t {v!r}", _perturb(start_t=v), UNREADABLE) for v in (None, math.nan, math.inf, -math.inf, True, "0", HUGE)],
    *[(f"3 end_t {v!r}", _perturb(end_t=v), UNREADABLE) for v in (None, math.nan, math.inf, True, "600", HUGE)],
    *[(f"4 timer_s {v!r}", _perturb(timer_s=v), UNREADABLE) for v in (None, math.nan, math.inf, False, "600", HUGE)],
    ("5 timer_s negative", _perturb(timer_s=-1.0), UNREADABLE),
    ("6 end_t == start_t", _perturb(end_t=300.0), UNREADABLE),
    ("7 end_t < start_t", _perturb(end_t=100.0), UNREADABLE),
    ("8 null step index", _perturb(wkt_step_index=None, start_t=None, end_t=None, timer_s=None), NONE_ACTIVE),
    ("9 warm-up step", _perturb(wkt_step_index=0), NONE_ACTIVE),
    ("10 missing step", _perturb(wkt_step_index=99), NONE_ACTIVE),
    *[(f"11 step index {v!r}", _perturb(wkt_step_index=v), NONE_ACTIVE) for v in (True, "1", 1.0)],
    *[(f"12 intensity {v!r}", _perturb(intensity=v), NONE_ACTIVE) for v in ("interval", None, "Active")],
    (
        "13 duplicate active",
        _set_steps(
            [
                {"step_index": 0, "intensity": "warmup", "duration_type": "time"},
                {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
                {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
            ]
        ),
        ACTIVE_OK,
    ),
    (
        "14 duplicate mixed",
        _set_steps(
            [
                {"step_index": 0, "intensity": "warmup", "duration_type": "time"},
                {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
                {"step_index": 1, "intensity": "recovery", "duration_type": "time"},
            ]
        ),
        NONE_ACTIVE,
    ),
    (
        "15 repeat step active",
        _set_steps(
            [
                {"step_index": 0, "intensity": "warmup", "duration_type": "time"},
                {"step_index": 1, "intensity": "active", "duration_type": "repeat_until_steps_cmplt", "target_type": "speed"},
            ]
        ),
        NONE_ACTIVE,
    ),
    ("16 one unreadable beside a readable", _add_readable_active_lap, UNREADABLE),
    ("17 warm-up times null", _null_warmup_times, ACTIVE_OK),
]


@pytest.mark.parametrize(("row", "perturb", "expected"), CONTRACT, ids=[row[0] for row in CONTRACT])
def test_contract_table(row, perturb, expected):
    laps, steps = _base()
    perturb(laps, steps)
    result = TP.active_laps(laps, steps)
    print(row, "->", result.unavailable, result.active_time_s)
    if expected == ACTIVE_OK:
        assert result.unavailable is None
        assert result.active_time_s == 600.0
        assert result.windows == ((300.0, 900.0),)
        assert result.active_blocks == 1
    else:
        assert result.unavailable == expected
        assert result.laps == () and result.windows == () and result.active_time_s is None


def test_a_zero_timer_lap_reads_and_adds_nothing():
    """A lap with ``timer_s`` 0 and its end after its start is active: its window stays, ``A`` is 0."""
    laps, steps = _base()
    laps[1]["timer_s"] = 0.0
    result = TP.active_laps(laps, steps)
    assert result.unavailable is None
    assert result.active_time_s == 0.0
    assert result.windows == ((300.0, 900.0),)
    assert result.active_blocks == 1


def test_active_block_carries_the_lap_reason_and_no_numbers():
    laps, steps = _base()
    laps[1]["end_t"] = 300.0
    block = TP.active_block(laps, steps, _segments())
    assert block.unavailable == TP.LAP_TIMES_UNREADABLE
    assert block.gap_pace_s_per_km is None and block.active_time_s is None
    assert block.gap_coverage is None and block.gps_degraded_fraction is None


def test_blocks_count_runs_of_consecutive_active_laps():
    """3 x 10 min with jogs reads 3; one continuous step split by auto-laps reads 1 (reference section 5)."""
    steps = [
        {"step_index": 0, "intensity": "active", "duration_type": "time", "target_type": "speed"},
        {"step_index": 1, "intensity": "recovery", "duration_type": "time"},
    ]

    def lap(index: int, step: int, intensity: str) -> dict:
        return {
            "lap_index": index,
            "start_t": index * 100.0,
            "end_t": index * 100.0 + 100.0,
            "timer_s": 100.0,
            "intensity": intensity,
            "wkt_step_index": step,
        }

    intervals = [lap(0, 0, "active"), lap(1, 1, "recovery"), lap(2, 0, "active"), lap(3, 1, "recovery"), lap(4, 0, "active")]
    assert TP.active_laps(intervals, steps).active_blocks == 3
    split = [lap(0, 0, "active"), lap(1, 0, "active"), lap(2, 0, "active")]
    assert TP.active_laps(split, steps).active_blocks == 1


def test_window_fractions_cover_the_active_windows_only():
    rows = _segments()
    for row in rows:
        if 600.0 <= row["t_start"] < 900.0:  # the first half of the first active lap
            row["gps_degraded"] = True
            row["graded"] = False
        if row["t_start"] < 600.0:  # the warm-up: degraded and ungraded, outside every window
            row["gps_degraded"] = True
            row["graded"] = False
    block = TP.active_block(_laps([0, 1, 2, 3, 4]), _simple_steps(), rows)
    assert block.gps_degraded_fraction == pytest.approx(0.25, rel=1e-12)
    assert block.gap_coverage == pytest.approx(0.75, rel=1e-12)


def test_window_fractions_are_shares_of_metres_not_of_graded_metres_time_or_count():
    """Graded, mixed-speed segments, some degraded, inside and outside the one active window
    ``[300, 900)``. By hand, over the three inside segments (800 m):

    - degraded metres 250 + 150 = 400, so ``gps_degraded_fraction`` = 400 / 800 = 0.5
      (grade-weighted it would be (275 + 195) / 830 = 0.566, by time 150 / 250 = 0.6, by count 2 / 3);
    - graded metres 250 + 400 = 650, so ``gap_coverage`` = 650 / 800 = 0.8125;
    - pace = 1000 * 250 s / (275 + 360 + 195) m = 301.2 s/km.
    """

    def seg(t_start, dt, metres, g, *, degraded, graded=True):
        return {
            "t_start": t_start,
            "dt": dt,
            "contributed_m": metres,
            "i": 0.0,
            "graded": graded,
            "g": g,
            "moving_gap": False,
            "gps_degraded": degraded,
        }

    rows = [
        seg(0.0, 10.0, 30.0, 1.2, degraded=True),  # the warm-up, outside
        seg(300.0, 100.0, 250.0, 1.10, degraded=True),
        seg(400.0, 100.0, 400.0, 0.90, degraded=False),
        seg(500.0, 50.0, 150.0, 1.30, degraded=True, graded=False),
        seg(900.0, 200.0, 500.0, 1.0, degraded=True, graded=False),  # at the window's end: outside
    ]
    block = TP.active_block(*_base(), rows)
    print("fractions:", block.gps_degraded_fraction, block.gap_coverage, block.gap_pace_s_per_km)
    assert block.gps_degraded_fraction == pytest.approx(400.0 / 800.0, rel=1e-12)
    assert block.gap_coverage == pytest.approx(650.0 / 800.0, rel=1e-12)
    assert block.gap_pace_s_per_km == pytest.approx(1000.0 * 250.0 / 830.0, rel=1e-12)


def test_no_segment_in_the_windows_reads_no_pace():
    block = TP.active_block(*_base(), [])
    assert block.unavailable is None and block.active_time_s == 600.0
    assert block.gap_pace_s_per_km is None
    assert block.gap_coverage is None and block.gps_degraded_fraction is None


# -- segment_rows' gps_degraded --------------------------------------------------------------------


def test_segment_rows_carry_gps_degraded_last():
    rows = []
    for k in range(40):
        rows.append(
            {
                "t": float(k),
                "distance": 3.0 * k,
                "speed": 3.0,
                "altitude": 0.03 * 3.0 * k,
                "sample_quality": ["gps_degraded"] if 10 <= k < 20 else [],
                # The bool column alone is not the tag: the rule reads the sample_quality tag.
                "gps_degraded": 25 <= k < 30,
            }
        )
    stream = SF.segment_rows({"session_id": "s", "sport": "running", "quality_flags": [], "context": {}}, rows)
    assert stream
    for row in stream:
        assert list(row)[-1] == "gps_degraded"
        k = int(row["t_start"])
        assert row["gps_degraded"] is (10 <= k < 20), k
