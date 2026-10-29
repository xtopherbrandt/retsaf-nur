"""``metrics.pace_target.project`` serves the F032 goal-race projection (feature AC3, AC4 and AC5b;
reference sections 3.1, 3.2, 3.3 and 4.1) from the races F029 used and its estimate.

The expected figures come from two places that share no code with the module under test:
``support/pace_target_oracle.py`` (the reference's rules restated in raw sums, importing nothing
from ``runcoach_api``; ``test_pace_target_oracle.py`` pins that), compared to 1e-9, and the F032
reference section 8's printed values typed in here (times to 1e-3 s, fractions to 1e-6). Four
perturbations of the fitted line go red against the worked examples: dropping the ``(x0 - x_w)^2``
term, dropping ``1 / n_eff``, reading ``n`` for ``n_eff`` (E3) and reading ``D`` in km; the tie test
goes red when the closest race is compared without ``tie_slack``.
"""

from __future__ import annotations

import ast
import importlib.util
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from runcoach_api.metrics import pace_target, riegel_b
from runcoach_api.metrics.pace_target import Projection, Race, project

ORACLE_PATH = Path(__file__).parent / "support" / "pace_target_oracle.py"
if "pace_target_oracle" in sys.modules:
    oracle = sys.modules["pace_target_oracle"]
else:
    _SPEC = importlib.util.spec_from_file_location("pace_target_oracle", ORACLE_PATH)
    oracle = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
    sys.modules["pace_target_oracle"] = oracle
    _SPEC.loader.exec_module(oracle)  # type: ignore[union-attr]

ORACLE = 1e-9
TIME = 1e-3
FRACTION = 1e-6

HALF = 21097.5
MARATHON = 42195.0

# F032 reference section 8, as (d_eq_m, time_s, weight).
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

# Section 8's printed figures per (set, distance): time, low, high, halfwidth, and the pace where printed.
PRINTED = {
    ("E1", 5000.0): (1206.148000, 1147.167166, 1268.161295, 0.051414, None),
    ("E1", HALF): (5491.027718, 5220.744031, 5775.304291, 0.051771, 260.269118),
    ("E1", MARATHON): (11391.149466, 10649.494125, 12184.455397, 0.069642, None),
    ("E1", 60000.0): (16501.563696, 15268.605313, 17834.084964, 0.080751, None),
    ("E2", 5000.0): (1198.174356, 1190.781957, 1205.612647, 0.006208, None),
    ("E2", HALF): (5542.806803, 5512.797559, 5572.979404, 0.005444, 262.723394),
    ("E2", MARATHON): (11587.584949, 11516.092810, 11659.520913, 0.006208, None),
    ("E2", 60000.0): (16852.003696, 16738.042964, 16966.740328, 0.006808, None),
    ("E3", 5000.0): (1198.521270, 1186.898816, 1210.257535, 0.009792, None),
    ("E3", HALF): (5539.474915, 5490.311745, 5589.078319, 0.008955, None),
    ("E3", MARATHON): (11575.653915, 11453.279395, 11699.335966, 0.010685, None),
    ("E3", 60000.0): (16830.985700, 16632.907662, 17031.422611, 0.011909, None),
}
SETS = {"E1": E1, "E2": E2, "E3": E3, "E4": E4, "A": A}

FIRST_START = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _races(triples, starts=None) -> list[Race]:
    """Races in the order given, one day apart from ``FIRST_START`` unless ``starts`` says otherwise."""
    out = []
    for index, (d_eq, time_s, weight) in enumerate(triples):
        start = starts[index] if starts is not None else FIRST_START + timedelta(days=index)
        out.append(Race(session_id=f"s{index}", start_time=start, d_eq_m=d_eq, time_s=time_s, weight=weight))
    return out


def _estimate(unavailable: str | None = None) -> riegel_b.RiegelEstimate:
    """F029's estimate as the projection reads it: only ``unavailable`` matters here."""
    return riegel_b.RiegelEstimate(
        as_of=FIRST_START,
        efforts=(),
        excluded=(),
        value=None if unavailable else 1.06,
        interval_low=None,
        interval_high=None,
        error=None,
        t_multiplier=None,
        confidence=None if unavailable else "low",
        provisional=False,
        population_default=False,
        below_one=False,
        unavailable=unavailable,
    )


def _project(triples, distance_m: float, starts=None) -> Projection:
    return project(_races(triples, starts), _estimate(), distance_m)


@pytest.mark.parametrize(("name", "distance_m"), sorted(PRINTED, key=lambda key: (key[0], key[1])))
def test_fitted_line_matches_the_worked_examples(name: str, distance_m: float) -> None:
    served = _project(SETS[name], distance_m)
    expected = oracle.projection(SETS[name], distance_m)
    km = distance_m / 1000.0

    assert served.route == pace_target.FITTED_LINE == expected.route
    assert served.unavailable is None and served.provisional is False
    assert served.distance_m == distance_m
    # Against the oracle, to 1e-9.
    assert served.time_s == pytest.approx(expected.time_s, abs=ORACLE)
    assert served.interval_low == pytest.approx(expected.interval[0], abs=ORACLE)
    assert served.interval_high == pytest.approx(expected.interval[1], abs=ORACLE)
    assert served.halfwidth == pytest.approx(expected.halfwidth, abs=ORACLE)
    assert served.pace_s_per_km == pytest.approx(expected.pace_s_per_km, abs=ORACLE)
    assert served.pace_low == pytest.approx(expected.interval[0] / km, abs=ORACLE)
    assert served.pace_high == pytest.approx(expected.interval[1] / km, abs=ORACLE)
    # Against the reference's printed figures.
    time_s, low, high, halfwidth, pace = PRINTED[name, distance_m]
    assert served.time_s == pytest.approx(time_s, abs=TIME)
    assert served.interval_low == pytest.approx(low, abs=TIME)
    assert served.interval_high == pytest.approx(high, abs=TIME)
    assert served.halfwidth == pytest.approx(halfwidth, abs=FRACTION)
    assert served.pace_s_per_km == pytest.approx(time_s / km, abs=TIME)
    if pace is not None:
        assert served.pace_s_per_km == pytest.approx(pace, abs=TIME)
    # The halfwidth is the upper side's and the interval is the time scaled both ways by exp(t sp).
    assert served.halfwidth == pytest.approx(served.interval_high / served.time_s - 1, abs=1e-12)
    assert served.interval_low == pytest.approx(served.time_s / (1 + served.halfwidth), abs=1e-9)


def test_the_fitted_line_reads_the_fit_once_from_riegel_b(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fit has one source: ``project`` calls ``riegel_b.fit`` for the main line and once per
    leave-one-out, and reads its t multiplier from it."""
    calls: list[list[tuple[float, float, float]]] = []
    real_fit = riegel_b.fit

    def counting_fit(efforts):
        used = list(efforts)
        calls.append(used)
        return real_fit(used)

    monkeypatch.setattr(pace_target.riegel_b, "fit", counting_fit)
    served = _project(E4, HALF)
    assert served.route == pace_target.FITTED_LINE
    assert len(calls) == 1 + len(E4)
    assert calls[0] == [(d, t, w) for d, t, w in E4]
    assert served.t_multiplier == riegel_b.t_multiplier(3) == real_fit(E4).t


def test_closest_race_fallback() -> None:
    served = _project([(21580.0, 5928.0, 1.0)], HALF)
    expected = oracle.projection([(21580.0, 5928.0, 1.0)], HALF)

    assert served.route == pace_target.CLOSEST_RACE == expected.route
    assert served.unavailable is None
    assert served.provisional is True
    assert served.time_s == pytest.approx(expected.time_s, abs=ORACLE)
    assert served.time_s == pytest.approx(5787.600207, abs=TIME)
    assert served.time_s == pytest.approx(5928.0 * (HALF / 21580.0) ** riegel_b.POPULATION_B, abs=ORACLE)
    assert served.pace_s_per_km == pytest.approx(5787.600207 / (HALF / 1000.0), abs=TIME)
    assert served.interval_low is None and served.interval_high is None and served.halfwidth is None
    assert served.pace_low is None and served.pace_high is None
    assert served.t_multiplier is None
    assert served.closest is not None and served.closest.d_eq_m == 21580.0
    assert served.leave_one_out is None and served.largest is None

    # Two races below the bar: the nearer in log distance, not in metres.
    two = _project([(5000.0, 1200.0, 1.0), (21097.5, 5500.0, 1.0)], 10500.0)
    assert two.route == pace_target.CLOSEST_RACE
    # ln(10500) is 0.742 from ln(5000) and 0.698 from ln(21097.5); in metres the 5K is nearer.
    assert abs(10500.0 - 5000.0) < abs(10500.0 - HALF)
    assert two.closest is not None and two.closest.d_eq_m == HALF
    assert two.time_s == pytest.approx(5500.0 * (10500.0 / HALF) ** 1.06, abs=ORACLE)


def test_tie_goes_to_the_higher_weight() -> None:
    """Reference section 8's tie: the 5000 m and 20000 m log distances differ from ln(10000) by about
    1.78e-15, inside ``tie_slack``, so the weight-1 race wins; a strict comparison picks 20000 m."""
    tie = [(5000.0, 1200.0, 1.0), (20000.0, 5050.0, 0.8)]
    gaps = [abs(math.log(10000.0) - math.log(d)) for d, _t, _w in tie]
    assert gaps[0] != gaps[1] and gaps[1] < gaps[0]  # strict: the 20000 m race is nearer
    assert abs(gaps[0] - gaps[1]) < pace_target.TIE_SLACK

    served = _project(tie, 10000.0)
    assert served.route == pace_target.CLOSEST_RACE
    assert served.closest is not None and served.closest.d_eq_m == 5000.0
    assert served.time_s == pytest.approx(1200.0 * 2**1.06, abs=ORACLE)
    assert served.time_s == pytest.approx(oracle.projection(tie, 10000.0).time_s, abs=ORACLE)
    # The order of the races does not decide it.
    reversed_order = _project(list(reversed(tie)), 10000.0)
    assert reversed_order.closest is not None and reversed_order.closest.d_eq_m == 5000.0

    # Outside the slack the nearer race wins whatever its weight.
    apart = _project([(5000.0, 1200.0, 1.0), (19999.0, 5050.0, 0.8)], 10000.0)
    assert apart.closest is not None and apart.closest.d_eq_m == 19999.0


def test_tie_at_equal_weight_goes_to_the_later_start() -> None:
    """Equal weights and equal log distances: the later start wins, in either list order."""
    earlier, later = FIRST_START, FIRST_START + timedelta(days=200)
    tie = [(5000.0, 1200.0, 1.0), (20000.0, 5050.0, 1.0)]
    assert abs(abs(math.log(10000.0) - math.log(5000.0)) - abs(math.log(10000.0) - math.log(20000.0))) < 1e-9

    served = project(_races(tie, [earlier, later]), _estimate(), 10000.0)
    assert (
        served.closest is not None and served.closest.d_eq_m == 20000.0 and served.closest.start_time == later
    )
    assert served.time_s == pytest.approx(5050.0 * 0.5**1.06, abs=ORACLE)

    swapped = project(_races(tie, [later, earlier]), _estimate(), 10000.0)
    assert (
        swapped.closest is not None
        and swapped.closest.d_eq_m == 5000.0
        and swapped.closest.start_time == later
    )
    assert swapped.time_s == pytest.approx(1200.0 * 2**1.06, abs=ORACLE)

    # Exactly equal distances too (the same race twice at different starts): the later one.
    same = project(
        _races([(5000.0, 1200.0, 1.0), (5000.0, 1210.0, 1.0)], [later, earlier]), _estimate(), 10000.0
    )
    assert same.closest is not None and same.closest.time_s == 1200.0


def test_unavailable_reasons() -> None:
    def figures_null(served: Projection) -> None:
        assert served.route is None
        assert served.time_s is None and served.pace_s_per_km is None
        assert served.interval_low is None and served.interval_high is None and served.halfwidth is None
        assert served.pace_low is None and served.pace_high is None
        assert served.leave_one_out is None and served.largest is None and served.closest is None
        assert served.provisional is False

    none = project([], _estimate(), HALF)
    assert none.unavailable == pace_target.NO_RACES == "no_races"
    figures_null(none)

    not_finite = project(_races(E2), _estimate(riegel_b.ESTIMATE_NOT_FINITE), HALF)
    assert not_finite.unavailable == pace_target.RIEGEL_UNAVAILABLE == "riegel_unavailable"
    figures_null(not_finite)

    # No races and an unavailable estimate: no_races, the first of section 3.3's reasons.
    assert project([], _estimate(riegel_b.ESTIMATE_NOT_FINITE), HALF).unavailable == pace_target.NO_RACES

    # The upper interval end overflows exp: projection_not_finite, not an exception.
    overflow = project(_races(E2), _estimate(), 1e308)
    assert overflow.unavailable == pace_target.PROJECTION_NOT_FINITE == "projection_not_finite"
    figures_null(overflow)
    # A goal far beyond the races but still representable serves a figure.
    assert project(_races(E2), _estimate(), 1e6).unavailable is None

    # F029's other outcomes are not this module's concern: the population default projects.
    population = _estimate()
    assert project(_races([(21580.0, 5928.0, 1.0)]), population, HALF).route == pace_target.CLOSEST_RACE


def _loo(served: Projection):
    assert served.leave_one_out is not None
    return served.leave_one_out


def test_leave_one_out_matches_the_reference() -> None:
    # E4 (half): all fitted line, the marathon's shift +0.036328 is the largest.
    half = _project(E4, HALF)
    rows = _loo(half)
    expected = oracle.leave_one_out(E4, HALF)
    assert [row.route for row in rows] == [pace_target.FITTED_LINE] * 5
    assert all(row.countable for row in rows)
    assert [row.time_s for row in rows] == pytest.approx([e.time_s for e in expected], abs=ORACLE)
    assert [row.shift for row in rows] == pytest.approx([e.shift for e in expected], abs=ORACLE)
    assert [row.time_s for row in rows] == pytest.approx(
        [5502.838230, 5502.207273, 5484.437376, 5481.776464, 5700.171152], abs=TIME
    )
    assert rows[4].shift == pytest.approx(0.036328, abs=FRACTION)
    for row, race in zip(rows, _races(E4), strict=True):
        assert row.session_id == race.session_id and row.start_time == race.start_time
        assert row.shift == pytest.approx(row.time_s / half.time_s - 1, abs=1e-12)
    assert half.largest is rows[4]
    assert half.largest.session_id == "s4"

    # E4 (marathon): +0.058655.
    marathon = _project(E4, MARATHON)
    rows = _loo(marathon)
    assert [row.time_s for row in rows] == pytest.approx(
        [11370.704887, 11376.575064, 11361.292003, 11355.847950, 12062.163498], abs=TIME
    )
    assert [row.shift for row in rows] == pytest.approx(
        [e.shift for e in oracle.leave_one_out(E4, MARATHON)], abs=ORACLE
    )
    assert marathon.largest is rows[4] and marathon.largest.shift == pytest.approx(0.058655, abs=FRACTION)

    # E1 (half): each leave-one-out leaves two races, below the bar, so all closest race, none countable.
    e1 = _project(E1, HALF)
    rows = _loo(e1)
    assert [row.route for row in rows] == [pace_target.CLOSEST_RACE] * 3
    assert not any(row.countable for row in rows)
    assert [row.time_s for row in rows] == pytest.approx([5465.036808, 5465.036808, 5575.441592], abs=TIME)
    assert e1.largest is None

    # A (half): the half's -0.027940 is a closest-race shift and so not countable.
    a = _project(A, HALF)
    rows = _loo(a)
    assert [row.route for row in rows] == [pace_target.CLOSEST_RACE] * 3
    assert rows[2].shift == pytest.approx(-0.027940, abs=FRACTION)
    assert a.largest is None

    # E2 (half): the section 4.2 table's 0.257 %.
    e2 = _project(E2, HALF)
    assert e2.largest is not None and abs(e2.largest.shift) == pytest.approx(0.00257, abs=5e-6)
    assert abs(e2.largest.shift) == pytest.approx(
        oracle.largest_countable_shift(oracle.leave_one_out(E2, HALF)), abs=ORACLE
    )

    # Route closest_race: leave_one_out null.
    assert _project([(21580.0, 5928.0, 1.0)], HALF).leave_one_out is None


def test_the_largest_shift_tie_goes_to_the_earlier_start() -> None:
    """Two identical races at two starts leave the same races out, so their shifts are the same
    figure; where that figure is the largest countable one, the earlier start is ``largest``, in
    either list order, and a closest-race row never is, however large its shift."""
    doubled = [*E4, (MARATHON, 11300.0, 1.0)]
    earlier, later = FIRST_START + timedelta(days=4), FIRST_START + timedelta(days=40)
    starts = [FIRST_START + timedelta(days=index) for index in range(4)]
    rows = _loo(project(_races(doubled, [*starts, later, earlier]), _estimate(), HALF))
    assert rows[4].shift == rows[5].shift and rows[4].start_time == later and rows[5].start_time == earlier

    def row(session_id: str, start: datetime, shift: float, route: str = pace_target.FITTED_LINE):
        return pace_target.LeaveOneOut(
            session_id, start, route, 5500.0 * (1 + shift), shift, route == pace_target.FITTED_LINE
        )

    tied_later, tied_earlier = row("later", later, -0.04), row("earlier", earlier, 0.04)
    smaller = row("small", FIRST_START, 0.039)
    uncountable = row("fallback", FIRST_START, 0.5, pace_target.CLOSEST_RACE)
    assert pace_target._largest([tied_later, smaller, tied_earlier, uncountable]) is tied_earlier
    assert pace_target._largest([tied_earlier, uncountable, smaller, tied_later]) is tied_earlier
    assert pace_target._largest([uncountable]) is None
    assert pace_target._largest([]) is None


def test_the_module_is_pure() -> None:
    path = Path(pace_target.__file__)
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
    assert names, "the walk saw no imports"
    forbidden = {"sqlite3", "fastapi", "runcoach_api.db", "runcoach_api.main", "runcoach_api.config"}
    assert not {n for n in names if n in forbidden or n.split(".")[0] in {"sqlite3", "fastapi"}}, names
    assert not {
        n for n in names if n.startswith(".") and n.strip(".").split(".")[0] in {"db", "main", "config"}
    }, names
    assert "runcoach_api.metrics.riegel_b" in names or "runcoach_api.metrics" in names
    assert isinstance(pace_target.PARAMETERS, type(riegel_b.PARAMETERS))
    with pytest.raises(TypeError):
        pace_target.PARAMETERS["tie_slack"] = 0  # type: ignore[index]
    assert pace_target.PARAMETERS == {
        "high_max_uncertainty": 0.02,
        "medium_max_uncertainty": 0.05,
        "fitness_gap": 0.30,
        "max_extrapolation": 1.5,
        "calibrated_min_m": 5000,
        "calibrated_max_m": 42195,
        "population_b": riegel_b.POPULATION_B,
        "tie_slack": riegel_b.COUNT_SLACK,
    }
    assert pace_target.POPULATION_B is riegel_b.POPULATION_B and pace_target.TIE_SLACK == riegel_b.COUNT_SLACK
    for name, value in {
        "FITTED_LINE": "fitted_line",
        "CLOSEST_RACE": "closest_race",
        "NO_RACES": "no_races",
        "RIEGEL_UNAVAILABLE": "riegel_unavailable",
        "PROJECTION_NOT_FINITE": "projection_not_finite",
        "HIGH": "high",
        "MEDIUM": "medium",
        "LOW": "low",
        "RANGE": "range",
        "ONE_RACE": "one_race",
        "FITNESS_BELOW_RACES": "fitness_below_races",
        "POPULATION_DEFAULT": "population_default",
        "BELOW_ONE": "below_one",
        "OUTSIDE_CALIBRATED_RANGE": "outside_calibrated_range",
        "BEYOND_LONGEST_RACE": "beyond_longest_race",
    }.items():
        assert getattr(pace_target, name) == value, name
    served = _project(E2, HALF)
    with pytest.raises(AttributeError):
        served.time_s = 0.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("races", "distance_m"),
    [
        pytest.param(E2, 0.0, id="distance-zero"),
        pytest.param(E2, -5000.0, id="distance-negative"),
        pytest.param(E2, math.nan, id="distance-nan"),
        pytest.param(E2, math.inf, id="distance-inf"),
        pytest.param(E2, 1e308, id="distance-1e308"),
        pytest.param([(21580.0, 5928.0, 1.0)], 0.0, id="closest-distance-zero"),
        pytest.param([(21580.0, 5928.0, 1.0)], math.nan, id="closest-distance-nan"),
        pytest.param([(21580.0, 5928.0, 1.0)], math.inf, id="closest-distance-inf"),
        pytest.param([(21580.0, 5928.0, 1.0)], 1e308, id="closest-distance-1e308"),
        pytest.param([(0.0, 5928.0, 1.0)], HALF, id="race-d-eq-zero"),
        pytest.param([(math.inf, 5928.0, 1.0)], HALF, id="race-d-eq-inf"),
        pytest.param([(0.0, 1200.0, 1.0), *E2[1:]], HALF, id="race-d-eq-zero-among-four"),
        pytest.param([(math.inf, 1200.0, 1.0), *E2[1:]], HALF, id="race-d-eq-inf-among-four"),
        pytest.param([(5000.0, math.nan, 1.0), *E2[1:]], HALF, id="race-time-nan"),
        pytest.param([(5000.0, 0.0, 1.0), *E2[1:]], HALF, id="race-time-zero"),
        pytest.param([], HALF, id="no-races"),
        pytest.param([], math.nan, id="no-races-nan-distance"),
    ],
)
def test_an_adversarial_input_never_raises(races, distance_m) -> None:
    """Every degenerate input lands on a code (section 3.3), none raises and no served figure is
    non-finite; a 1e308 m goal is ``projection_not_finite`` (its time overflows ``exp``)."""
    served = project(_races(races), _estimate(), distance_m)
    assert served.unavailable in {pace_target.NO_RACES, pace_target.PROJECTION_NOT_FINITE}
    if not races:
        assert served.unavailable == pace_target.NO_RACES
    else:
        assert served.unavailable == pace_target.PROJECTION_NOT_FINITE
    assert served.route is None and served.time_s is None and served.leave_one_out is None
    assert served.distance_m is distance_m or served.distance_m == distance_m or math.isnan(distance_m)
