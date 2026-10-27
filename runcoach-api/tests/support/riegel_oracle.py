"""A Riegel-b oracle outside the code: the F029 reference's fit, restated another way, nothing else.

Written from the F029 reference (section 1's constants, section 3's fit, section 4's worked
examples), not from ``metrics.riegel_b``. It imports nothing from ``runcoach_api`` and
``test_riegel_b_estimator.py`` pins that by parsing this file's imports, so the expected values it
produces cannot share code with the module they check
(``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``).

It takes the races already chosen, as ``(d_eq_m, time_s, weight)`` triples; it qualifies nothing.

- **The fit** is weighted least squares of ``y = ln(T)`` on ``x = ln(D_eq)`` solved from the 2x2
  normal equations in raw sums, ``[[S_w, S_wx], [S_wx, S_wxx]] [a, b] = [S_wy, S_wxy]``, by
  Cramer's rule, rather than the reference's centred sums, so a slip in the estimator's centring
  cannot be copied here. The standard error is ``sqrt(sum(w r^2) / (n - 2) / (S_wxx - S_wx^2 /
  S_w))``, the same quantity as the reference's centred denominator.
- **Student's t quantiles** come from the regularized incomplete beta function by its continued
  fraction (W. H. Press et al., *Numerical Recipes*, 3rd ed., section 6.4, ``betai`` and
  ``betacf``): for ``t >= 0`` and ``nu`` degrees of freedom, ``P(T <= t) = 1 - I_x(nu / 2, 1 / 2) /
  2`` with ``x = nu / (nu + t^2)`` (Numerical Recipes 6.4.9, Abramowitz and Stegun 26.7.1 with
  26.5.27). The 0.9 quantile is found by bisection on that CDF. The normal quantile ``z`` is the
  stdlib's ``NormalDist``.

``WORKED_EXAMPLES`` is the reference's section 4 table typed in by hand, at its printed precision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

WINDOW_DAYS = 1095
FADE_DAYS = 182
POPULATION_B = 1.06
POPULATION_ERROR = 0.05
Z = NormalDist().inv_cdf(0.9)


def weight(age_days: float) -> float:
    """Reference section 1: ``min(1, (1095 - age) / 182)``, 0 at or past the window."""
    if age_days >= WINDOW_DAYS:
        return 0.0
    return min(1.0, (WINDOW_DAYS - age_days) / FADE_DAYS)


def _betacf(a: float, b: float, x: float) -> float:
    """Numerical Recipes 6.4: the continued fraction of the incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 10000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            return h
    raise ArithmeticError("betacf did not converge")


def betai(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta ``I_x(a, b)`` (Numerical Recipes 6.4.9 and its symmetry)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: int) -> float:
    """``P(T <= t)`` for Student's t with ``df`` degrees of freedom."""
    tail = betai(df / 2.0, 0.5, df / (df + t * t)) / 2.0
    return 1.0 - tail if t >= 0 else tail


def t_quantile(p: float, df: int) -> float:
    """The ``p`` quantile (``p > 0.5``) of Student's t by bisection on ``t_cdf``."""
    low, high = 0.0, 1000.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


@dataclass(frozen=True)
class Fit:
    b: float
    error: float
    t: float
    low: float
    high: float
    residuals: tuple[float, ...]
    """``exp(r) - 1`` in the order given."""


def fit(races: list[tuple[float, float, float]]) -> Fit:
    """The weighted fit of ``(d_eq_m, time_s, weight)`` races with its Student's t 80 % interval."""
    sw = swx = swxx = swy = swxy = 0.0
    points = [(math.log(d), math.log(t), w) for d, t, w in races]
    for x, y, w in points:
        sw += w
        swx += w * x
        swxx += w * x * x
        swy += w * y
        swxy += w * x * y
    det = sw * swxx - swx * swx
    a = (swy * swxx - swx * swxy) / det
    b = (sw * swxy - swx * swy) / det
    resid = [y - (a + b * x) for x, y, _w in points]
    n = len(points)
    sxx = swxx - swx * swx / sw
    se = math.sqrt(sum(w * r * r for (_x, _y, w), r in zip(points, resid, strict=True)) / (n - 2) / sxx)
    t = t_quantile(0.9, n - 2) if n - 2 <= 30 else Z
    return Fit(b, se, t, b - t * se, b + t * se, tuple(math.exp(r) - 1 for r in resid))


A = (5000.0, 1200.0)
B = (10000.0, 2520.0)
C = (21100.0, 5600.0)
D = (42200.0, 12000.0)
H = (10000.0, 2350.0)
F = (21100.0, 4700.0)

WORKED_EXAMPLES: dict[str, tuple[list[tuple[float, float, float]], tuple[float, ...]]] = {
    # name: (races as (d_eq, time, weight), (b, error, t, low, high, residual % ...))
    "A, B, C, D": (
        [(*A, 1.0), (*B, 1.0), (*C, 1.0), (*D, 1.0)],
        (1.078410, 0.004995, 1.885618, 1.068991, 1.087830, 0.388, -0.169, -0.838, 0.625),
    ),
    "A, B, C": (
        [(*A, 1.0), (*B, 1.0), (*C, 1.0)],
        (1.069870, 0.000285, 3.077684, 1.068992, 1.070748, -0.012, 0.024, -0.011),
    ),
    "A, B, C, D at 0.5": (
        [(*A, 1.0), (*B, 1.0), (*C, 1.0), (*D, 0.5)],
        (1.076416, 0.005066, 1.885618, 1.066864, 1.085969, 0.294, -0.124, -0.646, 0.959),
    ),
    "A, H, F": (
        [(*A, 1.0), (*H, 1.0), (*F, 1.0)],
        (0.947937, 0.011909, 3.077684, 0.911284, 0.984589, -0.512, 0.995, -0.475),
    ),
}
POPULATION_INTERVAL = (0.995922, 1.124078)
