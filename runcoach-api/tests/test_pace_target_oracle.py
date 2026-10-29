"""The pace-target oracle reproduces every printed figure of the F032 reference's section 8.

The oracle (``tests/support/pace_target_oracle.py``) is written from the F032 reference sections
3.1, 3.2, 4.1 and 8 alone and imports nothing from ``runcoach_api``; the last test here parses its
imports to pin that, so the expected values the estimator tests compare against cannot share code
with the module they check. Times match to 1e-3 s; halfwidths, fractions, b and se to 1e-6.
"""

from __future__ import annotations

import ast
import importlib.util
import math
import sys
from pathlib import Path

import pytest

ORACLE_PATH = Path(__file__).parent / "support" / "pace_target_oracle.py"

# Loaded by path: the tests directory is not a package.
_SPEC = importlib.util.spec_from_file_location("pace_target_oracle", ORACLE_PATH)
oracle = importlib.util.module_from_spec(_SPEC)
sys.modules["pace_target_oracle"] = oracle
_SPEC.loader.exec_module(oracle)  # type: ignore[union-attr]

HALF = 21097.5
MARATHON = 42195.0

E1 = [(5000.0, 1200.0, 1.0), (10000.0, 2526.937004, 1.0), (HALF, 5465.036808, 1.0)]
E2 = [(5000.0, 1200.0, 1.0), (10000.0, 2502.0, 1.0), (HALF, 5530.0, 1.0), (MARATHON, 11610.0, 1.0)]
E3 = [(5000.0, 1200.0, 1.0), (10000.0, 2502.0, 1.0), (HALF, 5530.0, 1.0), (MARATHON, 11610.0, 0.5)]
E4 = [
    (5000.0, 1200.0, 1.0),
    (5000.0, 1203.0, 1.0),
    (10000.0, 2540.0, 1.0),
    (10000.0, 2545.0, 1.0),
    (MARATHON, 11300.0, 1.0),
]
A = [(5000.0, 1200.0, 1.0), (10000.0, 2574.828566, 1.0), (HALF, 5841.627023, 1.0)]

TIME = 1e-3
FRACTION = 1e-6


def _check_fit(races, b, se, df, t, n_eff=None) -> None:
    fit = oracle.fit(races)
    assert fit.b == pytest.approx(b, abs=FRACTION)
    assert fit.se == pytest.approx(se, abs=FRACTION)
    assert fit.df == df
    assert fit.t == pytest.approx(t, abs=FRACTION)
    if n_eff is not None:
        assert fit.n_eff == pytest.approx(n_eff, abs=FRACTION)


def _check_projection(races, distance_m, time, low, high, halfwidth, pace=None) -> None:
    p = oracle.projection(races, distance_m)
    assert p.route == "fitted_line"
    assert p.time_s == pytest.approx(time, abs=TIME)
    assert p.interval == pytest.approx((low, high), abs=TIME)
    assert p.halfwidth == pytest.approx(halfwidth, abs=FRACTION)
    assert p.pace_s_per_km == pytest.approx(p.time_s / (distance_m / 1000.0), abs=TIME)
    if pace is not None:
        assert p.pace_s_per_km == pytest.approx(pace, abs=TIME)


def test_oracle_matches_the_reference_worked_examples() -> None:
    # E1, exactly three.
    _check_fit(E1, 1.052765, 0.011856, 1, 3.077684)
    _check_projection(E1, 5000.0, 1206.148000, 1147.167166, 1268.161295, 0.051414)
    _check_projection(E1, HALF, 5491.027718, 5220.744031, 5775.304291, 0.051771, pace=260.269118)
    _check_projection(E1, MARATHON, 11391.149466, 10649.494125, 12184.455397, 0.069642)
    _check_projection(E1, 60000.0, 16501.563696, 15268.605313, 17834.084964, 0.080751)
    # E2, four tight.
    _check_fit(E2, 1.063891, 0.001578, 2, 1.885618)
    _check_projection(E2, 5000.0, 1198.174356, 1190.781957, 1205.612647, 0.006208)
    _check_projection(E2, HALF, 5542.806803, 5512.797559, 5572.979404, 0.005444, pace=262.723394)
    _check_projection(E2, MARATHON, 11587.584949, 11516.092810, 11659.520913, 0.006208)
    _check_projection(E2, 60000.0, 16852.003696, 16738.042964, 16966.740328, 0.006808)
    # E3, E2 with the marathon at weight 0.5.
    _check_fit(E3, 1.063273, 0.001710, 1, 3.077684, n_eff=3.769231)
    _check_projection(E3, 5000.0, 1198.521270, 1186.898816, 1210.257535, 0.009792)
    _check_projection(E3, HALF, 5539.474915, 5490.311745, 5589.078319, 0.008955)
    _check_projection(E3, MARATHON, 11575.653915, 11453.279395, 11699.335966, 0.010685)
    _check_projection(E3, 60000.0, 16830.985700, 16632.907662, 17031.422611, 0.011909)
    # E4, one race load-bearing.
    _check_fit(E4, 1.050660, 0.007739, 3, 1.637744)
    _check_projection(E4, HALF, 5500.352498, 5359.638602, 5644.760747, 0.026254)
    _check_projection(E4, MARATHON, 11393.852623, 11054.789427, 11743.315279, 0.030671)
    # A, three races on b 1.10.
    _check_fit(A, 1.099280, 0.001187, 1, 3.077684)
    _check_projection(A, HALF, 5844.403570, 5814.932631, 5874.023873, 0.005068)


def test_oracle_leave_one_out_matches_the_reference() -> None:
    # E1 (half): with two races left none clears the bar, so every one is the closest race.
    e1 = oracle.leave_one_out(E1, HALF)
    assert [r.route for r in e1] == ["closest_race"] * 3
    assert [r.time_s for r in e1] == pytest.approx([5465.036808, 5465.036808, 5575.441592], abs=TIME)
    assert oracle.largest_countable_shift(e1) is None

    # E4 (half): all fitted line, the marathon's shift +0.036328.
    e4_half = oracle.leave_one_out(E4, HALF)
    assert [r.route for r in e4_half] == ["fitted_line"] * 5
    assert [r.time_s for r in e4_half] == pytest.approx(
        [5502.838230, 5502.207273, 5484.437376, 5481.776464, 5700.171152], abs=TIME
    )
    assert e4_half[4].shift == pytest.approx(0.036328, abs=FRACTION)
    assert oracle.largest_countable_shift(e4_half) == pytest.approx(0.036328, abs=FRACTION)
    for r in e4_half:
        base = oracle.projection(E4, HALF).time_s
        assert r.shift == pytest.approx(r.time_s / base - 1, abs=1e-12)

    # E4 (marathon): u is the marathon's +0.058655.
    e4_mar = oracle.leave_one_out(E4, MARATHON)
    assert [r.route for r in e4_mar] == ["fitted_line"] * 5
    assert [r.time_s for r in e4_mar] == pytest.approx(
        [11370.704887, 11376.575064, 11361.292003, 11355.847950, 12062.163498], abs=TIME
    )
    assert e4_mar[4].shift == pytest.approx(0.058655, abs=FRACTION)
    halfwidth = oracle.projection(E4, MARATHON).halfwidth
    assert max(halfwidth, oracle.largest_countable_shift(e4_mar)) == pytest.approx(0.058655, abs=FRACTION)

    # A (half): all closest race, the half's -0.027940 not countable.
    a = oracle.leave_one_out(A, HALF)
    assert [r.route for r in a] == ["closest_race"] * 3
    assert [r.time_s for r in a] == pytest.approx([5841.627023, 5841.627023, 5681.109681], abs=TIME)
    assert a[2].shift == pytest.approx(-0.027940, abs=FRACTION)
    assert oracle.largest_countable_shift(a) is None

    # E2 (half): the section 4.2 table's 0.257 % largest countable shift.
    e2 = oracle.leave_one_out(E2, HALF)
    assert [r.route for r in e2] == ["fitted_line"] * 4
    assert oracle.largest_countable_shift(e2) == pytest.approx(0.00257, abs=5e-6)


def test_oracle_fallback_and_tie() -> None:
    # Fallback: one race, the population exponent, no interval.
    p = oracle.projection([(21580.0, 5928.0, 1.0)], HALF)
    assert p.route == "closest_race"
    assert p.time_s == pytest.approx(5928.0 * (HALF / 21580.0) ** 1.06, abs=1e-9)
    assert p.time_s == pytest.approx(5787.600207, abs=TIME)
    assert p.interval is None and p.halfwidth is None
    assert p.pace_s_per_km == pytest.approx(5787.600207 / (HALF / 1000.0), abs=TIME)
    assert oracle.leave_one_out([(21580.0, 5928.0, 1.0)], HALF) is None

    # Tie: the log distances differ by about 1.78e-15, within tie_slack, so weight decides.
    tie = [(5000.0, 1200.0, 1.0), (20000.0, 5050.0, 0.8)]
    gaps = [abs(math.log(10000.0) - math.log(d)) for d, _t, _w in tie]
    assert gaps[0] != gaps[1]
    assert abs(gaps[0] - gaps[1]) < oracle.TIE_SLACK
    with_slack = oracle.closest_race(tie, 10000.0)
    assert with_slack.d_eq_m == 5000.0
    assert oracle.projection(tie, 10000.0).time_s == pytest.approx(1200.0 * 2**1.06, abs=1e-9)
    strict = oracle.closest_race(tie, 10000.0, tie_slack=0.0)
    assert strict.d_eq_m == 20000.0
    assert strict is not with_slack

    # Goal-time gap (E2, half).
    gap = oracle.goal_gap(oracle.projection(E2, HALF), 5500.0)
    assert gap.gap_s == pytest.approx(-42.806803, abs=TIME)
    assert gap.gap_fraction == pytest.approx(-0.007723, abs=FRACTION)
    assert gap.within_range is False
    assert oracle.goal_gap(oracle.projection(E2, HALF), 5530.0).within_range is True
    assert oracle.goal_gap(p, 5800.0).within_range is None


def test_the_pace_target_oracle_imports_nothing_from_runcoach_api() -> None:
    names: set[str] = set()
    for node in ast.walk(ast.parse(ORACLE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    assert names, "the walk saw no imports"
    assert not {n for n in names if n.split(".")[0] == "runcoach_api" or n.startswith(".")}, names
    assert not any(n.split(".")[0] in {"numpy", "scipy"} for n in names), names
    assert not hasattr(oracle, "riegel_b") and not hasattr(oracle, "estimate")
