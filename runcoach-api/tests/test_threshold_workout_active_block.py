"""F027 AC3, AC4 and AC5 (and F026 AC3's window rule): the sustained work reps of a threshold
workout, as pure functions.

``threshold_pace.active_laps(laps, steps)`` decides F027 reference section 4 orders 3, 4 and 5
from the laps and steps alone (the work laps and their reps, section 3);
``threshold_pace.active_block(laps, steps, segment_rows)`` adds orders 8 to 11: each sustained
rep's GAP pace over F013's per-segment stream, the band, the counted time ``A`` and the pace and
window fractions over the counted windows. No database: laps, steps and segment rows are plain
dicts, except the ``sample_run.fit`` case, which goes through ``mapping.to_laps``,
``mapping.to_canonical``, ``quality_gates.apply`` and ``session_features.segment_rows``.

**Contract table** (rule contract-tables-need-an-independent-oracle): authored from the F026
reference section 3 before that implementation existed, re-pointed row by row to F027 reference
sections 3 and 4 (the step-target and ``interval`` rules), and driven by ``test_contract_table``.
Each row perturbs the one work lap of a base workout (a warm-up lap, then one ``active`` lap of
600 s naming work step 1, a step with a speed target) and names the result. "otherwise work"
means a lap whose intensity and step index make it a work lap, before its times are read.

| # | perturbation of the work lap | result |
|---|---|---|
| 1 | none | work, one rep of 600 s, window [300, 900) |
| 2 | ``start_t`` None, NaN, inf, -inf, True, "0", 10**400 | ``lap_times_unreadable`` |
| 3 | ``end_t`` None, NaN, inf, True, "600", 10**400 | ``lap_times_unreadable`` |
| 4 | ``timer_s`` None, NaN, inf, False, "600", 10**400 | ``lap_times_unreadable`` |
| 5 | ``timer_s`` negative (-1) | ``lap_times_unreadable`` (a lap time below zero does not read) |
| 5b | ``timer_s`` zero, ``end_t`` after ``start_t`` | reads as a work lap of 0 s, so no rep reaches 290 s: ``no_sustained_work``; ``test_a_zero_timer_lap_reads_and_adds_nothing`` |
| 6 | ``end_t == start_t`` with ``timer_s`` 600 | ``lap_times_unreadable`` (not a remainder lap: its timer is 2 s or more) |
| 7 | ``end_t < start_t`` | ``lap_times_unreadable`` |
| 8 | ``wkt_step_index`` None (with its times null too) | not work: ``no_workout_steps`` (order 3 never reads it) |
| 9 | ``wkt_step_index`` names the warm-up step | not work: ``no_workout_steps`` |
| 10 | ``wkt_step_index`` names no step (99) | not work: ``no_workout_steps`` |
| 11 | ``wkt_step_index`` True, "1" or 1.0 (not an int) | not work: ``no_workout_steps`` (True would otherwise name step 1) |
| 12 | lap intensity ``interval`` | work (Garmin Coach writes its work steps and laps ``interval``) |
| 12 | lap intensity None or ``Active`` | not work: ``no_workout_steps`` |
| 13 | two step rows share index 1, both work | not work: ``no_workout_steps`` (a shared index names no work step; the store refuses a duplicate step index, so two rows are a defect) |
| 14 | two steps share index 1, one work and one recovery | not work: ``no_workout_steps`` |
| 15 | step 1 is a repeat step (``repeat_until_steps_cmplt``) carrying intensity ``active`` and a target | not work: ``no_workout_steps`` (a repeat step is never work, whatever it carries) |
| 16 | a second, readable work lap beside an unreadable one | ``lap_times_unreadable`` (one unreadable work lap excludes, never shrinks the rep) |
| 17 | the warm-up lap (not work) has null times | work, one rep of 600 s (order 3 reads work laps only) |

**Adversarial probe table** (rule adversarial-input-probes-are-a-task-deliverable) for the new
predicates, each driven by the test named; the argument for each outcome is in that test's
docstring.

| input | where | routed |
|---|---|---|
| ``timer_s`` 0.0, 1.999 and -0.0 with ``end_t == start_t`` | ``test_a_remainder_lap_adds_to_its_rep_and_gives_no_window`` | a remainder lap: its time added, no window; -0.0 is not below zero |
| ``timer_s`` 2.0 or -0.1 with equal stamps; ``end_t < start_t``; ``timer_s`` -0.1; NaN, bool, None or a string on any of the three | ``test_unreadable_work_lap_times_read_lap_times_unreadable`` | ``lap_times_unreadable`` |
| the same faults on the cool-down lap | ``test_a_bad_cool_down_lap_changes_nothing`` | nothing changes |
| rep lengths 289.99 and 290 beside a 400 s rep | ``test_the_rep_minimum_at_289_99_and_290`` | A 400 (``active_time_below_minimum``) and A 690 |
| a lone 289.99 s rep | ``test_a_lone_short_rep_reads_no_sustained_work`` | ``no_sustained_work`` |
| rep paces 275 and 275.1 s/km beside 250, flat | ``test_the_band_counts_275_and_drops_275_1`` | 275 counts, 275.1 is dropped (``<= 1.10 * 250``) |
| counted reps summing to 589.99 and 590 | ``test_counted_reps_at_589_99_and_590`` | ``active_time_below_minimum`` and A 590 |
| a rep with no segment; one whose metres overflow (pace 0) | ``test_a_rep_without_a_finite_pace_is_dropped_before_the_band`` | dropped at order 8; none left is ``gap_pace_unavailable`` |
| two laps of ``timer_s`` 1e308 on one step (an inf sum) | ``test_a_counted_time_that_overflows_to_inf_reads_below_minimum`` (the pure seam) and ``test_threshold_workout_qualifying.test_an_active_time_that_overflows_to_inf_is_refused`` (the route) | one rep of inf, sustained; A inf is not finite: ``active_time_below_minimum`` |
| step ``target_type`` None, ``open`` or missing | ``test_an_open_or_missing_target_is_never_work`` | not work |
| every ``repeat_until_*`` duration type | ``test_every_repeat_type_is_never_work`` | not work |

**The window boundary.** A segment belongs to the window its ``t_start`` lies in, ``[start_t,
end_t)``, and counts there in full: a moving dropout over 5 s that starts inside a work lap
counts all its ``dt`` and metres there even when it ends after the lap; one that starts in a
recovery lap and ends inside a work lap counts nowhere. ``rep_paces`` walks the stream once
against the sorted windows; ``test_rep_paces_match_a_brute_force_oracle`` checks it against a
per-rep scan.
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
    name work steps 1 and 2 (reps of 1200 s and 900 s); lap 9 is the step-less ``session_end`` lap.

    Fragile, and not a pin of the band: step 1 reads about 331.38 s/km GAP and step 2 about
    301.47, a ratio of 1.0992, inside the 1.10 band by under a percent, so both reps count and the
    values stay 2100 s, 7 windows, 1 block. Never use this file's served values to pin the band;
    ``test_the_band_counts_275_and_drops_275_1`` does that on exact synthetic reps."""
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


# -- Orders 3 and 4 (the lap rows of F026 AC2, re-pointed) ---------------------------------------


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
    assert result.laps == () and result.reps == ()


def test_a_lap_ending_before_it_starts_is_lap_times_unreadable():
    laps, steps = _base()
    laps[1]["end_t"] = 299.0
    result = TP.active_laps(laps, steps)
    print("end before start:", result)
    assert result.unavailable == TP.LAP_TIMES_UNREADABLE
    assert result.laps == () and result.reps == ()


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


ACTIVE_OK = "work"
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
    ("12 intensity 'interval'", _perturb(intensity="interval"), ACTIVE_OK),
    *[(f"12 intensity {v!r}", _perturb(intensity=v), NONE_ACTIVE) for v in (None, "Active")],
    (
        "13 duplicate work rows",
        _set_steps(
            [
                {"step_index": 0, "intensity": "warmup", "duration_type": "time"},
                {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
                {"step_index": 1, "intensity": "active", "duration_type": "time", "target_type": "speed"},
            ]
        ),
        NONE_ACTIVE,
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
    print(row, "->", result.unavailable, result.reps)
    if expected == ACTIVE_OK:
        assert result.unavailable is None
        assert [(rep.step_index, rep.length_s, rep.windows) for rep in result.reps] == [(1, 600.0, ((300.0, 900.0),))]
        assert len(result.laps) == 1
    else:
        assert result.unavailable == expected
        assert result.laps == () and result.reps == ()


def test_a_zero_timer_lap_reads_and_adds_nothing():
    """A work lap with ``timer_s`` 0 and its end after its start reads (only below zero does not):
    its rep is 0 s long and keeps its window, so alone it is ``no_sustained_work``; beside a 600 s
    lap of the same step it adds nothing to that rep."""
    laps, steps = _base()
    laps[1]["timer_s"] = 0.0
    result = TP.active_laps(laps, steps)
    assert result.unavailable == TP.NO_SUSTAINED_WORK
    assert [(rep.length_s, rep.windows) for rep in result.reps] == [(0.0, ((300.0, 900.0),))]
    laps.append(
        {"lap_index": 2, "start_t": 900.0, "end_t": 1500.0, "timer_s": 600.0, "intensity": "active", "wkt_step_index": 1}
    )
    result = TP.active_laps(laps, steps)
    assert result.unavailable is None
    assert [(rep.length_s, rep.windows) for rep in result.reps] == [(600.0, ((300.0, 900.0), (900.0, 1500.0)))]


def test_active_block_carries_the_lap_reason_and_no_numbers():
    laps, steps = _base()
    laps[1]["end_t"] = 300.0
    block = TP.active_block(laps, steps, _segments())
    assert block.unavailable == TP.LAP_TIMES_UNREADABLE
    assert block.gap_pace_s_per_km is None and block.active_time_s is None
    assert block.gap_coverage is None and block.gps_degraded_fraction is None


def test_blocks_count_runs_of_consecutive_active_laps():
    """3 x 5 min with jogs reads 3; one continuous step split by auto-laps reads 1 (reference
    section 5). The laps are 300 s, so every rep is sustained and counts at one pace; the blocks
    are ``active_block``'s, counted over the reps that count."""
    steps = [
        {"step_index": 0, "intensity": "active", "duration_type": "time", "target_type": "speed"},
        {"step_index": 1, "intensity": "recovery", "duration_type": "time"},
    ]

    def lap(index: int, step: int, intensity: str) -> dict:
        return {
            "lap_index": index,
            "start_t": index * 300.0,
            "end_t": index * 300.0 + 300.0,
            "timer_s": 300.0,
            "intensity": intensity,
            "wkt_step_index": step,
        }

    intervals = [lap(0, 0, "active"), lap(1, 1, "recovery"), lap(2, 0, "active"), lap(3, 1, "recovery"), lap(4, 0, "active")]
    assert TP.active_block(intervals, steps, _stream(intervals, {0: 270.0, 2: 270.0, 4: 270.0})).active_blocks == 3
    split = [lap(0, 0, "active"), lap(1, 0, "active"), lap(2, 0, "active")]
    assert TP.active_block(split, steps, _stream(split, {0: 270.0, 1: 270.0, 2: 270.0})).active_blocks == 1


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
    """No segment in the one sustained rep's window: its pace is dropped at order 8 and none is
    left, ``gap_pace_unavailable`` with every number empty."""
    block = TP.active_block(*_base(), [])
    assert block.unavailable == TP.GAP_PACE_UNAVAILABLE
    assert block.active_time_s is None and block.gap_pace_s_per_km is None
    assert block.gap_coverage is None and block.gps_degraded_fraction is None and block.windows == ()


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


# -- Sustained work reps (F027 reference sections 3 and 4) -----------------------------------------
#
# Expected values below are written by hand from the reference before the rule was changed: rep
# lengths are sums of the laps' timer_s, A the counted reps' sum, and every pace is the flat-route
# formula 1000 * T / D typed out, never imported.


def _lap(index: int, start: float, seconds: float, intensity: str, step: int | None, **over) -> dict:
    lap = {
        "lap_index": index,
        "start_t": start,
        "end_t": start + seconds,
        "timer_s": float(seconds),
        "intensity": intensity,
        "wkt_step_index": step,
    }
    lap.update(over)
    return lap


def _plan_laps(plan) -> list[dict]:
    """``(intensity, seconds, step)`` per lap, laid end to end from t = 0."""
    laps, t = [], 0.0
    for index, (intensity, seconds, step) in enumerate(plan):
        laps.append(_lap(index, t, seconds, intensity, step))
        t += seconds
    return laps


def _step(index: int, intensity: str, duration_type: str = "time", **over) -> dict:
    return {"step_index": index, "intensity": intensity, "duration_type": duration_type, **over}


WORK = {"target_type": "speed"}
HR = {"target_type": "heart_rate"}
OPEN = {"target_type": "open"}


def _segment(t_start: float, dt: float, metres: float, g: float = 1.0, *, graded: bool = True, degraded: bool = False) -> dict:
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


def _stream(laps: list[dict], pace_by_lap: dict[int, float]) -> list[dict]:
    """1 Hz flat counted segments over the laps named in ``pace_by_lap`` (by ``lap_index``), each
    second at that lap's pace; laps not named give no segment."""
    rows = []
    for lap in laps:
        pace = pace_by_lap.get(lap["lap_index"])
        if pace is None:
            continue
        start, end = lap["start_t"], lap["end_t"]
        if not all(isinstance(x, float) and math.isfinite(x) for x in (start, end)):
            continue  # a lap whose times do not read gets no segment (a perturbed lap under test)
        v = 1000.0 / pace
        t = start
        while t < end:
            rows.append(_segment(t, 1.0, v))
            t += 1.0
    return rows


def _reps(decided) -> list[tuple[int, float, tuple]]:
    return [(rep.step_index, rep.length_s, rep.windows) for rep in decided.reps]


def _three_steps() -> list[dict]:
    return [_step(0, "warmup"), _step(1, "active", **WORK), _step(2, "recovery"), _step(3, "cooldown")]


def test_interval_laps_and_steps_are_work():
    """Garmin Coach writes its work as ``interval`` with a heart-rate target: lap and step
    intensity ``interval`` are work, in every pairing with ``active``."""
    for lap_intensity, step_intensity in (("interval", "interval"), ("interval", "active"), ("active", "interval")):
        steps = [_step(0, "warmup"), _step(1, step_intensity, **HR), _step(2, "recovery")]
        laps = _plan_laps([("warmup", 300, 0), (lap_intensity, 600, 1), ("recovery", 120, 2)])
        decided = TP.active_laps(laps, steps)
        print(lap_intensity, step_intensity, "->", decided)
        assert decided.unavailable is None
        assert _reps(decided) == [(1, 600.0, ((300.0, 900.0),))]
        block = TP.active_block(laps, steps, _stream(laps, {1: 270.0}))
        assert block.unavailable is None
        assert (block.active_time_s, block.active_blocks, block.windows) == (600.0, 1, ((300.0, 900.0),))
        assert block.gap_pace_s_per_km == pytest.approx(270.0, rel=1e-9)


@pytest.mark.parametrize(("lap_intensity", "step_intensity"), [("active", "interval"), ("interval", "active")])
def test_a_lap_and_its_step_need_not_share_an_intensity(lap_intensity: str, step_intensity: str):
    """The rule the CHANGELOG and spec/04 state: a work lap is a lap of intensity ``active`` or
    ``interval`` under a workout step of intensity ``active`` or ``interval`` (not a repeat step,
    target neither null nor open). The lap and the step are checked apart, so an ``active`` lap
    under an ``interval`` step with a speed target counts, and the reverse; a rule reading "a step
    of the same intensity" would drop both."""
    steps = [_step(0, "warmup"), _step(1, step_intensity, **WORK), _step(2, "recovery")]
    laps = _plan_laps([("warmup", 300, 0), (lap_intensity, 600, 1), ("recovery", 120, 2)])
    block = TP.active_block(laps, steps, _stream(laps, {1: 270.0}))
    print(lap_intensity, "under", step_intensity, "->", block)
    assert block.unavailable is None
    assert (block.active_time_s, block.active_blocks, block.windows) == (600.0, 1, ((300.0, 900.0),))
    assert block.gap_pace_s_per_km == pytest.approx(270.0, rel=1e-9)


@pytest.mark.parametrize("target", [{"target_type": None}, OPEN, {}], ids=["null", "open", "missing"])
def test_an_open_or_missing_target_is_never_work(target: dict):
    """A step with no target or an open one (the watch's recovery jogs) is never work: alone it
    reads ``no_workout_steps``; beside a work step its laps are left out of every rep."""
    steps = [_step(0, "warmup"), _step(1, "active", **target), _step(2, "recovery")]
    laps = _plan_laps([("warmup", 300, 0), ("active", 600, 1), ("recovery", 120, 2)])
    decided = TP.active_laps(laps, steps)
    assert decided.unavailable == TP.NO_WORKOUT_STEPS and decided.laps == () and decided.reps == ()
    steps.append(_step(3, "active", **WORK))
    laps.append(_lap(3, 1020.0, 600, "active", 3))
    decided = TP.active_laps(laps, steps)
    assert decided.unavailable is None
    assert _reps(decided) == [(3, 600.0, ((1020.0, 1620.0),))]


@pytest.mark.parametrize(
    "duration_type",
    ["repeat_until_steps_cmplt", "repeat_until_time", "repeat_until_distance", "repeat_until_hr_less_than"],
)
def test_every_repeat_type_is_never_work(duration_type: str):
    """A repeat step loops over other steps; whatever its intensity and target, it is never work."""
    steps = [_step(0, "warmup"), _step(1, "active", duration_type, **WORK)]
    laps = _plan_laps([("warmup", 300, 0), ("active", 600, 1)])
    decided = TP.active_laps(laps, steps)
    print(duration_type, "->", decided)
    assert decided.unavailable == TP.NO_WORKOUT_STEPS and decided.reps == ()


def test_a_shared_step_index_names_no_work_step():
    """An index carried by two step rows names no work step: a work and a non-work row (the
    reference's case), and two work rows (a duplicate the store refuses; here a defect, not two
    steps). An unshared work step beside them still reads."""
    laps = _plan_laps([("warmup", 300, 0), ("active", 600, 1), ("recovery", 120, 2), ("active", 600, 3)])
    mixed = [_step(0, "warmup"), _step(1, "active", **WORK), _step(1, "recovery"), _step(2, "recovery")]
    decided = TP.active_laps(laps, mixed)
    assert decided.unavailable == TP.NO_WORKOUT_STEPS
    twice = [_step(0, "warmup"), _step(1, "active", **WORK), _step(1, "active", **WORK), _step(2, "recovery")]
    assert TP.active_laps(laps, twice).unavailable == TP.NO_WORKOUT_STEPS
    with_unshared = [*twice, _step(3, "active", **WORK)]
    decided = TP.active_laps(laps, with_unshared)
    assert decided.unavailable is None
    assert _reps(decided) == [(3, 600.0, ((1020.0, 1620.0),))]


def test_reps_of_a_repeated_step_stay_separate():
    """8 x (300 s work, 120 s recovery) on one repeated work step: 8 reps of 300 s, not one of 2400,
    and 8 blocks when all count."""
    plan = [("warmup", 300, 0)]
    for _ in range(8):
        plan += [("active", 300, 1), ("recovery", 120, 2)]
    laps = _plan_laps(plan)
    steps = _three_steps()
    decided = TP.active_laps(laps, steps)
    assert decided.unavailable is None
    assert [(rep.step_index, rep.length_s) for rep in decided.reps] == [(1, 300.0)] * 8
    assert [len(rep.windows) for rep in decided.reps] == [1] * 8
    block = TP.active_block(laps, steps, _stream(laps, {lap["lap_index"]: 270.0 for lap in laps if lap["intensity"] == "active"}))
    assert block.unavailable is None
    assert (block.active_time_s, block.active_blocks, len(block.windows)) == (2400.0, 8, 8)


def test_a_step_repeated_back_to_back_merges_into_one_rep():
    """The 2023-03-29 Run Workout: 2 x 480 s on one step with nothing between them reads one 960 s
    rep (the laps carry nothing that separates the repetitions), two windows, one block."""
    laps = _plan_laps([("warmup", 300, 0), ("active", 480, 1), ("active", 480, 1), ("cooldown", 300, 3)])
    steps = _three_steps()
    decided = TP.active_laps(laps, steps)
    assert decided.unavailable is None
    assert _reps(decided) == [(1, 960.0, ((300.0, 780.0), (780.0, 1260.0)))]
    block = TP.active_block(laps, steps, _stream(laps, {1: 270.0, 2: 270.0}))
    assert (block.active_time_s, block.active_blocks) == (960.0, 1)
    assert block.windows == ((300.0, 780.0), (780.0, 1260.0))


@pytest.mark.parametrize(
    ("timer", "length"),
    [(0.0, 600.0), (1.999, 601.999), (-0.0, 600.0)],
    ids=["timer-0", "timer-1.999", "timer-negative-zero"],
)
def test_a_remainder_lap_adds_to_its_rep_and_gives_no_window(timer: float, length: float):
    """The 2026-10-16 Speed Repeats' shape: a work lap closed on the step's end with ``end_t ==
    start_t`` and under 2 s of timer adds its time to the rep and gives no window; -0.0 is not
    below zero. The rep's 600 s lap keeps ``A`` at the bound, so the block reads too."""
    laps = [
        _lap(0, 0.0, 300, "warmup", 0),
        _lap(1, 300.0, 600, "active", 1),
        _lap(2, 900.0, 0, "active", 1, timer_s=timer),
        _lap(3, 900.0, 300, "cooldown", 3),
    ]
    assert laps[2]["start_t"] == laps[2]["end_t"] == 900.0
    steps = _three_steps()
    decided = TP.active_laps(laps, steps)
    print(timer, "->", decided)
    assert decided.unavailable is None
    assert _reps(decided) == [(1, length, ((300.0, 900.0),))]
    assert len(decided.laps) == 2
    block = TP.active_block(laps, steps, _stream(laps, {1: 270.0}))
    assert block.unavailable is None
    assert (block.active_time_s, block.active_blocks, block.windows) == (length, 1, ((300.0, 900.0),))


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"start_t": 600.0, "end_t": 600.0, "timer_s": 2.0}, id="equal-stamps-timer-2.0"),
        pytest.param({"start_t": 600.0, "end_t": 600.0, "timer_s": -0.1}, id="equal-stamps-timer-negative"),
        pytest.param({"end_t": 599.0}, id="end-before-start"),
        pytest.param({"timer_s": -0.1}, id="timer-negative"),
        pytest.param({"timer_s": math.nan}, id="timer-nan"),
        pytest.param({"start_t": math.nan}, id="start-nan"),
        pytest.param({"end_t": math.inf}, id="end-inf"),
        pytest.param({"timer_s": True}, id="timer-bool"),
        pytest.param({"start_t": False}, id="start-bool"),
        pytest.param({"timer_s": None}, id="timer-null"),
        pytest.param({"timer_s": "300"}, id="timer-string"),
    ],
)
def test_unreadable_work_lap_times_read_lap_times_unreadable(bad: dict):
    """On a work lap, ``timer_s`` 2.0 with equal stamps (not a remainder), ``end_t < start_t``,
    ``timer_s`` -0.1, a NaN, a bool, a null or a string: ``lap_times_unreadable``, never a smaller
    rep, even when another rep is sustained."""
    laps = _plan_laps([("warmup", 300, 0), ("active", 300, 1), ("active", 300, 1), ("recovery", 120, 2), ("active", 600, 1)])
    laps[2].update(bad)
    steps = _three_steps()
    decided = TP.active_laps(laps, steps)
    print(bad, "->", decided)
    assert decided.unavailable == TP.LAP_TIMES_UNREADABLE
    assert decided.laps == () and decided.reps == ()
    block = TP.active_block(laps, steps, _stream(laps, {1: 270.0, 2: 270.0, 4: 270.0}))
    assert block.unavailable == TP.LAP_TIMES_UNREADABLE
    assert block.active_time_s is None and block.gap_pace_s_per_km is None and block.windows == ()


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"end_t": 0.0}, id="end-before-start"),
        pytest.param({"timer_s": -1.0}, id="timer-negative"),
        pytest.param({"start_t": math.nan, "end_t": math.nan, "timer_s": math.nan}, id="all-nan"),
        pytest.param({"timer_s": True}, id="timer-bool"),
        pytest.param({"start_t": None, "end_t": None, "timer_s": None}, id="all-null"),
    ],
)
def test_a_bad_cool_down_lap_changes_nothing(bad: dict):
    """Lap times are screened on work laps only: the same fault on the cool-down lap leaves the
    reading as it is without it."""
    laps = _plan_laps([("warmup", 300, 0), ("active", 600, 1), ("cooldown", 300, 3)])
    steps = _three_steps()
    clean = TP.active_block(laps, steps, _stream(laps, {1: 270.0}))
    laps[2].update(bad)
    broken = TP.active_block(laps, steps, _stream(laps, {1: 270.0}))
    assert clean.unavailable is None
    assert broken == clean
    assert TP.active_laps(laps, steps) == TP.active_laps(_plan_laps([("warmup", 300, 0), ("active", 600, 1), ("cooldown", 300, 3)]), steps)


def _exact_rep_stream(laps: list[dict], metres_by_lap: dict[int, float]) -> list[dict]:
    """One counted segment per named lap, ``dt`` the lap's whole timer and the given metres, so a
    rep's pace is ``1000 * timer / metres`` in one division (float-exact bounds)."""
    return [
        _segment(float(lap["start_t"]), float(lap["timer_s"]), metres_by_lap[lap["lap_index"]])
        for lap in laps
        if lap["lap_index"] in metres_by_lap
    ]


def test_the_rep_minimum_at_289_99_and_290():
    """A 400 s rep plus a 289.99 s rep at one pace: only the 400 s rep is sustained, so A = 400 and
    the block reads ``active_time_below_minimum``; with 290.0 s instead both are, A = 690."""
    for second, expected_a, reason in ((289.99, 400.0, TP.ACTIVE_TIME_BELOW_MINIMUM), (290.0, 690.0, None)):
        laps = _plan_laps([("warmup", 300, 0), ("active", 400, 1), ("recovery", 120, 2), ("active", second, 1)])
        steps = _three_steps()
        decided = TP.active_laps(laps, steps)
        assert decided.unavailable is None
        assert [rep.length_s for rep in decided.reps] == [400.0, second]
        # Pace 250 s/km on both reps: 4 m per second of timer.
        block = TP.active_block(laps, steps, _exact_rep_stream(laps, {1: 1600.0, 3: 4 * second}))
        print(second, "->", block)
        assert block.unavailable == reason
        if reason is None:
            assert block.active_time_s == expected_a
            assert block.windows == ((300.0, 700.0), (820.0, 820.0 + second))
            assert block.active_blocks == 2
        else:
            assert block.active_time_s is None and block.windows == ()


def test_a_lone_short_rep_reads_no_sustained_work():
    """One work rep of 289.99 s: ``no_sustained_work`` from the laps alone (order 5, before any
    record is read) and carried by the block with every number empty; 290.0 s passes."""
    laps = _plan_laps([("warmup", 300, 0), ("active", 289.99, 1), ("cooldown", 300, 3)])
    steps = _three_steps()
    decided = TP.active_laps(laps, steps)
    assert decided.unavailable == TP.NO_SUSTAINED_WORK
    assert [rep.length_s for rep in decided.reps] == [289.99]
    assert len(decided.laps) == 1
    block = TP.active_block(laps, steps, _stream(laps, {1: 270.0}))
    assert block.unavailable == TP.NO_SUSTAINED_WORK
    assert block.active_time_s is None and block.gap_pace_s_per_km is None and block.active_blocks == 0
    laps = _plan_laps([("warmup", 300, 0), ("active", 290.0, 1), ("cooldown", 300, 3)])
    assert TP.active_laps(laps, steps).unavailable is None


def test_the_band_counts_275_and_drops_275_1():
    """On a flat route beside a fastest rep of 250 s/km, a rep at 275 s/km counts and one at 275.1
    is dropped: ``rep_pace <= WORKOUT_REP_BAND * fastest_pace``, 1.10 * 250. The slower rep comes
    first, so the fastest need not lead. Paces are one division each (``_exact_rep_stream``)."""
    for slow_s, slow_m, counted in ((1100.0, 4000.0, True), (1100.4, 4000.0, False)):
        laps = _plan_laps([("warmup", 300, 0), ("active", slow_s, 1), ("recovery", 120, 2), ("active", 1000, 1)])
        steps = _three_steps()
        stream = _exact_rep_stream(laps, {1: slow_m, 3: 4000.0})
        slow_pace = 1000.0 * slow_s / slow_m
        assert slow_pace == (275.0 if counted else 275.1)
        block = TP.active_block(laps, steps, stream)
        print(slow_pace, "->", block)
        assert block.unavailable is None
        if counted:
            assert block.active_time_s == 2100.0
            assert block.windows == ((300.0, 1400.0), (1520.0, 2520.0))
            assert block.active_blocks == 2
            assert block.gap_pace_s_per_km == pytest.approx(1000.0 * 2100.0 / 8000.0, rel=1e-12)
        else:
            assert block.active_time_s == 1000.0
            assert block.windows == ((laps[3]["start_t"], laps[3]["end_t"]),)
            assert block.active_blocks == 1
            assert block.gap_pace_s_per_km == 250.0


def test_counted_reps_at_589_99_and_590():
    """Two counted reps summing to 589.99 s read ``active_time_below_minimum``; 590.0 s counts
    (the 10 s grace of order 10, as order 5's)."""
    for second, reason in ((289.99, TP.ACTIVE_TIME_BELOW_MINIMUM), (290.0, None)):
        laps = _plan_laps([("warmup", 300, 0), ("active", 300, 1), ("recovery", 120, 2), ("active", second, 1)])
        steps = _three_steps()
        block = TP.active_block(laps, steps, _exact_rep_stream(laps, {1: 1200.0, 3: 4 * second}))
        print(second, "->", block)
        assert block.unavailable == reason
        if reason is None:
            assert block.active_time_s == 590.0
            assert block.active_blocks == 2
        else:
            assert block.active_time_s is None and block.windows == () and block.active_blocks == 0


def test_a_counted_time_that_overflows_to_inf_reads_below_minimum():
    """Two adjacent work laps on one targeted step, each of ``timer_s`` 1e308 and each with a
    segment in its window: the rep's length sums to inf, which is sustained (inf >= 290) and has
    a finite pace, so the band counts it and ``A`` is inf. A counted time that is not finite is
    refused at order 10 as ``active_time_below_minimum``, never served as inf. The route's later
    screen hides this guard, so the pure seam pins it."""
    laps = [
        _lap(0, 0.0, 300, "warmup", 0),
        _lap(1, 300.0, 600, "active", 1, timer_s=1e308),
        _lap(2, 900.0, 600, "active", 1, timer_s=1e308),
    ]
    steps = _three_steps()
    decided = TP.active_laps(laps, steps)
    assert decided.unavailable is None
    assert [rep.length_s for rep in decided.reps] == [math.inf]
    block = TP.active_block(laps, steps, [_segment(300.0, 1.0, 3.7), _segment(900.0, 1.0, 3.7)])
    print("overflow:", block)
    assert block.unavailable == TP.ACTIVE_TIME_BELOW_MINIMUM
    assert block.active_time_s is None


def test_a_rep_without_a_finite_pace_is_dropped_before_the_band():
    """Three sustained reps: one with no segment (no pace), one at 300 s/km, one at 400 s/km. The
    pace-less rep is dropped at order 8, the 300 s/km rep is the fastest, the 400 s/km rep falls
    outside its band: A = 600. A rep whose metres overflow (pace 0) is dropped the same way. With
    no rep left, ``gap_pace_unavailable``."""
    laps = _plan_laps(
        [("warmup", 300, 0), ("active", 600, 1), ("recovery", 120, 2), ("active", 600, 1), ("recovery", 120, 2), ("active", 600, 1)]
    )
    steps = _three_steps()
    block = TP.active_block(laps, steps, _stream(laps, {3: 300.0, 5: 400.0}))
    print("one pace-less:", block)
    assert block.unavailable is None
    assert block.windows == ((1020.0, 1620.0),)
    assert (block.active_time_s, block.active_blocks) == (600.0, 1)
    assert block.gap_pace_s_per_km == pytest.approx(300.0, rel=1e-9)
    overflow = [*_stream(laps, {3: 300.0, 5: 400.0}), _segment(300.0, 600.0, 1e308, g=1e10)]
    assert TP.active_block(laps, steps, overflow) == block
    none_left = TP.active_block(laps, steps, [])
    assert none_left.unavailable == TP.GAP_PACE_UNAVAILABLE
    assert none_left.active_time_s is None and none_left.windows == () and none_left.active_blocks == 0


def test_the_pure_orders_decide_in_sequence():
    """Orders 3, 4 and 5 from the laps, 8 and 10 from the block: a workout failing several reads
    the first. Order 3 and 5 together (an unreadable work lap beside a short rep): 3. Order 4 and
    5 cannot both fail (no work lap, no rep): 4. Orders 8 and 10 (one sustained rep without a pace,
    A short): 8; with its pace, 10."""
    steps = _three_steps()
    laps = _plan_laps([("warmup", 300, 0), ("active", 100, 1), ("recovery", 120, 2), ("active", 100, 1)])
    laps[3]["timer_s"] = math.nan
    assert TP.active_laps(laps, steps).unavailable == TP.LAP_TIMES_UNREADABLE
    laps = _plan_laps([("warmup", 300, 0), ("recovery", 120, 2)])
    assert TP.active_laps(laps, steps).unavailable == TP.NO_WORKOUT_STEPS
    laps = _plan_laps([("warmup", 300, 0), ("active", 100, 1), ("recovery", 120, 2), ("active", 100, 1)])
    assert TP.active_laps(laps, steps).unavailable == TP.NO_SUSTAINED_WORK
    laps = _plan_laps([("warmup", 300, 0), ("active", 300, 1), ("cooldown", 300, 3)])
    assert TP.active_block(laps, steps, []).unavailable == TP.GAP_PACE_UNAVAILABLE
    assert TP.active_block(laps, steps, _stream(laps, {1: 270.0})).unavailable == TP.ACTIVE_TIME_BELOW_MINIMUM


def test_parameters_carry_the_workout_bounds():
    assert TP.WORKOUT_MIN_ACTIVE_S == 590
    assert TP.WORKOUT_MIN_REP_S == 290
    assert TP.WORKOUT_REP_BAND == 1.10
    assert TP.NO_SUSTAINED_WORK == "no_sustained_work"
    assert dict(TP.PARAMETERS) == {
        "typical_error": 0.025,
        "doubling_days": 42,
        "coverage": 0.80,
        "high_max_error": 0.025,
        "medium_max_error": 0.05,
        "short_race_s": 1800,
        "workout_min_active_s": 590,
        "workout_min_rep_s": 290,
        "workout_rep_band": 1.10,
    }


def test_speed_repeats_read_no_sustained_work():
    """AC5's Speed Repeats. 8 x 800 m (192-200 s) with open-target ``active`` jogs: the jogs are
    not work and no rep reaches 290 s. 6 x 4 min whose reps are a 1 km auto-lap plus a remainder
    (234.348 + 5.652 s; 239.125 + 0.875 s with equal stamps), then 5 x (30 s, 60 s open): reps of
    240.0 s and 30 s, ``no_sustained_work``, not ``lap_times_unreadable``."""
    steps = [_step(0, "warmup"), _step(1, "active", **WORK), _step(2, "active", **OPEN), _step(3, "cooldown")]
    plan = [("warmup", 600, 0)]
    for k in range(8):
        plan += [("active", 192 + k, 1), ("active", 240, 2)]
    plan.append(("cooldown", 600, 3))
    laps = _plan_laps(plan)
    decided = TP.active_laps(laps, steps)
    print("8 x 800:", [rep.length_s for rep in decided.reps])
    assert decided.unavailable == TP.NO_SUSTAINED_WORK
    assert [rep.length_s for rep in decided.reps] == [192.0 + k for k in range(8)]

    steps = [
        _step(0, "warmup"),
        _step(1, "active", **WORK),
        _step(2, "active", **OPEN),
        _step(3, "active", **WORK),
        _step(4, "active", **OPEN),
        _step(5, "cooldown"),
    ]
    laps, t, index = [], 0.0, 0

    def add(seconds: float, intensity: str, step: int, *, remainder: bool = False) -> None:
        nonlocal t, index
        if remainder:
            laps.append(_lap(index, t, 0.0, intensity, step, timer_s=seconds))
        else:
            laps.append(_lap(index, t, seconds, intensity, step))
            t += seconds
        index += 1

    add(600.0, "warmup", 0)
    for k in range(6):
        if k % 2 == 0:
            add(234.348, "active", 1)
            add(5.652, "active", 1)
        else:
            add(239.125, "active", 1)
            add(0.875, "active", 1, remainder=True)
        add(180.0, "active", 2)
    for _ in range(5):
        add(30.0, "active", 3)
        add(60.0, "active", 4)
    add(300.0, "cooldown", 5)
    decided = TP.active_laps(laps, steps)
    print("6 x 4 min:", [rep.length_s for rep in decided.reps])
    assert decided.unavailable == TP.NO_SUSTAINED_WORK
    assert [rep.length_s for rep in decided.reps] == pytest.approx([240.0] * 6 + [30.0] * 5, abs=1e-9)
    assert [len(rep.windows) for rep in decided.reps] == [2, 1, 2, 1, 2, 1] + [1] * 5
    assert TP.active_block(laps, steps, []).unavailable == TP.NO_SUSTAINED_WORK


def test_a_progression_run_counts_its_finish():
    """AC5's Progression Run: 60 min at 323 s/km, 10 min at 270, 5 min at 261, three work steps on
    a flat route. The finish counts (270 <= 1.10 * 261 = 287.1), the easy hour is dropped (323 >
    287.1): 900 s in 1 block at 1000 * 900 / (600 * 1000 / 270 + 300 * 1000 / 261) s/km, by hand."""
    steps = [_step(0, "active", **WORK), _step(1, "active", **WORK), _step(2, "active", **WORK)]
    laps = _plan_laps([("active", 3600, 0), ("active", 600, 1), ("active", 300, 2)])
    decided = TP.active_laps(laps, steps)
    assert [(rep.step_index, rep.length_s) for rep in decided.reps] == [(0, 3600.0), (1, 600.0), (2, 300.0)]
    block = TP.active_block(laps, steps, _stream(laps, {0: 323.0, 1: 270.0, 2: 261.0}))
    print("progression:", block)
    assert block.unavailable is None
    assert block.active_time_s == 900.0
    assert block.active_blocks == 1
    assert block.windows == ((3600.0, 4200.0), (4200.0, 4500.0))
    expected = 1000.0 * 900.0 / (600 * 1000.0 / 270.0 + 300 * 1000.0 / 261.0)
    assert 266.9 < expected < 267.0
    assert block.gap_pace_s_per_km == pytest.approx(expected, rel=1e-9)
    assert block.gap_coverage == 1.0 and block.gps_degraded_fraction == 0.0


def test_a_garmin_coach_threshold_counts_three_blocks():
    """AC5's Garmin Coach Threshold: ``interval`` steps with a heart-rate target, 3 x 8 min with
    2 min recoveries: 1440 s in 3 blocks."""
    steps = [_step(0, "warmup"), _step(1, "interval", **HR), _step(2, "recovery", **OPEN), _step(3, "cooldown")]
    plan = [("warmup", 600, 0)]
    for _ in range(3):
        plan += [("interval", 480, 1), ("recovery", 120, 2)]
    plan.append(("cooldown", 600, 3))
    laps = _plan_laps(plan)
    work = {lap["lap_index"]: 255.0 for lap in laps if lap["intensity"] == "interval"}
    block = TP.active_block(laps, steps, _stream(laps, work))
    print("coach threshold:", block)
    assert block.unavailable is None
    assert (block.active_time_s, block.active_blocks) == (1440.0, 3)
    assert block.windows == ((600.0, 1080.0), (1200.0, 1680.0), (1800.0, 2280.0))
    assert block.gap_pace_s_per_km == pytest.approx(255.0, rel=1e-9)


def test_rep_paces_match_a_brute_force_oracle():
    """Per-rep GAP paces from the one bisect walk against a per-rep scan of every segment with
    ``start <= t_start < end``: graded, mixed-pace segments in shuffled order, over a workout
    whose reps are not laid out in time order (lap 0 is the latest), with a segment on each
    window's start and one on each end (outside)."""
    import random

    steps = [_step(0, "active", **WORK), _step(1, "active", **WORK), _step(2, "recovery"), _step(3, "active", **WORK)]
    laps = [
        _lap(0, 2000.0, 400, "active", 0),
        _lap(1, 0.0, 300, "active", 1),
        _lap(2, 300.0, 100, "recovery", 2),
        _lap(3, 400.0, 350, "active", 3),
        _lap(4, 750.0, 350, "active", 3),
        _lap(5, 1100.0, 300, "active", 1),
    ]
    decided = TP.active_laps(laps, steps)
    assert decided.unavailable is None
    assert [(rep.step_index, rep.length_s, len(rep.windows)) for rep in decided.reps] == [
        (0, 400.0, 1),
        (1, 300.0, 1),
        (3, 700.0, 2),
        (1, 300.0, 1),
    ]
    rng = random.Random(7)
    rows = []
    for t in range(0, 2500, 5):
        rows.append(_segment(float(t), 5.0, rng.uniform(10.0, 25.0), rng.uniform(0.9, 1.3)))
    for rep in decided.reps:
        for start, end in rep.windows:
            rows.append(_segment(start, 5.0, 17.0, 1.05))
            rows.append(_segment(end, 5.0, 17.0, 1.05))
    rng.shuffle(rows)

    def brute(rep) -> float | None:
        inside = [row for row in rows if any(start <= row["t_start"] < end for start, end in rep.windows)]
        seconds = sum(row["dt"] for row in inside)
        gap_m = sum(row["contributed_m"] * row["g"] for row in inside)
        return 1000.0 * seconds / gap_m if gap_m > 0 else None

    expected = [brute(rep) for rep in decided.reps]
    got = TP.rep_paces(decided.reps, rows)
    print("rep paces:", got, "oracle:", expected)
    assert len(got) == 4 and all(p is not None for p in expected)
    for pace, oracle in zip(got, expected, strict=True):
        assert pace == pytest.approx(oracle, rel=1e-12)
    block = TP.active_block(laps, steps, rows)
    fastest = min(expected)
    counted = [rep for rep, pace in zip(decided.reps, expected, strict=True) if pace <= 1.10 * fastest]
    assert block.active_time_s == sum(rep.length_s for rep in counted)
    assert block.windows == tuple(window for rep in counted for window in rep.windows)


def test_workout_names_match_stripped_and_casefolded():
    """A configured name admits when any step row's ``workout_name`` equals it after
    ``str.strip().casefold()`` on both sides; rows without a name are skipped."""
    admits = TP.workout_name_admits
    assert admits(["Progression Run"], [None, " progression run "]) is True
    assert admits([" Goal Pace Run "], ["GOAL PACE RUN"]) is True
    assert admits(["Straße"], ["STRASSE"]) is True  # casefold, not lower
    assert admits(["Threshold"], ["Threshold Run"]) is False
    assert admits(["Threshold"], [None, 3, True]) is False
    assert admits([], ["Threshold"]) is False
    assert admits(["Threshold"], []) is False
    assert admits(["Tempo", "Threshold"], ["Easy Run", "threshold"]) is True
