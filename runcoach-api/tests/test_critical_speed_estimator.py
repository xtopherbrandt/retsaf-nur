"""The critical-speed estimator (F033 reference sections 1, 2, 4 and 5): the screen, the one run per
length, the shape check, the spread, the far-off test, the line and the label, driven with
hand-built rows and stretch maps and no database.

Expected values come from two places that share no code with ``metrics.critical_speed``:

- **The oracle** (``tests/support/cs_oracle.py``): ordinary least squares by the 2x2 normal
  equations in raw sums, the far-off excess and the label rule, with Student's t from
  ``tests/support/riegel_oracle.py``'s incomplete-beta quantile.
  ``test_the_oracle_imports_nothing_from_the_estimator`` parses its imports to pin that.
- **The reference's section 5 figures**, typed into the oracle module by hand at full precision.

The edge rows (reference section 5's synthetic list) were written from the reference before the
module, as a table of inputs including the uninteresting ones; the edge distances are chosen so
the figure on each side of a bar is exact in floating point.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ORACLE_PATH = Path(__file__).parent / "support" / "cs_oracle.py"
ESTIMATOR_PATH = Path(__file__).parents[1] / "src" / "runcoach_api" / "metrics" / "critical_speed.py"

# ``tests/`` is not a package (importlib mode), so the oracle is loaded from its path.
_SPEC = importlib.util.spec_from_file_location("cs_oracle", ORACLE_PATH)
cs_oracle = importlib.util.module_from_spec(_SPEC)
sys.modules["cs_oracle"] = cs_oracle
_SPEC.loader.exec_module(cs_oracle)  # type: ignore[union-attr]

from runcoach_api.metrics import critical_speed as cs
from runcoach_api.metrics.gap_stretches import BestStretch

ZONE = ZoneInfo("America/Vancouver")
INSTANT = datetime(2026, 10, 31, 23, 59, 59, 999999, tzinfo=ZONE)
INSTANT_UTC = INSTANT.astimezone(UTC)


# ---------------------------------------------------------------------------- fit helpers


def _bs(length: float, distance: float) -> BestStretch:
    return BestStretch(length, distance, float(length), 0.0)


def _run(
    sid: str,
    bests: dict[float, float],
    *,
    days_ago: float = 10.0,
    tag: str | None = None,
    name: str | None = "Run",
    duration: float | None = 3000.0,
    admitted: bool = False,
) -> cs.RunBests:
    return cs.RunBests(
        session_id=sid,
        start_time=INSTANT_UTC - timedelta(days=days_ago),
        activity_tag=tag,
        name=name,
        duration_s=duration,
        admitted_as_workout=admitted,
        bests={length: _bs(length, d) for length, d in bests.items()},
    )


def _points(points: list[tuple[float, float]], meta: dict | None = None) -> list[cs.RunBests]:
    """One run per point, each holding only that length; ``meta`` maps a length to run keywords."""
    meta = meta or {}
    return [
        _run(f"run-{length}", {length: d}, days_ago=1.0 + k, **meta.get(length, {}))
        for k, (length, d) in enumerate(points)
    ]


def _fit(runs: list[cs.RunBests]) -> cs.CriticalSpeedEstimate:
    return cs.fit(runs, INSTANT)


def _by_length(result: cs.CriticalSpeedEstimate) -> dict[float, cs.Stretch]:
    return {s.length_s: s for s in result.stretches}


# Four kept stretches, speeds 4.5, 4.3, 4.2, 4.1 m/s: spread passes, far-off excess about 0.3 s/km.
BASE = [(120, 4.5 * 120), (300, 4.3 * 300), (480, 4.2 * 480), (720, 4.1 * 720)]


def _available(result: cs.CriticalSpeedEstimate) -> None:
    assert result.unavailable is None, result.unavailable
    assert result.cs_m_per_s is not None and result.cs_s_per_km is not None


# ---------------------------------------------------------------------------- AC3


def _worked_runs(example: dict) -> list[cs.RunBests]:
    runs = []
    for length, d, day, tag, name, duration, admitted, _kept in example["stretches"]:
        start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=ZONE).replace(hour=6)
        runs.append(
            cs.RunBests(
                session_id=f"{day}-{length}",
                start_time=start,
                activity_tag=tag,
                name=name,
                duration_s=duration,
                admitted_as_workout=admitted,
                bests={length: BestStretch(length, d, float(length), 0.0)},
            )
        )
    return runs


@pytest.mark.parametrize("day", list(cs_oracle.WORKED_EXAMPLES))
def test_worked_examples_match_the_reference(day: str) -> None:
    example = cs_oracle.WORKED_EXAMPLES[day]
    instant = datetime.combine(date.fromisoformat(day), datetime.max.time(), tzinfo=ZONE)
    result = cs.fit(_worked_runs(example), instant)
    _available(result)

    kept_rows = [row for row in example["stretches"] if row[7]]
    points = [(float(row[0]), row[1]) for row in kept_rows]
    oracle = cs_oracle.line(points)
    excess = cs_oracle.far_off_excess(points[-2], points[-1])
    label = cs_oracle.label([(row[3], row[6], row[5]) for row in kept_rows])

    served = {
        "cs": result.cs_m_per_s,
        "pace": result.cs_s_per_km,
        "pace_low": result.interval_low_s_per_km,
        "pace_high": result.interval_high_s_per_km,
        "d_prime": result.d_prime_m,
        "d_prime_low": result.d_prime_low_m,
        "d_prime_high": result.d_prime_high_m,
        "se": result.error_m_per_s,
        "se_d": result.d_prime_error_m,
        "t": result.t_multiplier,
        "excess": result.far_off_excess_s_per_km,
    }
    expected = {
        "cs": oracle.cs,
        "pace": oracle.pace,
        "pace_low": oracle.pace_low,
        "pace_high": oracle.pace_high,
        "d_prime": oracle.d_prime,
        "d_prime_low": oracle.d_prime_low,
        "d_prime_high": oracle.d_prime_high,
        "se": oracle.se,
        "se_d": oracle.se_d,
        "t": oracle.t,
        "excess": excess,
    }
    for key, value in served.items():
        assert value is not None, key
        assert abs(value - expected[key]) <= 1e-9, (key, value, expected[key])
        assert abs(value - example[key]) <= 1e-6, (key, value, example[key])
    assert result.interval_low_s_per_km < result.cs_s_per_km < result.interval_high_s_per_km
    assert result.label == label == example["label"]
    assert result.d_prime_not_positive is False

    stretches = _by_length(result)
    assert len(stretches) == 6
    for length, d, _day, _tag, _name, _duration, _admitted, kept in example["stretches"]:
        stretch = stretches[length]
        assert stretch.distance_m == d
        assert stretch.pace_s_per_km == pytest.approx(1000 * length / d, abs=1e-12)
        assert (stretch.drop_reason is None) is kept
    if day == "2025-01-31":
        assert stretches[720].drop_reason == cs.BEATEN_BY_LONGER
        assert stretches[720].beaten_by_s == 1200
    assert [s.length_s for s in result.stretches] == [120, 180, 300, 480, 720, 1200]


def test_a_run_best_at_two_lengths_is_used_at_the_longer() -> None:
    # A is best at 720 and at 480; B is second at 480. A is used at 720, B takes 480.
    runs = [
        _run("a", {120: 520.0, 300: 1290.0, 480: 2030.0, 720: 2950.0}, days_ago=5),
        _run("b", {120: 515.0, 300: 1280.0, 480: 2010.0}, days_ago=6),
        _run("c", {120: 540.0, 300: 1300.0}, days_ago=7),
        _run("d", {120: 545.0}, days_ago=8),
    ]
    stretches = _by_length(_fit(runs))
    assert stretches[720].session_id == "a"
    assert stretches[480].session_id == "b"
    assert stretches[300].session_id == "c"
    assert stretches[120].session_id == "d"


def test_ties_go_to_the_later_start_then_the_larger_session_id() -> None:
    later = _fit([_run("x", {720: 2900.0}, days_ago=5), _run("y", {720: 2900.0}, days_ago=3)])
    assert _by_length(later)[720].session_id == "y"
    same = _fit([_run("x2", {720: 2900.0}, days_ago=4), _run("x1", {720: 2900.0}, days_ago=4)])
    assert _by_length(same)[720].session_id == "x2"


# ---------------------------------------------------------------------------- AC1 (pure half)


def _segments(seconds: int, speed: float, *, start: float = 0.0) -> list[dict]:
    return [
        {"t_start": start + k, "dt": 1.0, "v_actual": speed, "g": 1.0, "moving_gap": False}
        for k in range(seconds)
    ]


def _row(
    sid: str,
    days_ago: float = 10.0,
    *,
    sport: object = "running",
    tag: object = None,
    name: object = "Run",
    quality_flags: object = "[]",
    unreadable: object = None,
    flags: object = (),
    segments: object = None,
    timer: object = 3000.0,
    summary: object = '{"duration_s": 3000.0}',
    admitted: object = False,
    start: object = None,
) -> dict:
    return {
        "session_id": sid,
        "start_time": start if start is not None else (INSTANT_UTC - timedelta(days=days_ago)).isoformat(),
        "sport": sport,
        "activity_tag": tag,
        "name": name,
        "timer_time_s": timer,
        "duration_s": None,
        "summary": summary,
        "quality_flags": quality_flags,
        "admitted_as_workout": admitted,
        "unreadable": unreadable,
        "flags": list(flags) if isinstance(flags, tuple) else flags,
        "segments": segments if segments is not None else _segments(130, 4.0),
    }


def test_candidates_screen_in_reference_order() -> None:
    rows = [
        _row("01-cycling", 1, sport="cycling", tag="resting_hrv_check", quality_flags="{"),
        _row("02-declared", 2, tag="resting_hrv_check", quality_flags='["distance_regressed"]'),
        _row("03-declared-snapshot", 3, tag="health_snapshot"),
        _row("04-qf-not-json", 4, quality_flags="not json", flags=["grade_clamped"]),
        _row("05-qf-not-list", 5, quality_flags='{"a": 1}'),
        _row("06-qf-marked", 6, quality_flags="[]", unreadable="quality_flags_unreadable"),
        _row("07-stored-dr", 7, quality_flags='["smart_recording", "distance_regressed"]'),
        _row("08-stored-smart", 8, quality_flags='["smart_recording"]', flags=["distance_regressed"]),
        _row("09-features", 9, unreadable="features_unavailable", flags=None, segments=None),
        _row("10-features-not-list", 10, flags="grade_clamped"),
        _row("11-f013-dr", 11, flags=["grade_clamped", "smart_recording", "distance_regressed"]),
        _row("12-f013-smart", 12, flags=["grade_clamped", "smart_recording"]),
        _row("13-f013-clamped", 13, flags=["grade_clamped", "gps_degraded"]),
        _row("14-used", 14, flags=["gps_degraded"]),
    ]
    rows[8]["segments"] = None
    result = cs.estimate(rows, INSTANT)
    got = {e.session_id: (e.reason, e.source) for e in result.excluded}
    assert got == {
        "01-cycling": ("not_running", None),
        "02-declared": ("declared_capture", None),
        "03-declared-snapshot": ("declared_capture", None),
        "04-qf-not-json": ("quality_flags_unreadable", None),
        "05-qf-not-list": ("quality_flags_unreadable", None),
        "06-qf-marked": ("quality_flags_unreadable", None),
        "07-stored-dr": ("distance_regressed", "stored"),
        "08-stored-smart": ("smart_recording", "stored"),
        "09-features": ("features_unavailable", None),
        "10-features-not-list": ("features_unavailable", None),
        "11-f013-dr": ("distance_regressed", "f013"),
        "12-f013-smart": ("smart_recording", "f013"),
        "13-f013-clamped": ("grade_clamped", "f013"),
    }
    # Listed in start order, oldest first.
    assert [e.session_id for e in result.excluded] == sorted(got, reverse=True)
    assert [s.session_id for s in result.stretches] == ["14-used"]
    # The stored steps decide the records read: false for each row a stored value excludes.
    needs = {row["session_id"]: cs.needs_records(row, INSTANT) for row in rows}
    assert needs == {
        "01-cycling": False,
        "02-declared": False,
        "03-declared-snapshot": False,
        "04-qf-not-json": False,
        "05-qf-not-list": False,
        "06-qf-marked": False,
        "07-stored-dr": False,
        "08-stored-smart": False,
        "09-features": True,
        "10-features-not-list": True,
        "11-f013-dr": True,
        "12-f013-smart": True,
        "13-f013-clamped": True,
        "14-used": True,
    }


class _NoRecordsRow(dict):
    """A row whose record-derived keys raise when read."""

    def __getitem__(self, key):  # type: ignore[no-untyped-def]
        if key in ("flags", "segments"):
            raise AssertionError(f"{key} read before the records")
        return super().__getitem__(key)

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        if key in ("flags", "segments"):
            raise AssertionError(f"{key} read before the records")
        return super().get(key, default)


def test_needs_records_reads_stored_values_only() -> None:
    clean = _NoRecordsRow(_row("clean", 5))
    flagged = _NoRecordsRow(_row("flagged", 5, quality_flags='["smart_recording"]'))
    cycling = _NoRecordsRow(_row("cycling", 5, sport="cycling"))
    old = _NoRecordsRow(_row("old", 400))
    assert cs.needs_records(clean, INSTANT) is True
    assert cs.needs_records(flagged, INSTANT) is False
    assert cs.needs_records(cycling, INSTANT) is False
    assert cs.needs_records(old, INSTANT) is False
    # A date as_of with its zone is the last instant of that local day.
    assert cs.needs_records(clean, date(2026, 10, 31), ZONE) is True


def test_window_edges_at_90_days() -> None:
    edge_out = _row("out-90.00", start=(INSTANT_UTC - timedelta(days=90)).isoformat())
    edge_in = _row("in-89.99", start=(INSTANT_UTC - timedelta(days=89.99)).isoformat())
    after = _row("after", start=(INSTANT_UTC + timedelta(microseconds=1)).isoformat())
    for row in (edge_out, edge_in, after):
        row["segments"] = _segments(130, 4.0)
    assert cs.needs_records(edge_out, INSTANT) is False
    assert cs.needs_records(edge_in, INSTANT) is True
    assert cs.needs_records(after, INSTANT) is False
    result = cs.estimate([edge_out, edge_in, after], INSTANT)
    assert [s.session_id for s in result.stretches] == ["in-89.99"]
    assert result.excluded == ()
    # The same rows flagged: only the one inside is listed.
    for row in (edge_out, edge_in, after):
        row["quality_flags"] = '["smart_recording"]'
    flagged = cs.estimate([edge_out, edge_in, after], INSTANT)
    assert [e.session_id for e in flagged.excluded] == ["in-89.99"]
    assert flagged.unavailable == cs.NO_CANDIDATE_RUNS


def test_outside_the_window_is_never_listed() -> None:
    rows = [
        _row("cycling-2015", start="2015-06-01T08:00:00+00:00", sport="cycling"),
        _row("flagged-2015", start="2015-06-02T08:00:00+00:00", quality_flags="{"),
        _row("cycling-inside", 3, sport="cycling"),
        _row("cycling-after", start=(INSTANT_UTC + timedelta(days=1)).isoformat(), sport="cycling"),
    ]
    result = cs.estimate(rows, INSTANT)
    assert [(e.session_id, e.reason) for e in result.excluded] == [("cycling-inside", "not_running")]
    assert result.unavailable == cs.NO_CANDIDATE_RUNS


@pytest.mark.parametrize("zone", ["Pacific/Auckland", "America/Los_Angeles"])
def test_an_as_of_on_the_first_calendar_day_never_raises(zone: str) -> None:
    tz = ZoneInfo(zone)
    rows = [
        _row("year-one", start="0001-01-01T00:00:00+00:00"),
        _row("year-one-east", start="0001-01-01T12:00:00+14:00"),
        _row("year-one-west", start="0001-01-01T01:00:00-12:00", quality_flags='["smart_recording"]'),
        _row("modern", start="2026-10-01T08:00:00+00:00"),
    ]
    for row in rows:
        cs.needs_records(row, date(1, 1, 1), tz)
    result = cs.estimate(rows, date(1, 1, 1), tz)
    assert "modern" not in {s.session_id for s in result.stretches}
    assert "modern" not in {e.session_id for e in result.excluded}
    assert result.unavailable is not None


# ---------------------------------------------------------------------------- AC4: the rules' edges


def test_a_stretch_beaten_by_a_longer_one_is_dropped() -> None:
    # The 300 s stretch at 4.0 m/s is slower than the 480 s one at 4.2 m/s.
    points = [(120, 4.5 * 120), (300, 4.0 * 300), (480, 4.2 * 480), (720, 4.1 * 720)]
    result = _fit(_points(points))
    stretches = _by_length(result)
    assert stretches[300].drop_reason == "beaten_by_longer"
    assert stretches[300].beaten_by_s == 480
    assert all(stretches[length].drop_reason is None for length in (120, 480, 720))
    _available(result)
    kept = [(float(length), d) for length, d in points if length != 300]
    assert abs(result.cs_m_per_s - cs_oracle.line(kept).cs) <= 1e-9
    # An equal speed is not a higher one: kept.
    equal = _by_length(_fit(_points([(120, 4.5 * 120), (300, 1260.0), (480, 2016.0), (720, 4.1 * 720)])))
    assert equal[300].drop_reason is None


def test_the_beating_length_is_the_shortest_faster_longer_stretch() -> None:
    # 120 is beaten by 300, 480 and 720; 300 (itself dropped, by 480) is the shortest of them.
    points = [(120, 4.0 * 120), (300, 4.1 * 300), (480, 4.3 * 480), (720, 4.2 * 720)]
    stretches = _by_length(_fit(_points(points)))
    assert stretches[120].beaten_by_s == 300
    assert stretches[300].beaten_by_s == 480
    assert stretches[480].drop_reason is None and stretches[480].beaten_by_s is None
    assert stretches[720].drop_reason is None


def _exact_excess_points(target: float) -> list[tuple[float, float]]:
    """``BASE``'s first three with a 720 s distance whose far-off excess is exactly ``target``."""
    l_prev, d_prev = BASE[2]
    exponent = (1.06 - 1.0) / 1.06
    # Not every predicted pace leaves ``predicted + target`` on the float grid, so the 480 s
    # distance is stepped by ulps too until one does.
    for _ in range(2000):
        p_prev = 1000.0 * l_prev / d_prev
        d = 1000.0 * 720 / (p_prev * (720 / l_prev) ** exponent + target)
        for _ in range(40):
            excess = cs_oracle.far_off_excess((l_prev, d_prev), (720, d))
            if excess == target:
                return [*BASE[:2], (l_prev, d_prev), (720, d)]
            d = math.nextafter(d, 0.0 if excess < target else math.inf)
        d_prev = math.nextafter(d_prev, math.inf)
    raise AssertionError("no float distance gives the exact excess")


def test_far_off_edges() -> None:
    at = _fit(_points(_exact_excess_points(15.0)))
    assert at.far_off_excess_s_per_km == 15.0
    _available(at)

    over_points = [
        *BASE[:3],
        (720, 1000.0 * 720 / (1000.0 * 480 / BASE[2][1] * 1.5 ** (0.06 / 1.06) + 15.01)),
    ]
    over = _fit(_points(over_points))
    assert over.unavailable == cs.LONGEST_FAR_OFF
    assert abs(over.far_off_excess_s_per_km - 15.01) < 1e-9
    assert (
        abs(over.far_off_excess_s_per_km - cs_oracle.far_off_excess(over_points[-2], over_points[-1])) <= 1e-9
    )
    assert over.cs_m_per_s is None and over.label is None
    assert len(over.stretches) == 4


def test_spread_edges() -> None:
    shortest_300 = _fit(_points([(300, 4.3 * 300), (480, 4.2 * 480), (720, 4.1 * 720)]))
    _available(shortest_300)
    shortest_301 = _fit(_points([(301, 4.3 * 301), (480, 4.2 * 480), (720, 4.1 * 720)]))
    assert shortest_301.unavailable == cs.SPREAD_TOO_NARROW
    longest_720 = _fit(_points([(120, 4.5 * 120), (300, 4.3 * 300), (720, 4.1 * 720)]))
    _available(longest_720)
    longest_719 = _fit(_points([(120, 4.5 * 120), (300, 4.3 * 300), (719, 4.1 * 719)]))
    assert longest_719.unavailable == cs.SPREAD_TOO_NARROW
    # The shortest kept stretch, not the shortest served: a dropped 120 s leaves 301 s shortest.
    dropped_short = _fit(_points([(120, 4.0 * 120), (301, 4.3 * 301), (480, 4.2 * 480), (720, 4.1 * 720)]))
    assert _by_length(dropped_short)[120].drop_reason == cs.BEATEN_BY_LONGER
    assert dropped_short.unavailable == cs.SPREAD_TOO_NARROW
    assert shortest_301.cs_m_per_s is None and shortest_301.far_off_excess_s_per_km is None


def test_two_kept_stretches_are_too_few() -> None:
    two = _fit(_points([(300, 4.3 * 300), (720, 4.1 * 720)]))
    assert two.unavailable == cs.TOO_FEW_STRETCHES
    assert len(two.stretches) == 2
    three_one_dropped = _fit(_points([(120, 4.0 * 120), (300, 4.3 * 300), (720, 4.1 * 720)]))
    assert three_one_dropped.unavailable == cs.TOO_FEW_STRETCHES
    assert len(three_one_dropped.stretches) == 3
    three = _fit(_points([(120, 4.5 * 120), (300, 4.3 * 300), (720, 4.1 * 720)]))
    _available(three)
    assert three.t_multiplier == pytest.approx(3.077683537175, abs=1e-12)


def test_no_candidate_runs() -> None:
    empty = _fit([])
    assert empty.unavailable == cs.NO_CANDIDATE_RUNS
    assert empty.stretches == ()
    # A candidate with no stretch at any length is a candidate: too few stretches.
    no_stretch = _fit([_run("short", {})])
    assert no_stretch.unavailable == cs.TOO_FEW_STRETCHES


@pytest.mark.parametrize(
    ("races", "label"),
    [
        ([], "low"),
        ([{"tag": "race", "duration": 1800.0}], "medium"),
        ([{"tag": "race", "duration": 1801.0}], "low"),
        ([{"tag": "race", "duration": 1800.0, "admitted": True, "name": "Threshold"}], "low"),
        ([{"tag": "race", "duration": None}], "low"),
        ([{"tag": "training", "duration": 1500.0}], "low"),
        ([{"tag": "race", "duration": 1206.0}, {"tag": "race", "duration": 1233.0}], "high"),
        ([{"tag": "race", "duration": 1206.0}, {"tag": "race", "duration": 11138.0}], "medium"),
        (
            [
                {"tag": "race", "duration": 1206.0},
                {"tag": "race", "duration": 1233.0},
                {"tag": "race", "duration": 900.0},
            ],
            "high",
        ),
    ],
)
def test_label_counts_short_races_only(races: list[dict], label: str) -> None:
    lengths = [720, 480, 300]
    meta = {lengths[k]: race for k, race in enumerate(races)}
    result = _fit(_points(BASE, meta))
    _available(result)
    assert result.label == label
    oracle_runs = [(r.get("tag"), r.get("admitted", False), r.get("duration")) for r in races]
    assert cs_oracle.label(oracle_runs) == label
    # The fit error is not a label input: a noisier line with the same runs keeps the label.
    noisy = [(120, 4.9 * 120), (300, 4.3 * 300), (480, 4.2 * 480), (720, 4.1 * 720)]
    assert _fit(_points(noisy, meta)).label == label


def test_a_dropped_race_does_not_lift_the_label() -> None:
    points = [(120, 4.5 * 120), (300, 4.0 * 300), (480, 4.2 * 480), (720, 4.1 * 720)]
    result = _fit(_points(points, {300: {"tag": "race", "duration": 1200.0}}))
    assert _by_length(result)[300].drop_reason == cs.BEATEN_BY_LONGER
    assert result.label == "low"


def test_collinear_stretches_serve_a_zero_interval() -> None:
    result = _fit(_points([(120, 580.0), (300, 1300.0), (720, 2980.0)]))
    _available(result)
    assert result.cs_m_per_s == 4.0
    assert result.d_prime_m == 100.0
    assert result.error_m_per_s == 0.0 and result.d_prime_error_m == 0.0
    assert result.interval_low_s_per_km == result.cs_s_per_km == result.interval_high_s_per_km == 250.0
    assert result.d_prime_low_m == result.d_prime_high_m == 100.0
    assert result.d_prime_not_positive is False


def test_a_slow_bound_at_zero_speed_is_not_finite() -> None:
    # CS 1.88 m/s with se 0.88 and t 3.08 at one degree of freedom: the slow speed bound is below 0.
    slow = _fit(_points([(120, 1080.0), (300, 900.0), (720, 2088.0)]))
    assert slow.unavailable == cs.ESTIMATE_NOT_FINITE
    assert slow.cs_m_per_s is None and slow.interval_high_s_per_km is None and slow.label is None
    assert len(slow.stretches) == 3
    oracle = cs_oracle.line([(120.0, 1080.0), (300.0, 900.0), (720.0, 2088.0)])
    assert oracle.cs > 0 and oracle.cs - oracle.t * oracle.se < 0
    # A CS that is not positive is not finite either.
    negative = _fit(_points([(120, 3600.0), (300, 1200.0), (720, 2808.0)]))
    assert negative.unavailable == cs.ESTIMATE_NOT_FINITE


def test_a_slow_bound_of_exactly_zero_speed_is_not_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    # "At or below 0": the t multiplier is set to CS / se, so the slow bound CS - t * se is exactly
    # 0.0 in floats (asserted below), and the result is refused, never a division by zero.
    points = [(120, 540.0), (300, 1290.0), (480, 2016.0), (720, 2952.0)]
    monkeypatch.setattr(cs.riegel_b, "t_multiplier", lambda dof: 1.0)
    unit = _fit(_points(points))
    _available(unit)
    speed, se = unit.cs_m_per_s, unit.error_m_per_s
    t = speed / se
    assert speed - t * se == 0.0, (speed, se, t)
    monkeypatch.setattr(cs.riegel_b, "t_multiplier", lambda dof: t)
    zero = _fit(_points(points))
    assert zero.unavailable == cs.ESTIMATE_NOT_FINITE
    assert zero.cs_m_per_s is None and zero.interval_high_s_per_km is None
    assert len(zero.stretches) == 4


def test_d_prime_not_positive_is_flagged() -> None:
    # Equal speeds put D' at exactly 0 (the shape check drops any shorter stretch slower than a
    # longer one, which is what a negative intercept needs). Served as computed, flagged.
    result = _fit(_points([(120, 480.0), (300, 1200.0), (720, 2880.0)]))
    _available(result)
    assert result.d_prime_m == 0.0
    assert result.d_prime_not_positive is True
    positive = _fit(_points(BASE))
    assert positive.d_prime_m > 0 and positive.d_prime_not_positive is False


def test_unavailable_still_serves_stretches() -> None:
    cases = {
        cs.TOO_FEW_STRETCHES: [(300, 4.3 * 300), (720, 4.1 * 720)],
        cs.SPREAD_TOO_NARROW: [(301, 4.3 * 301), (480, 4.2 * 480), (720, 4.1 * 720)],
        cs.LONGEST_FAR_OFF: [*BASE[:3], (720, 4.1 * 720 * 0.9)],
        cs.ESTIMATE_NOT_FINITE: [(120, 1080.0), (300, 900.0), (720, 2088.0)],
    }
    for reason, points in cases.items():
        result = _fit(_points(points))
        assert result.unavailable == reason
        assert [s.length_s for s in result.stretches] == [length for length, _d in points]
        assert [s.distance_m for s in result.stretches] == [d for _length, d in points]
        assert all(s.session_id == f"run-{s.length_s}" for s in result.stretches)
        assert result.cs_m_per_s is None and result.label is None
        assert dict(result.parameters) == dict(cs.PARAMETERS)


def test_parameters_equal_the_reference() -> None:
    assert dict(cs.PARAMETERS) == {
        "window_days": 90,
        "stretch_lengths_s": (120, 180, 300, 480, 720, 1200),
        "pause_break_s": 10,
        "population_b": 1.06,
        "far_off_s_per_km": 15,
        "min_stretches": 3,
        "max_shortest_s": 300,
        "min_longest_s": 720,
        "short_race_max_s": 1800,
        "coverage": 0.80,
    }
    with pytest.raises(TypeError):
        cs.PARAMETERS["window_days"] = 1  # type: ignore[index]
    from runcoach_api.metrics import gap_stretches

    assert cs.PARAMETERS["stretch_lengths_s"] is gap_stretches.STRETCH_LENGTHS_S
    assert cs.PARAMETERS["pause_break_s"] is gap_stretches.PAUSE_BREAK_S


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("where", [120, 480, 720])
def test_non_finite_input_is_estimate_not_finite(bad: float, where: int) -> None:
    points = [(length, bad if length == where else d) for length, d in BASE]
    result = _fit(_points(points))
    assert result.unavailable == cs.ESTIMATE_NOT_FINITE
    assert result.cs_m_per_s is None and result.far_off_excess_s_per_km is None
    stretches = _by_length(result)
    assert len(stretches) == 4
    assert stretches[where].pace_s_per_km is None
    assert math.isnan(stretches[where].distance_m) if math.isnan(bad) else stretches[where].distance_m == bad
    # Ranked first at its length, a non-finite stretch is served rather than stepped around.
    rival = _run("rival", {where: 4.6 * where}, days_ago=0.5)
    again = _fit([*_points(points), rival])
    assert _by_length(again)[where].session_id == f"run-{where}"
    assert again.unavailable == cs.ESTIMATE_NOT_FINITE


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_best_of_a_run_used_longer_is_estimate_not_finite(bad: float) -> None:
    # Run a is chosen at 1200 s, so its 720 s best is never chosen; it is still input to the fit.
    runs = [
        _run("a", {1200: 4800.0, 720: bad}, days_ago=1.0),
        _run("b", {720: 2952.0}, days_ago=2.0),
        _run("c", {300: 1290.0}, days_ago=3.0),
        _run("d", {120: 540.0}, days_ago=4.0),
    ]
    result = _fit(runs)
    assert result.unavailable == cs.ESTIMATE_NOT_FINITE
    assert result.cs_m_per_s is None and result.far_off_excess_s_per_km is None
    stretches = _by_length(result)
    assert sorted(stretches) == [120, 300, 720, 1200]
    assert stretches[720].session_id == "b" and stretches[1200].session_id == "a"
    # The same runs with a finite 720 s best in run a serve an estimate.
    runs[0] = _run("a", {1200: 4800.0, 720: 2900.0}, days_ago=1.0)
    _available(_fit(runs))


def test_a_dropped_stretch_at_or_below_zero_metres_is_ignored() -> None:
    # Reference section 4 step 2 drops the 0 m stretch (beaten by 180 s); step 7 names no cause
    # it meets, so the fit is served without it.
    points = [(120, 0.0), (180, 4.4 * 180), (300, 4.3 * 300), (480, 4.2 * 480), (720, 4.1 * 720)]
    result = _fit(_points(points))
    stretches = _by_length(result)
    assert stretches[120].drop_reason == "beaten_by_longer" and stretches[120].beaten_by_s == 180
    assert stretches[120].pace_s_per_km is None and stretches[120].distance_m == 0.0
    _available(result)
    kept = [(float(length), d) for length, d in points if length != 120]
    assert abs(result.cs_m_per_s - cs_oracle.line(kept).cs) <= 1e-9
    assert abs(result.cs_s_per_km - 250.27) <= 0.01
    negative = _fit(_points([(120, -5.0), *points[1:]]))
    assert _by_length(negative)[120].drop_reason == "beaten_by_longer"
    _available(negative)
    # A kept stretch at or below 0 m still refuses: no pace can be served from it.
    for distance in (0.0, -1.0):
        kept_bad = _fit(_points([*points[1:4], (720, distance)]))
        assert _by_length(kept_bad)[720].drop_reason is None
        assert kept_bad.unavailable == cs.ESTIMATE_NOT_FINITE


def test_a_stationary_run_through_estimate_is_not_finite_and_never_raises() -> None:
    # An 800 s run whose every counted segment has v_actual 0 (a treadmill run with no speed):
    # its best stretches are 0 m and kept, so no pace can be served; estimate refuses, never raises.
    result = cs.estimate([_row("still", segments=_segments(800, 0.0))], INSTANT)
    print(f"  {result.unavailable} {[(s.length_s, s.distance_m, s.pace_s_per_km) for s in result.stretches]}")
    assert result.unavailable == cs.ESTIMATE_NOT_FINITE
    assert result.excluded == ()
    assert result.stretches and all(s.distance_m == 0.0 for s in result.stretches)
    assert all(s.drop_reason is None and s.pace_s_per_km is None for s in result.stretches)


# ---------------------------------------------------------------------------- independence


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            names.add(module)
            names.update(
                f"{module}.{alias.name}" if module.strip(".") else module + alias.name for alias in node.names
            )
    return names


def test_the_oracle_imports_nothing_from_the_estimator() -> None:
    oracle = _imports(ORACLE_PATH)
    assert not {n for n in oracle if n.split(".")[0] == "runcoach_api" or n.startswith(".")}, oracle
    assert "runcoach_api" not in ORACLE_PATH.read_text(encoding="utf-8").replace("``runcoach_api``", "")
    riegel = _imports(ORACLE_PATH.parent / "riegel_oracle.py")
    assert not {n for n in riegel if n.split(".")[0] == "runcoach_api" or n.startswith(".")}, riegel
    assert not any(hasattr(cs_oracle, attr) for attr in ("critical_speed", "estimate", "fit"))

    estimator = _imports(ESTIMATOR_PATH)
    forbidden = {"sqlite3", "runcoach_api.db", "runcoach_api.main", "runcoach_api.config", "fastapi"}
    hits = {
        n
        for n in estimator
        if n.split(".")[0] in {"sqlite3", "fastapi"}
        or any(n == f or n.startswith(f + ".") for f in forbidden)
        or n.startswith("runcoach_api.routes")
        or (n.startswith(".") and any(part in {"db", "main", "config"} for part in n.lstrip(".").split(".")))
    }
    assert not hits, hits
    assert "runcoach_api.metrics.gap_stretches" in estimator  # the walk does see a from-import's names


# ---------------------------------------------------------------------------- adversarial


def _base_rows() -> list[dict]:
    """Four clean runs: kept stretches at 120, 300, 480 and 720 s from constant-speed segments.
    The 720 s run is a Race-tagged 5K, its duration fields varied by the caller."""
    return [
        _row("r120", 20, segments=_segments(130, 4.5)),
        _row("r300", 21, segments=_segments(310, 4.3)),
        _row("r480", 22, segments=_segments(500, 4.2)),
        _row("r720", 23, segments=_segments(730, 4.1), tag="race", name="5K", timer=1500.0),
    ]


def _overflow_segments(moving_gap_first: bool) -> list[dict]:
    """1300 one-second segments, one of whose GAP distances overflows to inf: as a counted segment
    the windows through it are inf; as the first segment and a moving gap, every eligible window's
    difference of prefix sums is inf - inf, NaN."""
    segments = _segments(1300, 4.0)
    k = 0 if moving_gap_first else 650
    segments[k] = {**segments[k], "v_actual": 1e308, "g": 10.0, "moving_gap": moving_gap_first}
    return segments


DURATIONS = {
    "bool": True,
    "str": "1500",
    "zero": 0,
    "negative": -1500.0,
    "nan": math.nan,
    "inf": math.inf,
    "huge-int": 10**400,
}


def test_an_adversarial_row_never_raises() -> None:
    # The base set: the race run counts at 1500 s.
    base = cs.estimate(_base_rows(), INSTANT)
    _available(base)
    assert base.label == "medium"
    assert [s.length_s for s in base.stretches] == [120, 300, 480, 720]

    # Degenerate durations: the race does not count, nothing raises, the fit stands.
    for key, value in DURATIONS.items():
        rows = _base_rows()
        rows[3]["timer_time_s"] = value
        result = cs.estimate(rows, INSTANT)
        _available(result)
        assert result.label == "low", key
    # A null timer reads the summary's duration (row key first, then the summary text).
    for summary, row_duration, label in [
        ('{"duration_s": 1500.0}', None, "medium"),
        (None, 1500.0, "medium"),
        ("{not json", None, "low"),
        ('["a list"]', None, "low"),
        ('{"duration_s": "1500"}', None, "low"),
        (b"\xff\xfe", None, "low"),
        (None, None, "low"),
    ]:
        rows = _base_rows()
        rows[3].update(timer_time_s=None, summary=summary, duration_s=row_duration)
        result = cs.estimate(rows, INSTANT)
        _available(result)
        assert result.label == label, summary

    # Degenerate stored and record values on one more run: listed or not, never raised.
    extra = {
        "flags-int": {"quality_flags": 5},
        "flags-text": {"quality_flags": '"distance_regressed"'},
        "flags-bytes": {"quality_flags": b"\xff"},
        "flags-none": {"quality_flags": None},
        "f013-flags-dict": {"flags": {"grade_clamped": True}},
        "f013-flags-none": {"flags": None},
        "segments-none": {"segments": None},
        "segments-empty": {"segments": []},
        "segments-text": {"segments": "abc"},
        "segments-missing-key": {"segments": [{"t_start": 0.0}]},
        "segments-not-mappings": {"segments": [1, 2, 3]},
        "nan-v": {"segments": [{**s, "v_actual": math.nan} for s in _segments(800, 9.0)]},
        "sport-none": {"sport": None},
        "tag-list": {"activity_tag": ["race"]},
        "name-int": {"name": 7},
        "start-garbage": {"start_time": "not a date"},
        "start-none": {"start_time": None},
        "start-naive": {"start_time": "2026-10-20T08:00:00"},
        "start-number": {"start_time": 12345},
    }
    for key, change in extra.items():
        row = _row(f"x-{key}", 15)
        row.update(change)
        cs.needs_records(row, INSTANT)
        result = cs.estimate([*_base_rows(), row], INSTANT)
        _available(result)
        assert {s.session_id for s in result.stretches} <= {"r120", "r300", "r480", "r720"}, key
    listed = {}
    for key, change in extra.items():
        row = _row(f"x-{key}", 15)
        row.update(change)
        result = cs.estimate([row], INSTANT)
        listed[key] = [(e.reason, e.source) for e in result.excluded]
    assert listed["flags-int"] == [("quality_flags_unreadable", None)]
    assert listed["flags-none"] == []  # no stored flags: a candidate
    assert listed["f013-flags-dict"] == [("features_unavailable", None)]
    assert listed["segments-none"] == [("features_unavailable", None)]
    assert listed["segments-empty"] == []  # a candidate with no stretch
    assert listed["segments-missing-key"] == [("features_unavailable", None)]
    assert listed["segments-not-mappings"] == [("features_unavailable", None)]
    assert listed["sport-none"] == [("not_running", None)]
    assert listed["start-garbage"] == listed["start-none"] == listed["start-naive"] == []
    # A NaN v_actual is a moving gap: that run has no stretch.
    nan_row = _row("nan-only", 15, segments=[{**s, "v_actual": math.nan} for s in _segments(800, 9.0)])
    assert cs.estimate([nan_row], INSTANT).stretches == ()

    # A run whose GAP distance overflows: inf through a counted segment, NaN through a moving gap.
    # The real path, best_stretches then fit, refuses the estimate and serves the stretches.
    for moving_gap_first, check in [(False, math.isinf), (True, math.isnan)]:
        rows = [*_base_rows(), _row("overflow", 16, segments=_overflow_segments(moving_gap_first))]
        result = cs.estimate(rows, INSTANT)
        assert result.unavailable == cs.ESTIMATE_NOT_FINITE, moving_gap_first
        served = _by_length(result)
        assert served[1200].session_id == "overflow"
        assert check(served[1200].distance_m) and served[1200].pace_s_per_km is None
        assert result.cs_m_per_s is None and result.label is None


def test_estimate_serves_run_metadata_and_window_instant() -> None:
    rows = _base_rows()
    rows[2]["admitted_as_workout"] = True
    result = cs.estimate(rows, date(2026, 10, 31), ZONE)
    assert result.as_of == INSTANT
    stretch = _by_length(result)[720]
    assert stretch.run_name == "5K" and stretch.activity_tag == "race" and stretch.run_duration_s == 1500.0
    assert stretch.start_time == datetime.fromisoformat(rows[3]["start_time"])
    assert stretch.distance_m == pytest.approx(4.1 * 720, rel=1e-12)
    assert json.dumps(dict(result.parameters), default=list)
