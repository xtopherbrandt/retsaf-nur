"""``metrics.pace_target``'s label, fitness context, goal-time gap and assumptions (F032 feature AC5,
AC6's pure part and AC7; reference sections 4.2, 5 and 6).

Every row of the reference section 4.2 table that is built from section 8's sets is pinned with its
served label, basis, uncertainty and reasons, in order; the uncertainty is checked against
``support/pace_target_oracle.py`` (the halfwidth and the largest countable shift restated in raw
sums) and against the table's printed percentages. Three perturbations of the label go red against
these rows: reading b's label instead of ``u`` (E1 reads ``high``), ignoring the shift (E4's marathon
reads ``medium``) and counting closest-race shifts (case A's half reads ``medium``).
"""

from __future__ import annotations

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
PERCENT = 5e-6  # the table prints three decimals of a percentage

HALF = 21097.5
MARATHON = 42195.0

# F032 reference section 8, as (d_eq_m, time_s, weight).
E1 = [(5000.0, 1200.0, 1.0), (10000.0, 2526.937004, 1.0), (HALF, 5465.036808, 1.0)]
E2 = [(5000.0, 1200.0, 1.0), (10000.0, 2502.0, 1.0), (HALF, 5530.0, 1.0), (MARATHON, 11610.0, 1.0)]
E4 = [
    (5000.0, 1200.0, 1.0),
    (5000.0, 1203.0, 1.0),
    (10000.0, 2540.0, 1.0),
    (10000.0, 2545.0, 1.0),
    (MARATHON, 11300.0, 1.0),
]
A = [(5000.0, 1200.0, 1.0), (10000.0, 2574.828566, 1.0), (HALF, 5841.627023, 1.0)]
ONE = [(21580.0, 5928.0, 1.0)]

FIRST_START = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _races(triples, starts=None, fitness=None) -> list[Race]:
    out = []
    for index, (d_eq, time_s, weight) in enumerate(triples):
        start = starts[index] if starts is not None else FIRST_START + timedelta(days=index)
        out.append(
            Race(
                session_id=f"s{index}",
                start_time=start,
                d_eq_m=d_eq,
                time_s=time_s,
                weight=weight,
                fitness_before=None if fitness is None else fitness[index],
            )
        )
    return out


def _estimate(unavailable: str | None = None) -> riegel_b.RiegelEstimate:
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


def _project(triples, distance_m: float, **kwargs) -> Projection:
    return project(_races(triples), _estimate(), distance_m, **kwargs)


def _fitness(races: list[Race], now: float | None) -> pace_target.Fitness:
    return pace_target.fitness_context(races, now)


def _label(triples, distance_m: float, *, below_one: bool = False, fitness=None) -> pace_target.Confidence:
    races = _races(triples)
    served = pace_target.label(
        project(races, _estimate(), distance_m), races, below_one=below_one, fitness=fitness
    )
    assert served is not None
    return served


def _oracle_u(triples, distance_m: float) -> float:
    p = oracle.projection(triples, distance_m)
    shift = oracle.largest_countable_shift(oracle.leave_one_out(triples, distance_m))
    return p.halfwidth if shift is None else max(p.halfwidth, shift)


# The section 4.2 table's rows built from section 8's sets: label, basis, u (as printed, a fraction),
# reasons, and the fitness_now that makes the fitness row (None elsewhere).
TABLE = [
    pytest.param("E2", HALF, "high", "range", 0.00544, (), None, id="E2-half"),
    pytest.param(
        "E2", HALF, "medium", "range", 0.00544, ("fitness_below_races",), 0.65, id="E2-half-fitness-35-below"
    ),
    pytest.param("E4", HALF, "medium", "one_race", 0.03633, (), None, id="E4-half"),
    pytest.param("E4", MARATHON, "low", "one_race", 0.05866, (), None, id="E4-marathon"),
    pytest.param("A", HALF, "high", "range", 0.00507, (), None, id="A-half"),
    pytest.param("E1", HALF, "low", "range", 0.05177, (), None, id="E1-half"),
    pytest.param("E1", MARATHON, "low", "range", 0.06964, ("beyond_longest_race",), None, id="E1-marathon"),
    pytest.param("E2", 60000.0, "low", "range", 0.00681, ("outside_calibrated_range",), None, id="E2-60km"),
]
SETS = {"E1": E1, "E2": E2, "E4": E4, "A": A}


@pytest.mark.parametrize(("name", "distance_m", "label", "basis", "u", "reasons", "fitness_ratio"), TABLE)
def test_label_table_rows(name, distance_m, label, basis, u, reasons, fitness_ratio) -> None:
    triples = SETS[name]
    races = _races(triples, fitness=[50.0] * len(triples))
    fitness = None if fitness_ratio is None else _fitness(races, fitness_ratio * 50.0)
    if fitness is not None:
        assert fitness.fitness_at_races == 50.0 and fitness.gap_holds is True
    projection = project(races, _estimate(), distance_m)
    assert projection.route == pace_target.FITTED_LINE
    served = pace_target.label(projection, races, below_one=False, fitness=fitness)
    assert served is not None

    assert served.label == label
    assert served.basis == basis
    assert served.uncertainty == pytest.approx(u, abs=PERCENT)
    assert served.uncertainty == pytest.approx(_oracle_u(triples, distance_m), abs=ORACLE)
    assert served.reasons == reasons
    assert isinstance(served.reasons, tuple)
    # u is the halfwidth or the largest countable shift, whichever is larger, and basis says which.
    largest = projection.largest
    if largest is None or projection.halfwidth >= abs(largest.shift):
        assert served.basis == pace_target.RANGE and served.uncertainty == projection.halfwidth
    else:
        assert served.basis == pace_target.ONE_RACE and served.uncertainty == abs(largest.shift)


def test_label_table_fewer_than_three_races() -> None:
    """The table's last row: route ``closest_race`` is ``low`` with ``population_default``, basis
    and uncertainty null, and a fitness gap is listed though the label is already ``low``."""
    served = _label(ONE, HALF)
    assert served.label == pace_target.LOW
    assert served.basis is None and served.uncertainty is None
    assert served.reasons == (pace_target.POPULATION_DEFAULT,)

    races = _races(ONE, fitness=[50.0])
    gap = _fitness(races, 30.0)
    with_gap = pace_target.label(project(races, _estimate(), HALF), races, below_one=False, fitness=gap)
    assert with_gap is not None
    assert with_gap.label == pace_target.LOW
    assert with_gap.reasons == (pace_target.FITNESS_BELOW_RACES, pace_target.POPULATION_DEFAULT)

    # Every rule-3 reason at once, in the reference's order.
    far = pace_target.label(project(races, _estimate(), 60000.0), races, below_one=True, fitness=gap)
    assert far is not None and far.reasons == (
        pace_target.FITNESS_BELOW_RACES,
        pace_target.POPULATION_DEFAULT,
        pace_target.BELOW_ONE,
        pace_target.OUTSIDE_CALIBRATED_RANGE,
        pace_target.BEYOND_LONGEST_RACE,
    )

    # Unavailable: no label.
    assert pace_target.label(project([], _estimate(), HALF), [], below_one=False) is None
    e2 = _races(E2)
    assert (
        pace_target.label(project(e2, _estimate(riegel_b.ESTIMATE_NOT_FINITE), HALF), e2, below_one=False)
        is None
    )


def _fitted(
    distance_m: float, halfwidth: float, largest: pace_target.LeaveOneOut | None = None
) -> Projection:
    """A fitted-line projection with just the figures the label reads."""
    time_s = 5500.0
    return Projection(
        distance_m=distance_m,
        route=pace_target.FITTED_LINE,
        time_s=time_s,
        interval_low=time_s / (1 + halfwidth),
        interval_high=time_s * (1 + halfwidth),
        halfwidth=halfwidth,
        pace_s_per_km=time_s / (distance_m / 1000),
        pace_low=None,
        pace_high=None,
        t_multiplier=1.6,
        provisional=False,
        closest=None,
        leave_one_out=(),
        largest=largest,
        unavailable=None,
    )


def _row(shift: float, countable: bool = True) -> pace_target.LeaveOneOut:
    route = pace_target.FITTED_LINE if countable else pace_target.CLOSEST_RACE
    return pace_target.LeaveOneOut("s9", FIRST_START, route, 5500.0 * (1 + shift), shift, countable)


def test_label_edges() -> None:
    races = _races(E2)  # D_max 42195

    def at(u: float, *, shift: float | None = None, distance_m: float = HALF, below_one: bool = False):
        largest = None if shift is None else _row(shift)
        served = pace_target.label(_fitted(distance_m, u, largest), races, below_one=below_one)
        assert served is not None
        return served

    # u at the thresholds: 0.02 high, just above medium; 0.05 medium, just above low.
    assert at(0.02).label == pace_target.HIGH
    assert at(math.nextafter(0.02, 1)).label == pace_target.MEDIUM
    assert at(0.05).label == pace_target.MEDIUM
    assert at(math.nextafter(0.05, 1)).label == pace_target.LOW
    assert at(0.02).reasons == () and at(math.nextafter(0.05, 1)).reasons == ()
    # The same thresholds reached through the shift.
    assert at(0.01, shift=-0.02).label == pace_target.HIGH
    assert at(0.01, shift=-0.02).basis == pace_target.ONE_RACE
    assert at(0.01, shift=-math.nextafter(0.02, 1)).label == pace_target.MEDIUM
    assert at(0.01, shift=math.nextafter(0.05, 1)).label == pace_target.LOW
    assert at(0.01, shift=0.01).basis == pace_target.RANGE  # equal: the range
    assert at(0.01, shift=0.01).uncertainty == 0.01
    assert at(0.03, shift=-0.04).uncertainty == 0.04
    # A closest-race row is not countable and is not read.
    far = pace_target.label(_fitted(HALF, 0.01, _row(0.5, countable=False)), races, below_one=False)
    assert far is not None and far.label == pace_target.HIGH and far.uncertainty == 0.01

    # The calibrated range, bounds inclusive.
    for distance_m in (5000.0, 42195.0):
        assert at(0.01, distance_m=distance_m).reasons == ()
        assert at(0.01, distance_m=distance_m).label == pace_target.HIGH
    for distance_m in (4999.0, 42196.0):
        served = at(0.01, distance_m=distance_m)
        assert served.label == pace_target.LOW
        assert served.reasons == (pace_target.OUTSIDE_CALIBRATED_RANGE,)
        assert served.uncertainty == 0.01 and served.basis == pace_target.RANGE  # u is still served

    # 1.5 x the longest race used is inside; just above it is beyond_longest_race.
    shorter = _races(E1)  # D_max 21097.5
    edge = pace_target.MAX_EXTRAPOLATION * HALF
    assert edge == 31646.25
    inside = pace_target.label(_fitted(edge, 0.01), shorter, below_one=False)
    assert inside is not None and inside.reasons == () and inside.label == pace_target.HIGH
    slack = pace_target.label(_fitted(edge * (1 + 1e-9), 0.01), shorter, below_one=False)
    assert slack is not None and slack.reasons == ()
    beyond = pace_target.label(_fitted(edge * (1 + 1e-8), 0.01), shorter, below_one=False)
    assert beyond is not None and beyond.label == pace_target.LOW
    assert beyond.reasons == (pace_target.BEYOND_LONGEST_RACE,)
    # D_max is the largest d_eq of the races given, whatever their order.
    reordered = list(reversed(shorter))
    assert pace_target.label(_fitted(edge, 0.01), reordered, below_one=False).reasons == ()

    # F029 below_one serves low, listed, u still served.
    below = at(0.01, below_one=True)
    assert below.label == pace_target.LOW and below.reasons == (pace_target.BELOW_ONE,)
    assert below.uncertainty == 0.01


def test_fitness_figures() -> None:
    starts = [FIRST_START + timedelta(days=d) for d in (10, 300, 40, 120)]
    races = _races(
        [(5000.0, 1200.0, 1.0), (10000.0, 2502.0, 0.5), (HALF, 5530.0, 1.0), (MARATHON, 11610.0, 0.25)],
        starts,
        fitness=[40.0, 60.0, None, 80.0],
    )
    served = pace_target.fitness_context(races, 44.0)
    # Weighted by F029 weight over the races with a figure: (40 + 30 + 20) / 1.75.
    assert served.fitness_at_races == pytest.approx((40.0 * 1.0 + 60.0 * 0.5 + 80.0 * 0.25) / 1.75, abs=1e-12)
    assert served.fitness_now == 44.0
    assert served.gap_holds is False
    # The newest race is the latest start, not the last in the list.
    assert served.newest_race is races[1] and served.newest_race.start_time == starts[1]

    # Every figure null: fitness_at_races null, nothing held, fitness_now served as given.
    none = pace_target.fitness_context(_races(E2), 44.0)
    assert none.fitness_at_races is None and none.fitness_now == 44.0 and none.gap_holds is False
    assert none.newest_race is _races(E2)[3] or none.newest_race.session_id == "s3"
    # fitness_now null: served null, nothing held.
    missing = pace_target.fitness_context(_races(E2, fitness=[50.0] * 4), None)
    assert missing.fitness_at_races == 50.0 and missing.fitness_now is None and missing.gap_holds is False
    # No race: everything null.
    empty = pace_target.fitness_context([], 44.0)
    assert empty.fitness_at_races is None and empty.newest_race is None and empty.gap_holds is False
    assert empty.fitness_now == 44.0


def test_fitness_gap_lowers_one_step() -> None:
    races = _races(E2, fitness=[50.0] * 4)
    projection = project(races, _estimate(), HALF)
    assert pace_target.FITNESS_GAP == 0.30

    def labelled(now: float | None):
        fitness = pace_target.fitness_context(races, now)
        served = pace_target.label(projection, races, below_one=False, fitness=fitness)
        assert served is not None
        return fitness, served

    without = pace_target.label(projection, races, below_one=False)
    assert without is not None and without.label == pace_target.HIGH

    # At exactly 0.70 the gap does not hold; a ratio of 0.6999 lowers one step.
    fitness, served = labelled(0.70 * 50.0)
    assert fitness.fitness_now == 35.0 and fitness.gap_holds is False
    assert served.label == pace_target.HIGH and served.reasons == ()
    fitness, served = labelled(0.6999 * 50.0)
    assert fitness.gap_holds is True
    assert served.label == pace_target.MEDIUM and served.reasons == (pace_target.FITNESS_BELOW_RACES,)
    assert served.uncertainty == without.uncertainty and served.basis == without.basis
    # Either figure null: not lowered.
    assert labelled(None)[1].label == pace_target.HIGH
    no_figures = pace_target.label(projection, _races(E2), below_one=False, fitness=_fitness(_races(E2), 1.0))
    assert no_figures is not None and no_figures.label == pace_target.HIGH and no_figures.reasons == ()
    # A fitness context whose gap holds is read by its own flag, whatever the races passed.
    held = pace_target.Fitness(fitness_at_races=50.0, fitness_now=10.0, gap_holds=True, newest_race=None)
    assert pace_target.label(projection, races, below_one=False, fitness=held).label == pace_target.MEDIUM

    # One step from each label: medium to low, low stays low.
    e4 = _races(E4, fitness=[50.0] * 5)
    gap = pace_target.fitness_context(e4, 30.0)
    medium = pace_target.label(project(e4, _estimate(), HALF), e4, below_one=False, fitness=gap)
    assert medium is not None and medium.label == pace_target.LOW
    assert medium.reasons == (pace_target.FITNESS_BELOW_RACES,)
    low = pace_target.label(project(e4, _estimate(), MARATHON), e4, below_one=False, fitness=gap)
    assert (
        low is not None and low.label == pace_target.LOW and low.reasons == (pace_target.FITNESS_BELOW_RACES,)
    )


def test_time_is_unchanged_by_the_fitness_gap() -> None:
    """The gap lowers the label and never touches a figure: the projection is the same object with
    and without it, and the label's uncertainty and basis are unchanged."""
    with_figures = _races(E2, fitness=[50.0] * 4)
    plain = _races(E2)
    same = project(with_figures, _estimate(), HALF)
    other = project(plain, _estimate(), HALF)
    assert same == other
    assert same.time_s == pytest.approx(5542.806803, abs=TIME)
    gap = pace_target.fitness_context(with_figures, 30.0)
    assert gap.gap_holds is True
    lowered = pace_target.label(same, with_figures, below_one=False, fitness=gap)
    kept = pace_target.label(same, with_figures, below_one=False)
    assert lowered is not None and kept is not None
    assert lowered.label == pace_target.MEDIUM and kept.label == pace_target.HIGH
    assert lowered.uncertainty == kept.uncertainty and lowered.basis == kept.basis
    assert same.time_s == other.time_s and same.halfwidth == other.halfwidth
    # The gap figures are served beside the projection, not applied to it.
    assert pace_target.goal_time_gap(same, 5500.0) == pace_target.goal_time_gap(other, 5500.0)


def test_goal_time_gap() -> None:
    half = _project(E2, HALF)
    served = pace_target.goal_time_gap(half, 5500.0)
    assert served is not None
    expected = oracle.goal_gap(oracle.projection(E2, HALF), 5500.0)
    assert served.gap_s == pytest.approx(-42.806803, abs=TIME)
    assert served.gap_fraction == pytest.approx(-0.007723, abs=FRACTION)
    assert served.within_range is False
    assert served.gap_s == pytest.approx(expected.gap_s, abs=ORACLE)
    assert served.gap_fraction == pytest.approx(expected.gap_fraction, abs=ORACLE)
    assert served.gap_s == pytest.approx(5500.0 - half.time_s, abs=1e-12)
    assert served.gap_fraction == pytest.approx(served.gap_s / half.time_s, abs=1e-15)

    inside = pace_target.goal_time_gap(half, 5530.0)
    assert inside is not None and inside.within_range is True
    assert inside.gap_s == pytest.approx(5530.0 - half.time_s, abs=1e-12)
    # The interval's ends are inside; just beyond them is not.
    assert pace_target.goal_time_gap(half, half.interval_low).within_range is True
    assert pace_target.goal_time_gap(half, half.interval_high).within_range is True
    assert pace_target.goal_time_gap(half, math.nextafter(half.interval_low, 0)).within_range is False
    assert pace_target.goal_time_gap(half, math.nextafter(half.interval_high, math.inf)).within_range is False

    # No goal time: null. Unavailable: null.
    assert pace_target.goal_time_gap(half, None) is None
    assert pace_target.goal_time_gap(project([], _estimate(), HALF), 5500.0) is None
    assert pace_target.goal_time_gap(_project(E2, 1e308), 5500.0) is None

    # Route closest_race: gap_s and gap_fraction served, within_range null.
    fallback = _project(ONE, HALF)
    one = pace_target.goal_time_gap(fallback, 5800.0)
    assert one is not None and one.within_range is None
    assert one.gap_s == pytest.approx(5800.0 - 5787.600207, abs=TIME)
    assert one.gap_fraction == pytest.approx(one.gap_s / fallback.time_s, abs=1e-15)

    # Section 3.3: a gap that is not finite makes the projection projection_not_finite.
    for target in (math.inf, math.nan, -math.inf):
        screened = _project(E2, HALF, target_time_s=target)
        assert screened.unavailable == pace_target.PROJECTION_NOT_FINITE
        assert screened.time_s is None
        assert pace_target.goal_time_gap(screened, target) is None
    assert _project(E2, HALF, target_time_s=5500.0) == half
    assert _project(E2, HALF, target_time_s=None) == half


def test_assumptions_in_order() -> None:
    expected = (
        "flat_course",
        "still_air",
        "cool_conditions",
        "no_economy_adjustment",
        "no_durability_adjustment",
        "projected_for_today_not_race_day",
        "watch_timer_time",
    )
    assert pace_target.assumptions() == expected
    assert pace_target.ASSUMPTIONS == expected
    assert isinstance(pace_target.assumptions(), tuple)
    assert len(set(expected)) == 7


def test_race_from_effort_carries_the_fitness_figure() -> None:
    effort = riegel_b.Effort(
        session_id="race-1",
        start_time=FIRST_START,
        duration_s=5530.0,
        distance_m=HALF,
        gap_pace_s_per_km=262.1,
        gap_equivalent_m=HALF,
        gap_coverage=0.99,
        gps_degraded_fraction=0.0,
        weight=0.8,
        fitness_before=47.5,
        fitness_earlier=45.0,
        fitness_drop=None,
        fitness_provisional=False,
        fitness_after_gap=False,
        residual=0.001,
    )
    race = Race.from_effort(effort)
    assert race.fitness_before == 47.5 and race.weight == 0.8 and race.d_eq_m == HALF
    # The figure is optional: a race built without it has none.
    assert Race("s", FIRST_START, HALF, 5530.0, 1.0).fitness_before is None
