"""The goal-race projection (F032, reference sections 3 and 4.1): the time, pace and range an
athlete can expect over a goal distance from the races F029 used, by the fitted line when they
clear F029's bar and by the closest race below it.

**Pure, by design**, on the pattern of ``metrics/riegel_b.py``: ``project`` takes the races F029
used (``Race``: ``D_eq`` in metres, ``T`` in seconds, the weight, the start and the session id),
F029's ``RiegelEstimate`` and the goal distance in metres, and returns a frozen ``Projection``; it
imports neither ``config`` nor ``db`` nor ``fastapi``. The fit has one source: ``riegel_b.fit``,
called once for the main line and once per leave-one-out, and its t multiplier is read from the
``Fit`` it returns (``riegel_b.t_multiplier`` at ``floor(n_eff + COUNT_SLACK) - 2`` degrees of
freedom). Metres throughout.

**The fitted line** (section 3.1, route ``fitted_line``), when the races clear the bar: with the
fit's ``x_w``, ``y_w``, b, ``n_eff``, ``se`` and ``sum(w r^2)``, ``x0 = ln(D)``, ``y0 = y_w + b (x0 -
x_w)``, ``s2 = n_eff * sum(w r^2) / (sum(w) * (n_eff - 2))`` (one race's scatter under F029's
convention of weights rescaled to sum to ``n_eff``), ``sp = sqrt(s2 * (1 + 1 / n_eff) + (x0 - x_w)^2
* se^2)``; the time is ``exp(y0)``, the interval ``[exp(y0 - t sp), exp(y0 + t sp)]``, the halfwidth
``exp(t sp) - 1`` (the upper side), the pace and its interval the times over ``D / 1000``.

**The closest race** (section 3.2, route ``closest_race``), below the bar with at least one race:
the race with the smallest ``|ln(D) - ln(D_eq)|``, computed as the difference of the two
logarithms; the races within ``TIE_SLACK`` of the smallest tie, and the tie goes to the higher
weight, then the later start. The time is ``T * (D / D_eq) ** POPULATION_B``; no interval;
``provisional`` true.

**Leave-one-out** (section 4.1) on route ``fitted_line``: per race, the projection from the others
with the weights unchanged and the bar re-applied, its route, ``shift = T_without / T - 1``;
``countable`` when the route is ``fitted_line``; ``largest`` the row with the largest countable
``|shift|``, the earlier start on a tie, ``None`` when none is countable (with exactly three races
none is). ``None`` on route ``closest_race``.

**Unavailable** (section 3.3), in this order: ``no_races`` with no race; ``riegel_unavailable`` when
F029's estimate is ``estimate_not_finite``; ``projection_not_finite`` when the goal distance or a
race's figure is not a finite positive number, when any figure served (time, interval ends,
halfwidth, pace, a leave-one-out time or shift) is not finite, or when ``exp``, ``log`` or a power
raises ``OverflowError`` or ``ValueError`` on the way (a 1e308 m goal overflows ``exp``). Every
figure is then ``None`` and ``unavailable`` names the reason.

The label constants (section 4.2: the labels, the bases and the reasons) are defined here for the
label rules that read the projection; ``project`` itself serves no label.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from runcoach_api.metrics import riegel_b
from runcoach_api.metrics.riegel_b import COUNT_SLACK, POPULATION_B, RiegelEstimate

HIGH_MAX_UNCERTAINTY = 0.02
"""``high`` while the uncertainty ``u`` (reference section 4) is at most this."""
MEDIUM_MAX_UNCERTAINTY = 0.05
"""``medium`` while ``u`` is at most this, else ``low``."""
FITNESS_GAP = 0.30
"""Lowered one step when ``fitness_now < (1 - FITNESS_GAP) * fitness_at_races`` (section 5)."""
MAX_EXTRAPOLATION = 1.5
"""``beyond_longest_race`` when ``D > MAX_EXTRAPOLATION * D_max * (1 + 1e-9)``, ``D_max`` the
largest ``D_eq`` among the races used."""
CALIBRATED_MIN_M = 5000
CALIBRATED_MAX_M = 42195
"""The calibrated range of goal distances, bounds inclusive; outside it ``outside_calibrated_range``."""
TIE_SLACK = COUNT_SLACK
"""Two log distances within this are a tie for the closest race (section 3.2); F029's ``COUNT_SLACK``."""

PARAMETERS: MappingProxyType[str, float | int] = MappingProxyType(
    {
        "high_max_uncertainty": HIGH_MAX_UNCERTAINTY,
        "medium_max_uncertainty": MEDIUM_MAX_UNCERTAINTY,
        "fitness_gap": FITNESS_GAP,
        "max_extrapolation": MAX_EXTRAPOLATION,
        "calibrated_min_m": CALIBRATED_MIN_M,
        "calibrated_max_m": CALIBRATED_MAX_M,
        "population_b": POPULATION_B,
        "tie_slack": TIE_SLACK,
    }
)
"""The F032 reference section 1 keys and values, served as the response's ``parameters``."""

FITTED_LINE = "fitted_line"
CLOSEST_RACE = "closest_race"

NO_RACES = "no_races"
RIEGEL_UNAVAILABLE = "riegel_unavailable"
PROJECTION_NOT_FINITE = "projection_not_finite"

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
RANGE = "range"
ONE_RACE = "one_race"
FITNESS_BELOW_RACES = "fitness_below_races"
POPULATION_DEFAULT = "population_default"
BELOW_ONE = "below_one"
OUTSIDE_CALIBRATED_RANGE = "outside_calibrated_range"
BEYOND_LONGEST_RACE = "beyond_longest_race"


@dataclass(frozen=True)
class Race:
    """A race F029 used, as the projection reads it: its GAP-equivalent distance in metres, its
    duration in seconds, its weight, its start and its session id."""

    session_id: str
    start_time: datetime
    d_eq_m: float
    time_s: float
    weight: float

    @classmethod
    def from_effort(cls, effort: riegel_b.Effort) -> Race:
        """The race behind one of F029's efforts."""
        return cls(
            session_id=effort.session_id,
            start_time=effort.start_time,
            d_eq_m=effort.gap_equivalent_m,
            time_s=effort.duration_s,
            weight=effort.weight,
        )


@dataclass(frozen=True)
class LeaveOneOut:
    """The projection from every race but this one (section 4.1): its route, time and ``shift =
    T_without / T - 1``; ``countable`` when its route is ``fitted_line``."""

    session_id: str
    start_time: datetime
    route: str
    time_s: float
    shift: float
    countable: bool


@dataclass(frozen=True)
class Projection:
    """The goal-race projection over ``distance_m``. On route ``fitted_line`` every figure is set
    and ``closest`` is ``None``; on route ``closest_race`` the interval, the halfwidth, the pace
    interval, ``t_multiplier``, ``leave_one_out`` and ``largest`` are ``None`` and ``provisional`` is
    true; when ``unavailable`` is set every figure and the route are ``None``."""

    distance_m: float
    route: str | None
    time_s: float | None
    interval_low: float | None
    interval_high: float | None
    halfwidth: float | None
    pace_s_per_km: float | None
    pace_low: float | None
    pace_high: float | None
    t_multiplier: float | None
    provisional: bool
    closest: Race | None
    leave_one_out: tuple[LeaveOneOut, ...] | None
    largest: LeaveOneOut | None
    unavailable: str | None
    parameters: MappingProxyType[str, float | int] = field(default_factory=lambda: PARAMETERS)


@dataclass(frozen=True)
class _Route:
    """One projection's route and figures, before the leave-one-out and the finiteness screen."""

    route: str
    time_s: float
    interval_low: float | None
    interval_high: float | None
    halfwidth: float | None
    t_multiplier: float | None
    closest: Race | None


def _unavailable(distance_m: float, reason: str) -> Projection:
    return Projection(
        distance_m=distance_m,
        route=None,
        time_s=None,
        interval_low=None,
        interval_high=None,
        halfwidth=None,
        pace_s_per_km=None,
        pace_low=None,
        pace_high=None,
        t_multiplier=None,
        provisional=False,
        closest=None,
        leave_one_out=None,
        largest=None,
        unavailable=reason,
    )


def _positive_finite(value: Any) -> bool:
    return (
        isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    )


def _closest(races: list[Race], x0: float) -> Race:
    """Section 3.2's race: the smallest ``|x0 - ln(D_eq)|`` as a difference of logs, every race
    within ``TIE_SLACK`` of it tying, the tie to the higher weight, then the later start."""
    gaps = [abs(x0 - math.log(race.d_eq_m)) for race in races]
    nearest = min(gaps)
    tied = [race for race, gap in zip(races, gaps, strict=True) if gap <= nearest + TIE_SLACK]
    return max(tied, key=lambda race: (race.weight, race.start_time))


def _route(races: list[Race], distance_m: float) -> _Route:
    """The fitted line when ``races`` clear F029's bar, else the closest race. May raise
    ``OverflowError`` or ``ValueError`` and may return non-finite figures; ``project`` screens both."""
    x0 = math.log(distance_m)
    line = riegel_b.fit([(race.d_eq_m, race.time_s, race.weight) for race in races])
    if not line.clears_bar:
        race = _closest(races, x0)
        return _Route(
            CLOSEST_RACE,
            race.time_s * (distance_m / race.d_eq_m) ** POPULATION_B,
            None,
            None,
            None,
            None,
            race,
        )
    assert line.b is not None and line.se is not None and line.t is not None
    assert (
        line.x_w is not None and line.y_w is not None and line.sum_w is not None and line.sum_w_r2 is not None
    )
    y0 = line.y_w + line.b * (x0 - line.x_w)
    s2 = line.n_eff * line.sum_w_r2 / (line.sum_w * (line.n_eff - 2))
    sp = math.sqrt(s2 * (1 + 1 / line.n_eff) + (x0 - line.x_w) ** 2 * line.se**2)
    spread = line.t * sp
    return _Route(
        FITTED_LINE,
        math.exp(y0),
        math.exp(y0 - spread),
        math.exp(y0 + spread),
        math.exp(spread) - 1,
        line.t,
        None,
    )


def _leave_one_out(races: list[Race], distance_m: float, time_s: float) -> tuple[LeaveOneOut, ...]:
    rows = []
    for index, race in enumerate(races):
        others = races[:index] + races[index + 1 :]
        without = _route(others, distance_m)
        rows.append(
            LeaveOneOut(
                session_id=race.session_id,
                start_time=race.start_time,
                route=without.route,
                time_s=without.time_s,
                shift=without.time_s / time_s - 1,
                countable=without.route == FITTED_LINE,
            )
        )
    return tuple(rows)


def _largest(rows: Iterable[LeaveOneOut]) -> LeaveOneOut | None:
    """The countable row with the largest ``|shift|``, the earlier start on a tie."""
    countable = [row for row in rows if row.countable]
    if not countable:
        return None
    return min(countable, key=lambda row: (-abs(row.shift), row.start_time))


def project(races: Iterable[Race], estimate: RiegelEstimate, distance_m: float) -> Projection:
    """The projection over ``distance_m`` metres from ``races`` (the efforts F029 used, in F029's
    order) and F029's ``estimate`` at the same instant: sections 3.1 to 3.3 and 4.1."""
    used = list(races)
    if not used:
        return _unavailable(distance_m, NO_RACES)
    if estimate.unavailable == riegel_b.ESTIMATE_NOT_FINITE:
        return _unavailable(distance_m, RIEGEL_UNAVAILABLE)
    if not _positive_finite(distance_m) or not all(
        _positive_finite(race.d_eq_m) and _positive_finite(race.time_s) and _positive_finite(race.weight)
        for race in used
    ):
        return _unavailable(distance_m, PROJECTION_NOT_FINITE)
    try:
        main = _route(used, distance_m)
        rows = _leave_one_out(used, distance_m, main.time_s) if main.route == FITTED_LINE else None
        km = distance_m / 1000
        pace = main.time_s / km
        pace_low = None if main.interval_low is None else main.interval_low / km
        pace_high = None if main.interval_high is None else main.interval_high / km
    except (OverflowError, ValueError, ZeroDivisionError):
        return _unavailable(distance_m, PROJECTION_NOT_FINITE)
    figures = [main.time_s, pace, main.interval_low, main.interval_high, main.halfwidth, pace_low, pace_high]
    for row in rows or ():
        figures.extend((row.time_s, row.shift))
    if not all(math.isfinite(figure) for figure in figures if figure is not None):
        return _unavailable(distance_m, PROJECTION_NOT_FINITE)
    return Projection(
        distance_m=distance_m,
        route=main.route,
        time_s=main.time_s,
        interval_low=main.interval_low,
        interval_high=main.interval_high,
        halfwidth=main.halfwidth,
        pace_s_per_km=pace,
        pace_low=pace_low,
        pace_high=pace_high,
        t_multiplier=main.t_multiplier,
        provisional=main.route == CLOSEST_RACE,
        closest=main.closest,
        leave_one_out=rows,
        largest=_largest(rows) if rows is not None else None,
        unavailable=None,
    )
