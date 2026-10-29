"""A goal-race projection oracle outside the code: the F032 reference's rules, restated, nothing else.

Written from the F032 reference sections 3.1 (the fitted line), 3.2 (the closest race), 4.1
(leave-one-out) and 8 (worked examples), and from the F029 reference's bar (section 1's
``min_races`` 3 and ``min_spread`` 2.0, read with a 1e-9 slack). It imports nothing from
``runcoach_api`` and ``test_pace_target_oracle.py`` pins that by parsing this file's imports, so
the expected values it produces cannot share code with the estimator they check
(``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``). It reads
``riegel_oracle`` for its Student's t quantile only (W. H. Press et al., *Numerical Recipes*,
3rd ed., section 6.4, by the regularized incomplete beta's continued fraction, bisected for the
0.9 quantile; z above 30 degrees of freedom), and types the rest out again by hand.

Races are ``(d_eq_m, time_s, weight)`` triples, optionally with a fourth item, the start (any
ordered value, an ISO string will do), which only the closest-race tie reads. They are the races
already used; nothing here qualifies them.

- **The fit** solves the 2x2 normal equations in raw sums, ``[[S_w, S_wx], [S_wx, S_wxx]] [a, b]
  = [S_wy, S_wxy]``, by Cramer's rule (not the reference's centred form). ``n_eff = S_w^2 /
  sum(w^2)``; the weights are rescaled to sum to ``n_eff`` and the standard error and the
  scatter ``s2`` are computed over the rescaled weights literally: ``se = sqrt(sum(w' r^2) /
  (n_eff - 2) / sum(w' (x - x_w)^2))`` and ``s2 = sum(w' r^2) / (n_eff - 2)`` (reference 3.1
  writes the same as ``n_eff * sum(w r^2) / (sum(w) * (n_eff - 2))``). ``df = floor(n_eff +
  1e-9) - 2``.
- **The fitted line** (route ``fitted_line``, the bar cleared): ``y0 = y_w + b (x0 - x_w)``,
  ``sp = sqrt(s2 (1 + 1 / n_eff) + (x0 - x_w)^2 se^2)``, time ``exp(y0)``, interval
  ``[exp(y0 - t sp), exp(y0 + t sp)]``, halfwidth ``exp(t sp) - 1``.
- **The closest race** (route ``closest_race``, below the bar): the race with the smallest
  ``|ln D - ln D_eq|`` as a difference of two logarithms; differences within ``TIE_SLACK`` tie,
  and a tie goes to the higher weight, then the later start. ``T (D / D_eq)^1.06``, no interval.
- **Leave-one-out** (section 4.1): for each race, the projection from the others with the bar
  re-applied; ``shift = T_without / T - 1``, countable on route ``fitted_line`` only.
- **The goal-time gap** (section 6): ``gap_s = target - projected``, ``gap_fraction = gap_s /
  projected``, ``within_range`` against the interval, null on route ``closest_race``.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# The sibling oracle is loaded by path, as the tests load this file: the tests directory is not a
# package.
_RIEGEL_PATH = Path(__file__).with_name("riegel_oracle.py")
if "riegel_oracle" in sys.modules:
    _riegel = sys.modules["riegel_oracle"]
else:
    _spec = importlib.util.spec_from_file_location("riegel_oracle", _RIEGEL_PATH)
    _riegel = importlib.util.module_from_spec(_spec)
    sys.modules["riegel_oracle"] = _riegel
    _spec.loader.exec_module(_riegel)  # type: ignore[union-attr]
Z: float = _riegel.Z
t_quantile = _riegel.t_quantile

MIN_RACES = 3
MIN_SPREAD = 2.0
COUNT_SLACK = 1e-9
POPULATION_B = 1.06
TIE_SLACK = 1e-9

Race = Sequence[object]


def _parts(race: Race) -> tuple[float, float, float, object]:
    d, t, w = float(race[0]), float(race[1]), float(race[2])
    start = race[3] if len(race) > 3 else None
    return d, t, w, start


@dataclass(frozen=True)
class Fit:
    b: float
    se: float
    n_eff: float
    df: int
    t: float
    x_w: float
    y_w: float
    s2: float


@dataclass(frozen=True)
class Projection:
    route: str
    time_s: float
    interval: tuple[float, float] | None
    halfwidth: float | None
    pace_s_per_km: float
    distance_m: float


@dataclass(frozen=True)
class Closest:
    d_eq_m: float
    time_s: float
    weight: float
    start: object


@dataclass(frozen=True)
class LeaveOneOut:
    route: str
    time_s: float
    shift: float


@dataclass(frozen=True)
class Gap:
    gap_s: float
    gap_fraction: float
    within_range: bool | None


def _used(races: Sequence[Race]) -> list[tuple[float, float, float, object]]:
    return [r for r in map(_parts, races) if r[2] > 0]


def clears_the_bar(races: Sequence[Race]) -> bool:
    """F029's bar: ``n_eff >= 3`` and the longest ``D_eq`` at least twice the shortest, 1e-9 slack."""
    used = _used(races)
    if not used:
        return False
    sw = sum(w for _d, _t, w, _s in used)
    n_eff = sw * sw / sum(w * w for _d, _t, w, _s in used)
    ds = [d for d, _t, _w, _s in used]
    return n_eff + COUNT_SLACK >= MIN_RACES and max(ds) / min(ds) >= MIN_SPREAD


def fit(races: Sequence[Race]) -> Fit:
    """The weighted fit of ``ln T`` on ``ln D_eq`` with its effective count, se, scatter and t."""
    points = [(math.log(d), math.log(t), w) for d, t, w, _s in _used(races)]
    sw = swx = swxx = swy = swxy = 0.0
    for x, y, w in points:
        sw += w
        swx += w * x
        swxx += w * x * x
        swy += w * y
        swxy += w * x * y
    det = sw * swxx - swx * swx
    a = (swy * swxx - swx * swxy) / det
    b = (sw * swxy - swx * swy) / det
    x_w = swx / sw
    y_w = swy / sw
    n_eff = sw * sw / sum(w * w for _x, _y, w in points)
    scaled = [(x, y, w * n_eff / sw) for x, y, w in points]
    ssr = sum(w * (y - (a + b * x)) ** 2 for x, y, w in scaled)
    sxx = sum(w * (x - x_w) ** 2 for x, _y, w in scaled)
    s2 = ssr / (n_eff - 2)
    se = math.sqrt(s2 / sxx)
    df = math.floor(n_eff + COUNT_SLACK) - 2
    t = t_quantile(0.9, df) if df <= 30 else Z
    return Fit(b, se, n_eff, df, t, x_w, y_w, s2)


def closest_race(races: Sequence[Race], distance_m: float, tie_slack: float = TIE_SLACK) -> Closest:
    """Reference 3.2: the smallest ``|ln D - ln D_eq|``; ties to the higher weight, then the later start."""
    x0 = math.log(distance_m)
    best: Closest | None = None
    best_gap = math.inf
    for d, t, w, start in _used(races):
        gap = abs(x0 - math.log(d))
        if best is None or gap < best_gap - tie_slack:
            best, best_gap = Closest(d, t, w, start), gap
        elif gap <= best_gap + tie_slack:
            later = start is not None and best.start is not None and start > best.start
            if w > best.weight or (w == best.weight and later):
                best, best_gap = Closest(d, t, w, start), min(gap, best_gap)
    if best is None:
        raise ValueError("no race used")
    return best


def projection(races: Sequence[Race], distance_m: float) -> Projection:
    """Reference 3.1 when the races clear the bar, else 3.2."""
    km = distance_m / 1000.0
    if clears_the_bar(races):
        f = fit(races)
        x0 = math.log(distance_m)
        y0 = f.y_w + f.b * (x0 - f.x_w)
        sp = math.sqrt(f.s2 * (1.0 + 1.0 / f.n_eff) + (x0 - f.x_w) ** 2 * f.se**2)
        time = math.exp(y0)
        low, high = math.exp(y0 - f.t * sp), math.exp(y0 + f.t * sp)
        return Projection("fitted_line", time, (low, high), math.exp(f.t * sp) - 1.0, time / km, distance_m)
    c = closest_race(races, distance_m)
    time = c.time_s * (distance_m / c.d_eq_m) ** POPULATION_B
    return Projection("closest_race", time, None, None, time / km, distance_m)


def leave_one_out(races: Sequence[Race], distance_m: float) -> list[LeaveOneOut] | None:
    """Reference 4.1: per race used, the projection from the others; null on route ``closest_race``."""
    base = projection(races, distance_m)
    if base.route != "fitted_line":
        return None
    used = [r for r in races if float(r[2]) > 0]
    out: list[LeaveOneOut] = []
    for i in range(len(used)):
        rest = used[:i] + used[i + 1 :]
        p = projection(rest, distance_m)
        out.append(LeaveOneOut(p.route, p.time_s, p.time_s / base.time_s - 1.0))
    return out


def largest_countable_shift(rows: Sequence[LeaveOneOut] | None) -> float | None:
    """The largest ``|shift|`` among rows on route ``fitted_line``; null when none is countable."""
    countable = [abs(r.shift) for r in rows or () if r.route == "fitted_line"]
    return max(countable) if countable else None


def goal_gap(p: Projection, target_time_s: float) -> Gap:
    """Reference section 6: the goal time against the projection and its interval."""
    gap_s = target_time_s - p.time_s
    within = None if p.interval is None else p.interval[0] <= target_time_s <= p.interval[1]
    return Gap(gap_s, gap_s / p.time_s, within)
