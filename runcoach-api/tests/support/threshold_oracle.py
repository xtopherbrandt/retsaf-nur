"""A threshold-pace oracle outside the code: the F024 reference's filter in information form, nothing else.

Written from the F024 reference (section 1's constants, section 3's filter, section 4's worked
example), not from ``metrics.threshold_pace``. It imports nothing from ``runcoach_api`` and
``test_threshold_pace_estimator.py`` pins that by reading ``sys.modules`` after the import, so the
expected values it produces cannot share code with the module they check
(``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``).

It takes the qualifying efforts already chosen, as ``(day, gap_pace_s_per_km)`` pairs with ``day``
a fractional day count from any origin, and ``as_of_day`` on the same axis; it does not qualify
anything. The filter is restated in **information form** rather than the reference's gain form,
so a slip in the estimator's ``K`` arithmetic cannot be copied here:

- ``y = ln(1000 / pace)``, log speed in m/s; ``R = 0.025 ** 2``; ``Q = 3 R / 42`` per day.
- The first effort: ``x = y``, ``P = R``.
- Each later effort ``dt`` days after the previous: the prior variance is ``P + Q dt``; the
  posterior information is the sum of the prior's and the observation's,
  ``1 / P' = 1 / (P + Q dt) + 1 / R``; the posterior mean weights each by its information,
  ``x' = P' (x / (P + Q dt) + y / R)``. The gain it entered with is ``P' / R`` (the observation's
  share of the posterior information), which equals the reference's ``K``.
- At ``as_of_day``: ``P + Q dt``.
- Pace ``1000 / exp(x)``; error ``sqrt(P)``; 80 % interval ``1000 / exp(x +- z sqrt(P))`` with
  ``z`` the 0.9 quantile of the standard normal; label ``high`` while ``P <= R``, ``medium`` while
  ``P <= 4 R``, ``low`` above.

``WORKED_EXAMPLE`` is the reference's section 4 table typed in by hand, for the tests to compare
both the oracle and the estimator with.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

R = 0.025**2
Q_PER_DAY = 3 * R / 42
Z = NormalDist().inv_cdf(0.9)


@dataclass(frozen=True)
class OracleResult:
    pace_s_per_km: float
    error: float
    interval_fast: float
    interval_slow: float
    label: str
    gains: tuple[float, ...]
    variance: float


def replay(efforts: list[tuple[float, float]], as_of_day: float) -> OracleResult:
    """The estimate at ``as_of_day`` from ``(day, pace)`` efforts, all at or before it."""
    ordered = sorted(efforts)
    if not ordered:
        raise ValueError("the oracle needs at least one effort")
    gains: list[float] = []
    x = math.log(1000 / ordered[0][1])
    variance = R
    gains.append(1.0)
    last_day = ordered[0][0]
    for day, pace in ordered[1:]:
        prior = variance + Q_PER_DAY * (day - last_day)
        information = 1 / prior + 1 / R
        posterior = 1 / information
        x = posterior * (x / prior + math.log(1000 / pace) / R)
        gains.append(posterior / R)
        variance = posterior
        last_day = day
    variance = variance + Q_PER_DAY * (as_of_day - last_day)
    spread = Z * math.sqrt(variance)
    if variance <= R:
        label = "high"
    elif variance <= 4 * R:
        label = "medium"
    else:
        label = "low"
    return OracleResult(
        pace_s_per_km=1000 / math.exp(x),
        error=math.sqrt(variance),
        interval_fast=1000 / math.exp(x + spread),
        interval_slow=1000 / math.exp(x - spread),
        label=label,
        gains=tuple(gains),
        variance=variance,
    )


# The reference's section 4 table: (efforts as (day, pace), as_of day) -> (pace, error %, fast, slow, label).
A = (0.0, 270.0)
B = (42.0, 260.0)
WORKED_EXAMPLE = {
    "right after A": (([A], 0.0), (270.00, 2.500, 261.49, 278.79, "high")),
    "A + 42 days, before B": (([A], 42.0), (270.00, 5.000, 253.24, 287.87, "medium")),
    "right after B": (([A, B], 42.0), (261.97, 2.236, 254.57, 269.59, "high")),
    "B + 30 days": (([A, B], 72.0), (261.97, 4.289, 247.96, 276.77, "medium")),
    "B + 90 days": (([A, B], 132.0), (261.97, 6.722, 240.35, 285.54, "low")),
    "A and B on the same instant": (([A, (0.0, 260.0)], 0.0), (264.95, 1.768, 259.02, 271.02, "high")),
}
