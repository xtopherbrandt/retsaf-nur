"""The Riegel-b estimator (F029 reference sections 1 to 4): the screen, the bar, the fade, the fit
and its Student's t interval, driven with hand-built rows and no database.

Expected values come from two places that share no code with ``metrics.riegel_b``:

- **The oracle** (``tests/support/riegel_oracle.py``): weighted least squares by the 2x2 normal
  equations in raw sums, and Student's t quantiles from the regularized incomplete beta function
  by its continued fraction (Numerical Recipes, 3rd ed., section 6.4), solved by bisection.
  ``test_the_oracle_imports_nothing_from_the_estimator`` parses its imports to pin that.
- **The reference's section 4 table and section 1 t values**, typed in by hand at their printed
  precision (6 decimals for b, the error, t and the interval; 0.001 percentage points for a
  residual).

The estimator is compared with the oracle to 1e-9 and with the printed figures at their precision.
"""

from __future__ import annotations

import ast
import importlib.util
import math
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ORACLE_PATH = Path(__file__).parent / "support" / "riegel_oracle.py"
ESTIMATOR_PATH = Path(__file__).parents[1] / "src" / "runcoach_api" / "metrics" / "riegel_b.py"

# ``tests/`` is not a package (importlib mode), so the oracle is loaded from its path.
_SPEC = importlib.util.spec_from_file_location("riegel_oracle", ORACLE_PATH)
riegel_oracle = importlib.util.module_from_spec(_SPEC)
sys.modules["riegel_oracle"] = riegel_oracle
_SPEC.loader.exec_module(riegel_oracle)  # type: ignore[union-attr]

from runcoach_api.metrics import riegel_b
from runcoach_api.metrics.riegel_b import estimate, needs_features

ORACLE_TOLERANCE = 1e-9
PRINTED_TOLERANCE = 5e-7 + 1e-12  # half a unit in the 6th decimal
RESIDUAL_TOLERANCE = 5e-6 + 1e-12  # half of 0.001 percentage points, as a fraction

ZONE = ZoneInfo("Europe/Berlin")
INSTANT = datetime(2026, 10, 24, 12, 0, tzinfo=ZONE)
REFERENCE_T = (
    3.077684,
    1.885618,
    1.637744,
    1.533206,
    1.475884,
    1.439756,
    1.414924,
    1.396815,
    1.383029,
    1.372184,
    1.363430,
    1.356217,
    1.350171,
    1.345030,
    1.340606,
    1.336757,
    1.333379,
    1.330391,
    1.327728,
    1.325341,
    1.323188,
    1.321237,
    1.319460,
    1.317836,
    1.316345,
    1.314972,
    1.313703,
    1.312527,
    1.311434,
    1.310415,
)
"""Reference section 1's t at 0.90 for 1 to 30 degrees of freedom, typed in by hand."""


def _race(
    session_id: str,
    d_eq: float,
    time_s: float,
    age_days: float = 100.0,
    fitness: tuple | None = None,
    pace: object = None,
    **over: object,
) -> dict:
    """One race-tagged running row ``age_days`` before ``INSTANT`` whose F013 GAP pace gives
    ``d_eq``; ``fitness`` is its ``(ctl_before, ctl_earlier, provisional, after_gap)``."""
    row = {
        "session_id": session_id,
        "start_time": (INSTANT.astimezone(UTC) - timedelta(days=age_days)).isoformat(),
        "sport": "running",
        "activity_tag": "race",
        "timer_time_s": time_s,
        "duration_s": None,
        "summary": f'{{"distance_m": {d_eq!r}}}',
        "quality_flags": "[]",
        "admitted_as_workout": False,
        "features": {
            "gap_avg_pace_s_per_km": 1000 * time_s / d_eq if pace is None else pace,
            "gap_coverage": 1.0,
            "gps_degraded_fraction": 0.0,
            "flags": [],
        },
        "_fitness": fitness,
    }
    row.update(over)
    return row


def _fitness(rows: list[dict]) -> dict:
    """The fitness mapping keyed by each row's local race date in ``ZONE``."""
    return {
        datetime.fromisoformat(row["start_time"]).astimezone(ZONE).date(): row["_fitness"]
        for row in rows
        if row.get("_fitness") is not None
    }


def _run(rows: list[dict], instant: datetime | date = INSTANT, **kw: object) -> riegel_b.RiegelEstimate:
    return estimate(rows, instant, _fitness(rows), **kw)


def _abcd(d_weight_age: float = 100.0, screened: bool = False) -> list[dict]:
    fit = (lambda f: f) if screened else (lambda f: None)
    return [
        _race("a", 5000.0, 1200.0, 400, fit((36, 37, False, False))),
        _race("b", 10000.0, 2520.0, 300, fit((40, 41, False, False))),
        _race("c", 21100.0, 5600.0, 200, fit((42, 40, False, False))),
        _race("d", 42200.0, 12000.0, d_weight_age, fit((29, 50, False, False))),
    ]


def _assert_fit(result: riegel_b.RiegelEstimate, name: str, order: str) -> None:
    """``order`` names the session ids in the table's race order (efforts are in start order)."""
    races, printed = riegel_oracle.WORKED_EXAMPLES[name]
    oracle = riegel_oracle.fit(races)
    got = (result.value, result.error, result.t_multiplier, result.interval_low, result.interval_high)
    want = (oracle.b, oracle.error, oracle.t, oracle.low, oracle.high)
    for g, o, p in zip(got, want, printed[:5], strict=True):
        assert abs(g - o) <= ORACLE_TOLERANCE, (name, g, o)
        assert abs(g - p) <= PRINTED_TOLERANCE, (name, g, p)
    by_id = {effort.session_id: effort.residual for effort in result.efforts}
    assert sorted(by_id) == sorted(order)
    residuals = [by_id[session_id] for session_id in order]
    assert len(residuals) == len(oracle.residuals) == len(printed) - 5
    for g, o, p in zip(residuals, oracle.residuals, printed[5:], strict=True):
        assert abs(g - o) <= ORACLE_TOLERANCE, (name, g, o)
        assert abs(g - p / 100) <= RESIDUAL_TOLERANCE, (name, g, p)
    assert result.unavailable is None
    assert result.population_default is False and result.provisional is False
    assert result.interval_low <= result.value <= result.interval_high


def test_worked_examples_match_the_reference() -> None:
    unscreened = _run(_abcd())
    _assert_fit(unscreened, "A, B, C, D", "abcd")
    assert unscreened.confidence == "high" and unscreened.excluded == ()

    screened = _run(_abcd(screened=True))
    _assert_fit(screened, "A, B, C", "abc")
    assert screened.confidence == "high"
    assert [e.session_id for e in screened.efforts] == ["a", "b", "c"]
    (dropped,) = screened.excluded
    assert (dropped.session_id, dropped.reason) == ("d", "fitness_dropped")
    assert (dropped.fitness_before, dropped.fitness_earlier) == (29, 50)
    assert dropped.fitness_drop == pytest.approx(0.42, abs=1e-12)
    c = screened.efforts[2]
    assert (c.fitness_before, c.fitness_earlier, c.fitness_provisional, c.fitness_after_gap) == (
        42,
        40,
        False,
        False,
    )
    assert c.fitness_drop == pytest.approx(1 - 42 / 40, abs=1e-12)

    faded = _run(_abcd(d_weight_age=1004))
    _assert_fit(faded, "A, B, C, D at 0.5", "abcd")
    assert {e.session_id: e.weight for e in faded.efforts} == {"a": 1.0, "b": 1.0, "c": 1.0, "d": 0.5}
    assert faded.confidence == "high"


def test_two_races_serve_the_population() -> None:
    result = _run(_abcd()[:2])
    assert (result.value, result.error, result.t_multiplier) == (1.06, 0.05, riegel_b.Z)
    low, high = riegel_oracle.POPULATION_INTERVAL
    assert (
        abs(result.interval_low - low) <= PRINTED_TOLERANCE
        and abs(result.interval_high - high) <= PRINTED_TOLERANCE
    )
    assert abs(result.interval_low - (1.06 - riegel_oracle.Z * 0.05)) <= ORACLE_TOLERANCE
    assert result.confidence == "low"
    assert result.provisional is True and result.population_default is True and result.below_one is False
    assert result.unavailable is None
    assert [(e.session_id, e.residual) for e in result.efforts] == [("a", None), ("b", None)]


def test_three_5ks_serve_the_population() -> None:
    rows = [_race(f"5k{i}", 5000.0 + 20 * i, 1200.0 + 5 * i, 100 + 30 * i) for i in range(3)]
    result = _run(rows)
    assert result.population_default is True and result.value == 1.06 and result.confidence == "low"
    assert len(result.efforts) == 3 and all(e.residual is None for e in result.efforts)


def test_below_one_is_served_as_fitted() -> None:
    rows = [
        _race("a", 5000.0, 1200.0, 300),
        _race("h", 10000.0, 2350.0, 200),
        _race("f", 21100.0, 4700.0, 100),
    ]
    result = _run(rows)
    _assert_fit(result, "A, H, F", "ahf")
    assert result.confidence == "high" and result.below_one is True
    assert abs(result.value - 0.947937) <= PRINTED_TOLERANCE


def _edge(third: dict | None = None, long_time: float = 2520.0) -> list[dict]:
    """Paces 240 and 252 s/km: ``D_eq`` 5000.0 and 10000.0 exactly, beside a 7000.0 race."""
    rows = [
        _race("short", 5000.0, 1200.0, 300, pace=240.0),
        _race("long", 10000.0, long_time, 200, pace=252.0),
    ]
    return rows + ([third] if third is not None else [])


def _mid(**over: object) -> dict:
    over.setdefault("pace", 240.0)
    return _race("mid", 7000.0, 1680.0, **{"age_days": 100, **over})


def test_bar_edges_at_a_ratio_of_two() -> None:
    at_two = _run(_edge(_mid()))
    served = [e.gap_equivalent_m for e in at_two.efforts]
    assert served == [5000.0, 10000.0, 7000.0]
    assert max(served) / min(served) == 2.0
    assert at_two.population_default is False and at_two.value is not None and at_two.unavailable is None

    below = _run(_edge(_mid(), long_time=2518.74))
    assert max(e.gap_equivalent_m for e in below.efforts) / 5000.0 == pytest.approx(1.999, abs=1e-12)
    assert below.population_default is True and below.value == 1.06

    assert _run(_edge()).population_default is True

    screens = {
        "admitted_as_workout": {"admitted_as_workout": True},
        "outside_window": {"age_days": 1095},
        "duration_unavailable": {"timer_time_s": float("nan")},
        "smart_recording": {"quality_flags": '["smart_recording"]'},
        "fitness_dropped": {"fitness": (29, 50, False, False)},
        "features_unavailable": {"features": None},
        "grade_clamped": {"features": {"gap_avg_pace_s_per_km": 240.0, "flags": ["grade_clamped"]}},
        "gap_pace_unavailable": {"pace": 0},
    }
    for reason, over in screens.items():
        result = _run(_edge(_mid(**over)))
        assert [e.reason for e in result.excluded] == [reason], reason
        assert result.population_default is True, reason

    # The bar counts races by their weight: at 1095.00 days the third is out by row 2; at 1094.99
    # it is used at a weight under 1e-4, and the effective count (2 + w)^2 / (2 + w^2) is under 3.
    assert _run(_edge(_mid(age_days=1095.0))).population_default is True
    barely = _run(_edge(_mid(age_days=1094.99)))
    assert 0 < {e.session_id: e.weight for e in barely.efforts}["mid"] < 1e-4
    assert barely.population_default is True and barely.value == 1.06 and barely.confidence == "low"
    assert [e.residual for e in barely.efforts] == [None, None, None]


def test_weight_fades_from_913_to_1095_days() -> None:
    rows = [
        _race("913", 5000.0, 1200.0, 913),
        _race("1004", 10000.0, 2520.0, 1004),
        _race("1094.99", 21100.0, 5600.0, 1094.99),
        _race("1095.00", 42200.0, 12000.0, 1095.0),
    ]
    result = _run(rows)
    weights = {e.session_id: e.weight for e in result.efforts}
    assert weights["913"] == 1.0
    assert weights["1004"] == 0.5
    assert weights["1094.99"] == pytest.approx(riegel_oracle.weight(1094.99), abs=1e-9)
    assert weights["1094.99"] > 0
    assert [(e.session_id, e.reason) for e in result.excluded] == [("1095.00", "outside_window")]
    assert riegel_oracle.weight(913) == 1.0 and riegel_oracle.weight(1004) == 0.5

    # ``as_of`` as a date is the last instant of that local day in the zone.
    day = date(2026, 10, 24)
    last = datetime.combine(day, time.max, tzinfo=ZONE)
    edge = [
        _race(
            "in",
            5000.0,
            1200.0,
            0,
            start_time=(last.astimezone(UTC) - timedelta(days=1095) + timedelta(microseconds=1)).isoformat(),
        ),
        _race("out", 5000.0, 1200.0, 0, start_time=(last.astimezone(UTC) - timedelta(days=1095)).isoformat()),
    ]
    by_date = estimate(edge, day, {}, zone=ZONE)
    assert by_date.as_of == last
    assert [e.session_id for e in by_date.efforts] == ["in"]
    assert [(e.session_id, e.reason) for e in by_date.excluded] == [("out", "outside_window")]
    with pytest.raises(ValueError):
        estimate(edge, day, {})


_RANK = {"low": 0, "medium": 1, "high": 2}


def _age(weight: float) -> float:
    """The age in days at which a race's weight is ``weight`` (inside the fade)."""
    return riegel_oracle.WINDOW_DAYS - riegel_oracle.FADE_DAYS * weight


def _power_law_races() -> dict[str, tuple[float, float]]:
    """A 5K, a 10K 1 % slow of a b = 1.06 line from it, a half 10 % slow and a marathon 1 % fast."""
    five = 1200.0
    return {
        "5k": (5000.0, five),
        "10k": (10000.0, five * 2**1.06 * 1.01),
        "half": (21100.0, five * (21100 / 5000) ** 1.06 * 1.10),
        "marathon": (42200.0, five * (42200 / 5000) ** 1.06 * 0.99),
    }


def test_a_fading_third_race_leaves_the_bar_at_once() -> None:
    """With three races, any fade below weight 1 takes the effective count under 3, so b is the
    population's until the race leaves: counted whole, the half at 1094 days read ``high`` on an
    error of 0.011 (and 0.004 at 1094.9) where the fit at weight 1 reads ``medium`` on 0.029."""
    races = _power_law_races()
    labels, errors = [], []
    for age in (900, 1000, 1080, 1090, 1094, 1094.9, 1095):
        rows = [
            _race("5k", *races["5k"], 30),
            _race("10k", *races["10k"], 60),
            _race("half", *races["half"], age),
        ]
        result = _run(rows)
        print(f"  half at {age} days: b {result.value} error {result.error} {result.confidence}")
        labels.append(_RANK[result.confidence])
        errors.append(result.error)
        if age == 900:
            assert result.population_default is False and result.confidence == "medium"
            assert result.error == pytest.approx(0.028804, abs=5e-7)
            assert result.t_multiplier == riegel_b.T_TABLE[1]
        else:
            assert result.population_default is True, age
            assert (result.value, result.error, result.confidence) == (1.06, 0.05, "low"), age
    assert labels == sorted(labels, reverse=True)
    assert errors == sorted(errors)


def test_a_fading_race_never_makes_the_fit_more_certain_than_without_it() -> None:
    """With four races, a fading race's weight shrinks its share of both the residuals and the
    count, so the error tends to the error of the three races without it, never below it, and the
    label is never better than theirs. Counted whole, the half's fade took the error to that of the
    three over the square root of 2. This is not a promise that the error only rises: as an
    outlier fades the error falls toward the fit without it (the half, 10 % slow, takes the label
    from ``medium`` to ``high``), and as a race on the line fades it rises."""
    races = _power_law_races()
    for fading in ("half", "marathon"):
        kept = [name for name in races if name != fading]
        without = _run([_race(name, *races[name], 30 + 10 * i) for i, name in enumerate(kept)])
        assert without.population_default is False and without.t_multiplier == riegel_b.T_TABLE[1]
        for weight in (0.999, 0.9, 0.5, 0.2, 0.1, 0.01, 0.001, 1e-6):
            rows = [_race(name, *races[name], 30 + 10 * i) for i, name in enumerate(kept)]
            rows.append(_race(fading, *races[fading], _age(weight)))
            result = _run(rows)
            print(
                f"  {fading} at {weight}: error {result.error} {result.confidence}; without {without.error}"
            )
            assert result.population_default is False, (fading, weight)
            assert result.error >= without.error * (1 - 1e-9), (fading, weight)
            assert _RANK[result.confidence] <= _RANK[without.confidence], (fading, weight)
            assert result.t_multiplier == without.t_multiplier, (fading, weight)
            assert result.interval_high - result.interval_low >= (
                without.interval_high - without.interval_low
            ) * (1 - 1e-9), (fading, weight)
        assert result.error == pytest.approx(without.error, rel=1e-4), fading


def test_the_t_multiplier_takes_the_whole_part_of_the_effective_count() -> None:
    """Five races, one at weight 0.5: the effective count is 4.5^2 / 4.25 = 4.765, so t is t(2)
    (rounding would give t(3)) and the error divides by 2.765; the oracle computes the same over
    the weights rescaled to sum to 4.765."""
    distances = (5000.0, 10000.0, 21100.0, 42200.0, 15000.0)
    rows = [
        _race(str(d), d, 0.24 * d**1.06 * (1 + 0.01 * (-1) ** i), 100 + i) for i, d in enumerate(distances)
    ]
    rows[-1] = _race("15000.0", 15000.0, 0.24 * 15000.0**1.06 * 1.02, _age(0.5))
    result = _run(rows)
    weights = [e.weight for e in result.efforts]
    assert sorted(weights) == [0.5, 1.0, 1.0, 1.0, 1.0]
    assert sum(weights) ** 2 / sum(w * w for w in weights) == pytest.approx(4.5**2 / 4.25, abs=1e-12)
    assert result.t_multiplier == riegel_b.T_TABLE[2]
    oracle = riegel_oracle.fit([(e.gap_equivalent_m, e.duration_s, e.weight) for e in result.efforts])
    assert abs(result.error - oracle.error) <= ORACLE_TOLERANCE
    assert abs(result.value - oracle.b) <= ORACLE_TOLERANCE
    assert abs(result.interval_low - oracle.low) <= ORACLE_TOLERANCE


def test_equal_weights_under_1_count_whole() -> None:
    """n equal weights give an effective count of n, but as a ratio of float sums it can land an
    ulp under: three races at 913.008 days compute 2.9999999999999996 and five at 913.003 days
    4.999999999999998. The count is read with a 1e-9 slack, so the three are fitted and the five
    take t(3), not the population and t(2)."""
    three = [
        _race(str(d), d, 0.24 * d**1.06 * (1.01 if d == 10000.0 else 1.0), 913.008)
        for d in (5000.0, 10000.0, 21100.0)
    ]
    fitted = _run(three)
    weights = [e.weight for e in fitted.efforts]
    assert sum(weights) ** 2 / sum(w * w for w in weights) < 3
    assert fitted.population_default is False and fitted.t_multiplier == riegel_b.T_TABLE[1]

    distances = (5000.0, 10000.0, 21100.0, 42200.0, 15000.0)
    five = [
        _race(str(d), d, 0.24 * d**1.06 * (1 + 0.01 * (-1) ** i), 913.003) for i, d in enumerate(distances)
    ]
    result = _run(five)
    weights = [e.weight for e in result.efforts]
    assert sum(weights) ** 2 / sum(w * w for w in weights) < 5
    assert result.t_multiplier == riegel_b.T_TABLE[3]


def test_the_bar_counts_races_by_their_weight() -> None:
    """Four races in the window, two of them fading: at weight 0.5 each the effective count is
    3^2 / 2.5 = 3.6 and b is fitted; at 0.2 each it is 2.4^2 / 2.08 = 2.77 and b is the
    population's, with every race still listed and its weight served."""
    races = _power_law_races()
    for weight, fitted in ((0.5, True), (0.2, False)):
        rows = [
            _race("5k", *races["5k"], 30),
            _race("marathon", *races["marathon"], 40),
            _race("10k", *races["10k"], _age(weight)),
            _race("half", *races["half"], _age(weight) + 1e-9),
        ]
        result = _run(rows)
        print(
            f"  two at {weight}: population {result.population_default}, weights {[e.weight for e in result.efforts]}"
        )
        assert len(result.efforts) == 4
        assert result.population_default is (not fitted), weight
        if not fitted:
            assert (result.value, result.confidence) == (1.06, "low")
            assert all(e.residual is None for e in result.efforts)


def test_t_table_matches_an_independent_source() -> None:
    assert sorted(riegel_b.T_TABLE) == list(range(1, 31))
    for df, value in riegel_b.T_TABLE.items():
        assert abs(value - riegel_oracle.t_quantile(0.9, df)) <= 1e-6, df
        assert abs(value - REFERENCE_T[df - 1]) <= PRINTED_TOLERANCE, df
        assert riegel_b.t_multiplier(df) == value
    assert abs(riegel_b.Z - riegel_oracle.Z) <= 1e-12
    assert riegel_b.t_multiplier(31) == riegel_b.t_multiplier(200) == riegel_b.Z


def test_a_non_finite_figure_is_unavailable() -> None:
    # A subnormal GAP pace is finite and > 0, so it qualifies, and its D_eq overflows to inf.
    rows = _abcd()[:3] + [_race("inf", 42200.0, 12000.0, 50, pace=5e-324)]
    result = _run(rows)
    assert math.isinf(result.efforts[-1].gap_equivalent_m)
    assert result.unavailable == "estimate_not_finite"
    assert (result.value, result.error, result.interval_low, result.interval_high) == (None, None, None, None)
    assert result.population_default is False and result.provisional is False
    assert [e.session_id for e in result.efforts] == ["a", "b", "c", "inf"]
    assert dict(result.parameters) == dict(riegel_b.PARAMETERS)

    # No population substitution, even with too few races for the bar.
    alone = _run([_race("inf", 42200.0, 12000.0, 50, pace=5e-324)])
    assert alone.unavailable == "estimate_not_finite" and alone.value is None


def test_a_gap_equivalent_distance_that_underflows_to_0_is_unavailable() -> None:
    """A finite timer above 0 (the smallest subnormal) at a finite GAP pace of 1e10 s/km gives a
    ``D_eq`` of exactly 0.0, whose logarithm does not exist: the estimate is unavailable, never a
    raise (a 500 at the route)."""
    rows = _abcd()[:3] + [_race("zero", 42200.0, 5e-324, 50, pace=1e10)]
    result = _run(rows)
    assert result.efforts[-1].gap_equivalent_m == 0.0
    assert result.unavailable == "estimate_not_finite" and result.value is None
    assert [e.session_id for e in result.efforts] == ["a", "b", "c", "zero"]


def test_parameters_are_the_reference_keys() -> None:
    assert dict(riegel_b.PARAMETERS) == {
        "window_days": 1095,
        "fade_days": 182,
        "min_races": 3,
        "min_spread": 2.0,
        "fitness_drop": 0.30,
        "fitness_lookback_days": 28,
        "population_b": 1.06,
        "population_error": 0.05,
        "coverage": 0.80,
        "high_max_error": 0.02,
        "medium_max_error": 0.04,
    }


def test_labels_compare_the_error_with_0_02_and_0_04() -> None:
    label = riegel_b._label
    assert [label(e) for e in (0.0, 0.02, 0.0200001, 0.04, 0.0400001)] == [
        "high",
        "high",
        "medium",
        "medium",
        "low",
    ]
    # Three races on one power law: the error is (all but) 0, high, the interval collapses to b.
    rows = [_race(str(d), d, 0.24 * d**1.05, 100 + i) for i, d in enumerate((5000.0, 10000.0, 20000.0))]
    result = _run(rows)
    assert result.confidence == "high" and result.error <= 1e-12
    assert result.value == pytest.approx(1.05, abs=1e-12)
    assert result.interval_high - result.interval_low <= 1e-11


def test_needs_features_reads_stored_values_only() -> None:
    good = _race("g", 5000.0, 1200.0, 100, features=None)
    assert needs_features(good, INSTANT, {}) is True
    for over in (
        {"admitted_as_workout": True},
        {"age_days": 1095},
        {"timer_time_s": 0},
        {"quality_flags": '["distance_regressed"]'},
        {"quality_flags": "{"},
        {"fitness": (20, 50, False, False)},
        {"sport": "other"},
        {"age_days": -1},
    ):
        row = _race("x", 5000.0, 1200.0, **{"age_days": 100, **over})
        assert needs_features(row, INSTANT, _fitness([row])) is False, over


NAN = float("nan")
INF = float("inf")
_ADVERSARIAL = [
    # (id, overrides, the expected reason, or None when the row qualifies)
    ("timer bool", {"timer_time_s": True}, "duration_unavailable"),
    ("timer string", {"timer_time_s": "1200"}, "duration_unavailable"),
    ("timer 0", {"timer_time_s": 0}, "duration_unavailable"),
    ("timer negative", {"timer_time_s": -1200}, "duration_unavailable"),
    ("timer nan", {"timer_time_s": NAN}, "duration_unavailable"),
    ("timer inf", {"timer_time_s": INF}, "duration_unavailable"),
    ("timer None, duration_s", {"timer_time_s": None, "duration_s": 1200.0}, None),
    ("timer None, summary duration", {"timer_time_s": None, "summary": '{"duration_s": 1200}'}, None),
    ("timer None, nothing", {"timer_time_s": None, "summary": None}, "duration_unavailable"),
    ("duration_s bool", {"timer_time_s": None, "duration_s": True}, "duration_unavailable"),
    ("duration_s string", {"timer_time_s": None, "duration_s": "1200"}, "duration_unavailable"),
    ("duration_s 0", {"timer_time_s": None, "duration_s": 0}, "duration_unavailable"),
    ("duration_s negative", {"timer_time_s": None, "duration_s": -1.0}, "duration_unavailable"),
    ("duration_s nan", {"timer_time_s": None, "duration_s": NAN}, "duration_unavailable"),
    ("duration_s inf", {"timer_time_s": None, "duration_s": INF}, "duration_unavailable"),
    ("duration_s huge int", {"timer_time_s": None, "duration_s": 10**400}, "duration_unavailable"),
    ("summary not json", {"timer_time_s": None, "summary": "{not json"}, "summary_unreadable"),
    ("summary a list", {"timer_time_s": None, "summary": "[1200]"}, "summary_unreadable"),
    ("summary bytes", {"timer_time_s": None, "summary": b"\xff\xfe"}, "summary_unreadable"),
    ("flags undecodable", {"quality_flags": "[smart"}, "quality_flags_unreadable"),
    ("flags an object", {"quality_flags": '{"a": 1}'}, "quality_flags_unreadable"),
    ("flags a string", {"quality_flags": '"smart_recording"'}, "quality_flags_unreadable"),
    ("flags a number", {"quality_flags": 5}, "quality_flags_unreadable"),
    ("flags json null", {"quality_flags": "null"}, None),
    ("flags None", {"quality_flags": None}, None),
    (
        "flags both, fixed order",
        {"quality_flags": '["smart_recording", "distance_regressed"]'},
        "distance_regressed",
    ),
    ("flags decoded list", {"quality_flags": ["smart_recording"]}, "smart_recording"),
    ("features None", {"features": None}, "features_unavailable"),
    ("features a string", {"features": "x"}, "features_unavailable"),
    (
        "features flags not a list",
        {"features": {"gap_avg_pace_s_per_km": 240.0, "flags": "grade_clamped"}},
        "features_unavailable",
    ),
    ("features no flags", {"features": {"gap_avg_pace_s_per_km": 240.0}}, "features_unavailable"),
    (
        "F013 flags all three",
        {
            "features": {
                "gap_avg_pace_s_per_km": 240.0,
                "flags": ["grade_clamped", "smart_recording", "distance_regressed"],
            }
        },
        "distance_regressed",
    ),
    (
        "F013 grade_clamped alone",
        {"features": {"gap_avg_pace_s_per_km": 240.0, "flags": ["grade_clamped"]}},
        "grade_clamped",
    ),
    (
        "F013 other flag",
        {"features": {"gap_avg_pace_s_per_km": 240.0, "flags": ["gap_unavailable", "gps_degraded"]}},
        None,
    ),
    (
        "gap pace None",
        {"pace": None, "features": {"gap_avg_pace_s_per_km": None, "flags": []}},
        "gap_pace_unavailable",
    ),
    ("gap pace 0", {"pace": 0}, "gap_pace_unavailable"),
    ("gap pace negative", {"pace": -240.0}, "gap_pace_unavailable"),
    ("gap pace nan", {"pace": NAN}, "gap_pace_unavailable"),
    ("gap pace inf", {"pace": INF}, "gap_pace_unavailable"),
    ("gap pace bool", {"pace": True}, "gap_pace_unavailable"),
    ("gap pace string", {"pace": "240"}, "gap_pace_unavailable"),
    (
        "gap coverage 0",
        {"features": {"gap_avg_pace_s_per_km": 240.0, "gap_coverage": 0.0, "flags": ["gap_unavailable"]}},
        None,
    ),
    ("fitness earlier null", {"fitness": (20, None, False, False)}, None),
    ("fitness nan", {"fitness": (NAN, 50, False, False)}, None),
    ("fitness earlier 0", {"fitness": (0, 0, True, True)}, None),
    ("fitness at the bound", {"fitness": (35, 50, False, False)}, None),
    ("fitness under the bound", {"fitness": (34.99, 50, False, False)}, "fitness_dropped"),
    (
        "admitted as workout",
        {"admitted_as_workout": True, "workout_name": "Threshold Run"},
        "admitted_as_workout",
    ),
]


@pytest.mark.parametrize(
    ("overrides", "expected"), [c[1:] for c in _ADVERSARIAL], ids=[c[0] for c in _ADVERSARIAL]
)
@pytest.mark.parametrize("fitness_kind", ["mapping", "callable", "no chart days"])
def test_an_adversarial_row_never_raises(overrides: dict, expected: str | None, fitness_kind: str) -> None:
    over = dict(overrides)
    pace = over.pop("pace", 240.0)
    row = _race("r", 5000.0, 1200.0, 100, pace=pace, **over)
    mapping = _fitness([row])
    if fitness_kind == "callable":
        fitness = mapping.get
    elif fitness_kind == "no chart days":
        fitness = {}  # a chart with no days: every figure null, the screen passes all
        if expected == "fitness_dropped":
            expected = None
    else:
        fitness = mapping
    needs = needs_features(row, INSTANT, fitness)
    result = estimate([row], INSTANT, fitness)
    if expected is None:
        assert [e.session_id for e in result.efforts] == ["r"] and result.excluded == ()
        assert needs is True
    else:
        assert result.efforts == () and [e.reason for e in result.excluded] == [expected]
        # Rows 6 and 7 read the features, so the predicate passed those rows on to them.
        assert needs is ("features" in overrides or "pace" in overrides)
    assert result.population_default is True


def test_rows_after_the_instant_and_other_sports_are_in_neither_list() -> None:
    rows = [
        _race("later", 5000.0, 1200.0, -0.001),
        _race("bike", 5000.0, 1200.0, 100, sport="cycling"),
        _race("untagged", 5000.0, 1200.0, 100, activity_tag=None),
    ]
    result = _run(rows)
    assert result.efforts == () and result.excluded == ()


def test_exclusion_fitness_figures_are_set_for_fitness_dropped_only() -> None:
    """Every excluded race below carries a chart day for both fitness figures, but only the
    ``fitness_dropped`` one serves them; rows 6 and 7 exclude races whose figures were read and
    still serve null, as the contract's ``RiegelExclusion`` says."""
    fit = (40, 41, False, False)
    rows = [
        _race("features", 5000.0, 1200.0, 100, fit, features=None),
        _race(
            "clamped",
            5000.0,
            1200.0,
            110,
            fit,
            features={"gap_avg_pace_s_per_km": 240.0, "flags": ["grade_clamped"]},
        ),
        _race("pace", 5000.0, 1200.0, 120, fit, pace=0),
        _race("smart", 5000.0, 1200.0, 130, fit, quality_flags='["smart_recording"]'),
        _race("workout", 5000.0, 1200.0, 140, fit, admitted_as_workout=True),
        _race("dropped", 5000.0, 1200.0, 150, (29, 50, False, False)),
    ]
    result = _run(rows)
    reasons = {e.session_id: e.reason for e in result.excluded}
    assert reasons == {
        "features": "features_unavailable",
        "clamped": "grade_clamped",
        "pace": "gap_pace_unavailable",
        "smart": "smart_recording",
        "workout": "admitted_as_workout",
        "dropped": "fitness_dropped",
    }
    for e in result.excluded:
        figures = (e.fitness_before, e.fitness_earlier, e.fitness_drop)
        if e.reason == "fitness_dropped":
            assert figures[:2] == (29, 50) and figures[2] == pytest.approx(0.42, abs=1e-12)
        else:
            assert figures == (None, None, None), e


def test_a_race_at_the_instant_is_used() -> None:
    """A race that starts exactly at the instant is at it, not after it: used at weight 1, and the
    predicate passes it on to have its features read."""
    at = _race("at", 5000.0, 1200.0, 0)
    assert datetime.fromisoformat(at["start_time"]) == INSTANT
    assert needs_features(at, INSTANT, {}) is True
    result = _run([at])
    assert [(e.session_id, e.weight) for e in result.efforts] == [("at", 1.0)]
    assert result.excluded == ()


def test_the_oracle_imports_nothing_from_the_estimator() -> None:
    def imports(path: Path) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                names.add(module)
                names.update(
                    f"{module}.{alias.name}" if module.strip(".") else module + alias.name
                    for alias in node.names
                )
        return names

    oracle = imports(ORACLE_PATH)
    assert not {n for n in oracle if n.split(".")[0] == "runcoach_api" or n.startswith(".")}, oracle
    assert not any(hasattr(riegel_oracle, attr) for attr in ("riegel_b", "estimate"))

    estimator = imports(ESTIMATOR_PATH)
    forbidden = {"sqlite3", "runcoach_api.db", "runcoach_api.main"}
    hits = {
        n
        for n in estimator
        if n.split(".")[0] == "sqlite3"
        or any(n == f or n.startswith(f + ".") for f in forbidden)
        or (n.startswith(".") and any(part in {"db", "main"} for part in n.lstrip(".").split(".")))
    }
    assert not hits, hits
    assert "runcoach_api.metrics.finite" in estimator  # the walk does see a from-import's names
