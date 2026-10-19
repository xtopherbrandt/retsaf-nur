"""The threshold-pace qualifier and estimator (F024 reference sections 1 to 4), driven pure.

``metrics.threshold_pace.estimate`` takes plain candidate rows and an aware ``as_of`` and needs no
database, so every test here builds dicts. The worked-example tests compare the estimator with two
things it did not write:

- **The oracle** (``tests/support/threshold_oracle.py``), written from the reference before the
  module, restating the filter in information form and importing nothing from ``runcoach_api``
  (``test_the_oracle_imports_nothing_from_the_estimator`` parses the oracle's imports to pin that).
- **The reference's section 4 table**, typed into the oracle module as ``WORKED_EXAMPLE``, to
  0.01 s/km for paces and interval ends and 0.001 percentage points for the error.

The label and short-race boundaries are asserted exactly (no tolerance), as reference section 1
rules: labels compare P with R and 4 R directly.
"""

from __future__ import annotations

import ast
import importlib.util
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ORACLE_PATH = Path(__file__).parent / "support" / "threshold_oracle.py"

# ``tests/`` is not a package (importlib mode), so the oracle is loaded from its path, as trimp_oracle is.
_SPEC = importlib.util.spec_from_file_location("threshold_oracle", ORACLE_PATH)
threshold_oracle = importlib.util.module_from_spec(_SPEC)
sys.modules["threshold_oracle"] = threshold_oracle
_SPEC.loader.exec_module(threshold_oracle)  # type: ignore[union-attr]

from runcoach_api.metrics import threshold_pace
from runcoach_api.metrics.threshold_pace import estimate

PACE_TOLERANCE = 0.01  # s/km
ERROR_TOLERANCE = 0.001  # percentage points

BASE = datetime(2026, 6, 1, 7, 0, tzinfo=UTC)


def _race(session_id: str, day: float, pace: object, duration: object = 2400, **over: object) -> dict:
    row = {
        "session_id": session_id,
        "start_time": (BASE + timedelta(days=day)).isoformat(),
        "sport": "running",
        "activity_tag": "race",
        "timer_time_s": duration,
        "duration_s": duration,
        "quality_flags": [],
        "gap_avg_pace_s_per_km": pace,
        "gap_coverage": 1.0,
        "gps_degraded_fraction": 0.0,
    }
    row.update(over)
    # The row's kind follows its tag, so an untagged row stays an ignored one (its kind is None).
    row.setdefault("kind", row["activity_tag"])
    return row


def _workout(session_id: str, day: float, pace: object, active_s: object = 1800.0, **over: object) -> dict:
    """A threshold workout's row as the reader hands it over (F026 reference section 3): its laps
    read, rows 1a and 2 passed (``lap_reason`` null), ``active_time_s`` the active laps' summed
    ``timer_s`` and the active GAP pace over the active windows."""
    row = {
        "session_id": session_id,
        "start_time": (BASE + timedelta(days=day)).isoformat(),
        "sport": "running",
        "activity_tag": "threshold_workout",
        "laps_read_at": "2026-10-16T00:00:00+00:00",
        "lap_reason": None,
        "active_time_s": active_s,
        "active_blocks": 1,
        "quality_flags": [],
        "gap_avg_pace_s_per_km": pace,
        "gap_coverage": 1.0,
        "gps_degraded_fraction": 0.0,
    }
    row.update(over)
    row.setdefault("kind", row["activity_tag"])
    return row


def _at(day: float) -> datetime:
    return BASE + timedelta(days=day)


def _rows_for(efforts: list[tuple[float, float]]) -> list[dict]:
    return [_race(f"s{i}", day, pace) for i, (day, pace) in enumerate(efforts)]


def _assert_matches_table(name: str) -> None:
    (efforts, as_of_day), (pace, error_pct, fast, slow, label) = threshold_oracle.WORKED_EXAMPLE[name]
    result = estimate(_rows_for(efforts), _at(as_of_day))
    oracle = threshold_oracle.replay(efforts, as_of_day)
    for got, want_oracle, want_table in (
        (result.value, oracle.pace_s_per_km, pace),
        (result.interval_low, oracle.interval_fast, fast),
        (result.interval_high, oracle.interval_slow, slow),
    ):
        assert abs(got - want_oracle) <= PACE_TOLERANCE, (name, got, want_oracle)
        assert abs(got - want_table) <= PACE_TOLERANCE + 0.005, (name, got, want_table)
    assert abs(result.error * 100 - oracle.error * 100) <= ERROR_TOLERANCE, (name, result.error, oracle.error)
    assert abs(result.error * 100 - error_pct) <= ERROR_TOLERANCE + 0.0005, (name, result.error, error_pct)
    assert result.confidence == oracle.label == label, (name, result.confidence, oracle.label, label)
    assert result.interval_low < result.value < result.interval_high
    assert result.unavailable is None
    gains = tuple(e.gain for e in result.efforts)
    assert len(gains) == len(oracle.gains)
    for got, want in zip(gains, oracle.gains, strict=True):
        assert math.isclose(got, want, rel_tol=1e-12), (name, gains, oracle.gains)


def test_the_oracle_imports_nothing_from_the_estimator() -> None:
    # Read from the oracle's source, not sys.modules: conftest has imported runcoach_api.db (and so
    # metrics.threshold_pace) before this file loads, so a module-set diff can never see that import.
    tree = ast.parse(ORACLE_PATH.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    forbidden = {n for n in names if n.split(".")[0] == "runcoach_api" or n.startswith(".")}
    assert not forbidden, forbidden
    assert not any(hasattr(threshold_oracle, attr) for attr in ("threshold_pace", "estimate"))


# --- the worked example (reference section 4) ----------------------------------------------


def test_right_after_a_reads_270_high_against_the_oracle() -> None:
    _assert_matches_table("right after A")
    result = estimate([_race("a", 0, 270.0)], _at(0))
    assert result.value == pytest.approx(270.0, abs=1e-9)
    assert result.variance == threshold_pace.R
    assert result.efforts[0].gain == 1.0
    assert result.confidence == "high"


def test_a_plus_42_days_alone_reads_medium() -> None:
    _assert_matches_table("A + 42 days, before B")


def test_right_after_b_gain_is_0_8() -> None:
    _assert_matches_table("right after B")
    result = estimate(_rows_for([(0, 270.0), (42, 260.0)]), _at(42))
    assert [e.session_id for e in result.efforts] == ["s0", "s1"]
    assert result.efforts[1].gain == pytest.approx(0.8, abs=1e-12)


def test_b_plus_30_days_medium() -> None:
    _assert_matches_table("B + 30 days")


def test_b_plus_90_days_low() -> None:
    _assert_matches_table("B + 90 days")


def test_same_instant_pair_is_the_geometric_mean() -> None:
    _assert_matches_table("A and B on the same instant")
    result = estimate([_race("a", 0, 270.0), _race("b", 0, 260.0)], _at(0))
    assert result.value == pytest.approx(math.sqrt(270.0 * 260.0), abs=1e-9)
    assert result.efforts[1].gain == pytest.approx(0.5, abs=1e-12)
    assert result.variance == pytest.approx(threshold_pace.R / 2, rel=1e-12)


def test_reverse_order_tags_give_the_same_result() -> None:
    forward = [_race("a", 0, 270.0), _race("b", 42, 260.0)]
    backward = list(reversed(forward))
    for day in (42, 72, 132):
        assert estimate(forward, _at(day)) == estimate(backward, _at(day))
    # Two at one instant replay in session-id order whatever order they arrive in.
    pair = [_race("b", 0, 260.0), _race("a", 0, 270.0)]
    assert estimate(pair, _at(0)) == estimate(list(reversed(pair)), _at(0))
    assert [e.session_id for e in estimate(pair, _at(0)).efforts] == ["a", "b"]


def test_third_race_at_b_has_gain_0_444() -> None:
    two = estimate(_rows_for([(0, 270.0), (42, 260.0)]), _at(42))
    three = estimate([*_rows_for([(0, 270.0), (42, 260.0)]), _race("s2", 42, 260.0)], _at(42))
    assert three.efforts[2].gain == pytest.approx(0.8 / 1.8, abs=1e-12)
    assert round(three.efforts[2].gain, 3) == 0.444
    moved_by_b = 270.0 - two.value
    moved_by_third = two.value - three.value
    assert 0 < moved_by_third < moved_by_b
    oracle = threshold_oracle.replay([(0, 270.0), (42, 260.0), (42, 260.0)], 42)
    assert abs(three.value - oracle.pace_s_per_km) <= PACE_TOLERANCE


# --- labels and the short-races flag (F024 AC3) ---------------------------------------------


def test_one_race_label_boundaries_at_42_days() -> None:
    rows = [_race("a", 0, 270.0)]
    assert estimate(rows, _at(0)).confidence == "high"
    assert estimate(rows, _at(41.99)).confidence == "medium"
    at_42 = estimate(rows, _at(42))
    assert at_42.variance == 4 * threshold_pace.R  # exact in doubles, reference section 1
    assert at_42.confidence == "medium"
    assert estimate(rows, _at(42.01)).confidence == "low"


def test_after_b_high_until_2_8_days() -> None:
    rows = [_race("a", 0, 270.0), _race("b", 42, 260.0)]
    assert estimate(rows, _at(42 + 2.79)).confidence == "high"
    assert estimate(rows, _at(42 + 2.81)).confidence == "medium"
    assert estimate(rows, _at(42 + 44.79)).confidence == "medium"
    assert estimate(rows, _at(42 + 44.81)).confidence == "low"


def test_short_races_only_flag() -> None:
    assert estimate([_race("a", 0, 250.0, duration=1200)], _at(1)).short_races_only is True
    assert estimate([_race("a", 0, 250.0, duration=1799)], _at(1)).short_races_only is True
    both_short = [_race("a", 0, 250.0, duration=1200), _race("b", 1, 250.0, duration=1799)]
    assert estimate(both_short, _at(2)).short_races_only is True
    one_long = [*both_short, _race("c", 2, 260.0, duration=1800)]
    assert estimate(one_long, _at(3)).short_races_only is False
    assert estimate([_race("c", 0, 260.0, duration=1800)], _at(1)).short_races_only is False
    unavailable = estimate([], _at(0))
    assert unavailable.short_races_only is False
    # A long race excluded (outside the band) does not clear the flag: only efforts used count.
    with_marathon = [_race("a", 0, 250.0, duration=1500), _race("m", 1, 300.0, duration=12000)]
    assert estimate(with_marathon, _at(2)).short_races_only is True


# --- unavailable (F024 AC4) -----------------------------------------------------------------


def test_no_effort_is_unavailable() -> None:
    def assert_unavailable(result, excluded: list[tuple[str, str]]) -> None:
        assert result.unavailable == "no_qualifying_effort"
        assert result.efforts == ()
        for attr in ("value", "interval_low", "interval_high", "error", "confidence", "x", "variance"):
            assert getattr(result, attr) is None, attr
        assert [(e.session_id, e.reason) for e in result.excluded] == excluded
        assert result.parameters == threshold_pace.PARAMETERS

    assert_unavailable(estimate([], _at(0)), [])
    marathon = _race("m", 0, 300.0, duration=3 * 3600)
    assert_unavailable(estimate([marathon], _at(1)), [("m", "outside_threshold_band")])
    untagged = [_race(f"u{i}", i, 260.0, activity_tag=None) for i in range(3)]
    assert_unavailable(estimate(untagged, _at(5)), [])
    flagged = _race("f", 0, 260.0, quality_flags=["smart_recording"])
    assert_unavailable(estimate([flagged], _at(1)), [("f", "smart_recording")])
    # An as_of before every effort sees none of them, qualifying or excluded.
    assert_unavailable(estimate([_race("a", 5, 270.0), marathon], _at(-1)), [])


def test_degenerate_gap_pace_is_excluded_never_raises() -> None:
    degenerate = [None, 0, 0.0, -0.0, -260.0, math.nan, math.inf, -math.inf, True, "260", 5e-324]
    rows = [_race(f"d{i:02d}", 0, pace) for i, pace in enumerate(degenerate)]
    result = estimate(rows, _at(1))
    assert result.unavailable == "no_qualifying_effort"
    assert [(e.session_id, e.reason) for e in result.excluded] == [
        (f"d{i:02d}", "gap_pace_unavailable") for i in range(len(degenerate))
    ]
    # An excluded effort changes nothing: the estimate with them equals the estimate without them.
    good = _race("good", 0.5, 265.0)
    assert estimate([good, *rows], _at(1)).value == estimate([good], _at(1)).value
    assert estimate([good, *rows], _at(1)).efforts == estimate([good], _at(1)).efforts


# --- qualifying order (reference section 2) -------------------------------------------------


def test_qualifying_order_and_reasons() -> None:
    rows = [
        _race("ride", 0, 260.0, sport="cycling"),
        _race("other", 0, 260.0, sport="other"),
        _race("short", 0, 260.0, duration=1199),
        _race("long", 0, 260.0, duration=3601),
        _race("both", 0, None, duration=10, quality_flags=["smart_recording", "distance_regressed"]),
        _race("flags", 0, None, quality_flags=["smart_recording", "distance_regressed"]),
        _race("smart", 0, None, quality_flags=["smart_recording"]),
        _race("degraded", 0, 260.0, gps_degraded_fraction=0.4),
        _race("flat", 0, 255.0, gap_coverage=0.0),
        _race("lo", 0, 262.0, duration=1200),
        _race("hi", 0, 258.0, duration=3600),
    ]
    result = estimate(rows, _at(0))
    assert [(e.session_id, e.reason) for e in result.excluded] == [
        ("both", "outside_threshold_band"),
        ("flags", "distance_regressed"),
        ("long", "outside_threshold_band"),
        ("short", "outside_threshold_band"),
        ("smart", "smart_recording"),
    ]
    used = {e.session_id: e for e in result.efforts}
    assert set(used) == {"degraded", "flat", "lo", "hi"}
    assert used["degraded"].gps_degraded_fraction == 0.4
    assert used["flat"].gap_coverage == 0.0
    assert used["lo"].duration_s == 1200 and used["hi"].duration_s == 3600


def test_duration_falls_back_to_summary_and_refuses_non_numbers() -> None:
    fallback = _race("fb", 0, 260.0, timer_time_s=None, duration_s=2000)
    result = estimate([fallback], _at(0))
    assert result.efforts[0].duration_s == 2000
    for i, bad in enumerate([True, "2000", math.nan, math.inf, None]):
        row = _race(f"b{i}", 0, 260.0, timer_time_s=None, duration_s=bad)
        assert [(e.session_id, e.reason) for e in estimate([row], _at(0)).excluded] == [
            (f"b{i}", "outside_threshold_band")
        ]
    for i, bad in enumerate([False, "2000", math.nan, -math.inf]):
        row = _race(f"t{i}", 0, 260.0, timer_time_s=bad, duration_s=2000)
        assert estimate([row], _at(0)).excluded[0].reason == "outside_threshold_band"
    # A timer in band wins over an elapsed summary out of band (a paused race's moving time).
    paused = _race("p", 0, 260.0, timer_time_s=3000, duration_s=4000)
    assert estimate([paused], _at(0)).efforts[0].duration_s == 3000


def test_an_int_too_large_for_a_float_is_refused_never_raises() -> None:
    """``json.loads`` returns an int beyond float range as an int; ``float()`` of it raises
    ``OverflowError``, which the screen reads as not a number."""
    huge = 10**400
    summary = _race("s", 0, 260.0, timer_time_s=None, duration_s=huge)
    timer = _race("t", 0, 260.0, timer_time_s=huge, duration_s=2000)
    pace = _race("p", 0, huge)
    result = estimate([summary, timer, pace], _at(0))
    assert [(e.session_id, e.reason) for e in result.excluded] == [
        ("p", "gap_pace_unavailable"),
        ("s", "outside_threshold_band"),
        ("t", "outside_threshold_band"),
    ]


def test_parameters_and_constants() -> None:
    assert threshold_pace.PARAMETERS == {
        "typical_error": 0.025,
        "doubling_days": 42,
        "coverage": 0.80,
        "high_max_error": 0.025,
        "medium_max_error": 0.05,
        "short_race_s": 1800,
        "workout_min_active_s": 600,
    }
    assert threshold_pace.R == 0.025**2
    assert threshold_pace.Q_PER_DAY == 3 * threshold_pace.R / 42
    assert threshold_pace.Z == 1.2815515655446004
    assert estimate([_race("a", 0, 270.0)], _at(0)).parameters == threshold_pace.PARAMETERS


def test_start_time_at_as_of_counts_and_after_is_ignored() -> None:
    at = estimate([_race("a", 0, 270.0)], _at(0))
    assert [e.session_id for e in at.efforts] == ["a"]
    later = estimate([_race("a", 0, 270.0), _race("b", 0.5, 200.0)], _at(0.25))
    assert [e.session_id for e in later.efforts] == ["a"]
    assert later.value == pytest.approx(270.0, abs=1e-9)
    assert later.as_of == _at(0.25)


def test_naive_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        estimate([], datetime(2026, 6, 1, 7, 0))  # noqa: DTZ001 -- the naive value is the input under test
    with pytest.raises(ValueError, match="naive"):
        estimate([_race("a", 0, 270.0, start_time="2026-06-01T07:00:00")], _at(1))


# --- threshold workouts in the filter (F026 reference section 4, AC4) ------------------------


def test_a_workout_alone_sets_x_to_y_and_p_to_r() -> None:
    result = estimate([_workout("w", 0, 265.0, active_s=1200.0)], _at(0))
    print("workout alone:", result)
    assert result.unavailable is None
    [effort] = result.efforts
    assert (effort.session_id, effort.kind, effort.active_blocks) == ("w", "threshold_workout", 1)
    assert effort.duration_s == 1200.0
    assert effort.gain == 1.0
    assert result.x == math.log(1000 / 265.0)
    assert result.variance == threshold_pace.R
    assert result.value == pytest.approx(265.0, abs=1e-9)
    oracle = threshold_oracle.replay([(0.0, 265.0)], 0.0)
    assert abs(result.value - oracle.pace_s_per_km) <= PACE_TOLERANCE
    assert abs(result.interval_low - oracle.interval_fast) <= PACE_TOLERANCE
    assert abs(result.interval_high - oracle.interval_slow) <= PACE_TOLERANCE
    assert result.confidence == oracle.label == "high"
    # A workout alone at 42 days reads as a race alone does: P = 4 R exactly, medium.
    later = estimate([_workout("w", 0, 265.0)], _at(42))
    assert later.variance == 4 * threshold_pace.R and later.confidence == "medium"


def test_a_race_then_a_workout_replay_with_the_same_r() -> None:
    """F024's section 3 filter with one R for both kinds: race A then a workout 42 days later is the
    worked example's "right after B" and "B + 30 days" rows, against the oracle and the table."""
    rows = [_race("a", 0, 270.0), _workout("w", 42, 260.0)]
    for name, day in (("right after B", 42.0), ("B + 30 days", 72.0), ("B + 90 days", 132.0)):
        (_efforts, _as_of), (pace, error_pct, fast, slow, label) = threshold_oracle.WORKED_EXAMPLE[name]
        result = estimate(rows, _at(day))
        oracle = threshold_oracle.replay([(0.0, 270.0), (42.0, 260.0)], day)
        print(f"  {name}: {result.value} {result.error} {result.confidence}")
        assert abs(result.value - oracle.pace_s_per_km) <= PACE_TOLERANCE
        assert abs(result.value - pace) <= PACE_TOLERANCE + 0.005
        assert abs(result.interval_low - fast) <= PACE_TOLERANCE + 0.005
        assert abs(result.interval_high - slow) <= PACE_TOLERANCE + 0.005
        assert abs(result.error * 100 - error_pct) <= ERROR_TOLERANCE + 0.0005
        assert result.confidence == oracle.label == label
        assert [(e.session_id, e.kind) for e in result.efforts] == [("a", "race"), ("w", "threshold_workout")]
        assert result.efforts[1].gain == pytest.approx(0.8, abs=1e-12)
        # The same values as two races: the workout's R is the race's.
        assert estimate([_race("a", 0, 270.0), _race("b", 42, 260.0)], _at(day)).value == result.value
    assert [e.active_blocks for e in estimate(rows, _at(42)).efforts] == [None, 1]


def test_short_races_only_reads_races_only() -> None:
    """Reference section 4: true when at least one race is used and every race used lasted under
    1800 s; workouts never set it. Under the retired "every effort used" rule the first case reads
    false and the workouts-only case true."""
    one_short_race = [_race("r", 0, 250.0, duration=1500), _workout("w", 1, 260.0, active_s=2000.0)]
    both_used = estimate(one_short_race, _at(2))
    assert [(e.kind, e.duration_s) for e in both_used.efforts] == [("race", 1500), ("threshold_workout", 2000.0)]
    assert both_used.short_races_only is True
    many_workouts = [*one_short_race, _workout("v", 1.5, 262.0, active_s=700.0)]
    assert estimate(many_workouts, _at(2)).short_races_only is True
    workouts_only = [_workout("w", 0, 260.0, active_s=700.0), _workout("v", 1, 262.0, active_s=900.0)]
    assert estimate(workouts_only, _at(2)).short_races_only is False
    race_of_1800 = [_race("r", 0, 260.0, duration=1800), _workout("w", 1, 260.0, active_s=700.0)]
    assert estimate(race_of_1800, _at(2)).short_races_only is False
    # An excluded short race does not set it: only races used count.
    excluded_race = [_race("r", 0, 250.0, duration=1500, quality_flags=["smart_recording"]), *workouts_only]
    assert estimate(excluded_race, _at(2)).short_races_only is False


def test_parameters_carry_workout_min_active_s() -> None:
    assert threshold_pace.PARAMETERS["workout_min_active_s"] == 600
    assert threshold_pace.WORKOUT_MIN_ACTIVE_S == 600
    result = estimate([_workout("w", 0, 260.0)], _at(0))
    assert result.parameters["workout_min_active_s"] == 600
    assert dict(result.parameters) == dict(threshold_pace.PARAMETERS)
