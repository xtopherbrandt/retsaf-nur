"""A critical-speed oracle outside the code: the F033 reference's fit, restated another way.

Written alongside the estimator, in the same commit, from the F033 reference (section 1's
constants, section 4's steps 4 to 6, section 5's worked examples) rather than by reading
``metrics.critical_speed``. It imports nothing from ``runcoach_api``;
``test_critical_speed_estimator.py`` pins that by parsing this file's imports, so the expected
values it produces cannot share code with the module they check
(``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``). What that is worth,
part by part:

- **Independent cross-checks.** The line (raw sums and Cramer's rule against the estimator's
  centred sums) and Student's t (``riegel_oracle.t_quantile``, an incomplete-beta quantile written
  for an earlier feature) compute the same figures by a different method, so a centring or
  degrees-of-freedom slip in the estimator shows here.
- **Restatements of the reference.** The far-off excess and the label repeat the reference's
  formulas as written, so they catch a slip in transcribing the reference, never a misreading of
  it; only the section 5 figures pin them.
- **Not covered.** The screen, the stretch search, one run per length, the shape check and the
  spread are not in this oracle. The section 5 figures come from the planning script, which uses
  the same window search and selection design as the estimator, so a match with them shows two
  implementations of one design agreeing, not that the design is right.

It takes the kept stretches already chosen, as ``(length_s, distance_m)`` pairs; it selects and
screens nothing.

- **The line** is ordinary least squares of distance on time solved from the 2x2 normal equations
  in raw sums, ``[[n, S_t], [S_t, S_tt]] [D', CS] = [S_d, S_td]``, by Cramer's rule, rather than
  the reference's centred sums, so a slip in the estimator's centring cannot be copied here.
  ``s2 = SSR / (n - 2)``; ``se = sqrt(s2 * n / det)`` and ``se_D = sqrt(s2 * S_tt / det)``, the
  diagonal of ``s2 (X'X)^-1``, which equal the reference's ``sqrt(s2 / Sxx)`` and ``sqrt(s2 (1/n +
  mean_t^2 / Sxx))`` algebraically.
- **Student's t** at 0.90 for ``n - 2`` degrees of freedom is ``riegel_oracle.t_quantile``: the
  regularized incomplete beta function by its continued fraction, solved by bisection. That module
  imports nothing from the app either.
- **The far-off excess** is ``p_long - p_prev * (L_long / L_prev) ** ((b - 1) / b)`` at b 1.06, the
  paces in s/km.
- **The label** counts the kept stretches whose run is tagged ``race``, not admitted as a workout,
  and lasted 1800 s or less: 0 ``low``, 1 ``medium``, 2 or more ``high``; then (F034 reference
  section 1) ``high`` is held at ``medium`` unless one of those counted stretches is 300 s or
  shorter. It returns the label and whether it was held.

``WORKED_EXAMPLES`` is the F033 reference's section 5 typed in by hand, re-pointed and extended
by the F034 reference's section 5.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import dataclass
from pathlib import Path

POPULATION_B = 1.06
SHORT_RACE_MAX_S = 1800
SHORT_END_MAX_S = 300

_RIEGEL_PATH = Path(__file__).parent / "riegel_oracle.py"
if "riegel_oracle" in sys.modules:
    riegel_oracle = sys.modules["riegel_oracle"]
else:
    _SPEC = importlib.util.spec_from_file_location("riegel_oracle", _RIEGEL_PATH)
    riegel_oracle = importlib.util.module_from_spec(_SPEC)
    sys.modules["riegel_oracle"] = riegel_oracle
    _SPEC.loader.exec_module(riegel_oracle)  # type: ignore[union-attr]


@dataclass(frozen=True)
class Line:
    cs: float
    d_prime: float
    se: float
    se_d: float
    t: float
    pace: float
    pace_low: float
    pace_high: float
    d_prime_low: float
    d_prime_high: float


def line(points: list[tuple[float, float]]) -> Line:
    """OLS of ``distance`` on ``time`` over ``(time_s, distance_m)`` points, with the 80 % intervals."""
    n = float(len(points))
    st = sum(t for t, _d in points)
    stt = sum(t * t for t, _d in points)
    sd = sum(d for _t, d in points)
    std = sum(t * d for t, d in points)
    det = n * stt - st * st
    d_prime = (sd * stt - st * std) / det
    cs = (n * std - st * sd) / det
    ssr = sum((d - (d_prime + cs * t)) ** 2 for t, d in points)
    s2 = ssr / (n - 2)
    se = math.sqrt(s2 * n / det)
    se_d = math.sqrt(s2 * stt / det)
    t = riegel_oracle.t_quantile(0.9, len(points) - 2)
    return Line(
        cs=cs,
        d_prime=d_prime,
        se=se,
        se_d=se_d,
        t=t,
        pace=1000.0 / cs,
        pace_low=1000.0 / (cs + t * se),
        pace_high=1000.0 / (cs - t * se),
        d_prime_low=d_prime - t * se_d,
        d_prime_high=d_prime + t * se_d,
    )


def far_off_excess(prev: tuple[float, float], longest: tuple[float, float]) -> float:
    """The longest stretch's pace less the pace the next-longest predicts at b 1.06, s/km."""
    (l_prev, d_prev), (l_long, d_long) = prev, longest
    p_prev = 1000.0 * l_prev / d_prev
    p_long = 1000.0 * l_long / d_long
    exponent = (POPULATION_B - 1.0) / POPULATION_B
    return p_long - p_prev * (l_long / l_prev) ** exponent


def label(points: list[tuple[str | None, bool, float | None, float]]) -> tuple[str, bool]:
    """``(label, held)`` from ``(tag, admitted_as_workout, run duration_s, stretch length_s)`` of
    each kept stretch: the short races counted, and ``high`` held at ``medium`` when no counted
    race fills a stretch of 300 s or less."""
    counted = [
        length
        for tag, admitted, duration, length in points
        if tag == "race" and not admitted and duration is not None and duration <= SHORT_RACE_MAX_S
    ]
    if len(counted) < 2:
        return ("low", "medium")[len(counted)], False
    if min(counted) <= SHORT_END_MAX_S:
        return "high", False
    return "medium", True


# Reference section 5, typed in by hand. Each stretch: (length_s, distance_m, local date, tag,
# name, run duration s, admitted_as_workout, kept). The run durations and session tags are the
# planning re-measure's (cs_planning.out.txt for F033's three, f034_gate_scan.out.txt for
# 2024-11-24); the reference prints the race ones. ``unavailable``, ``label`` and ``held`` are what
# the F034 estimator serves; the line figures are the fit of the kept stretches (on 2026-10-31 the
# fit F033 served, which the 4 s/km cut now refuses: only its excess is served). On 2024-11-24 the
# reference gives CS, D', their intervals and the excess; ``se``, ``se_d`` and ``t`` were computed
# here with ``line`` over its six points.
WORKED_EXAMPLES: dict[str, dict] = {
    "2026-10-31": {
        "stretches": [
            (120, 523.5537014471311, "2026-10-07", "threshold_workout", None, 3498.662, False, True),
            (180, 784.548392069948, "2026-10-16", None, "Speed Repeats", 3519.97, False, True),
            (300, 1243.5530061433337, "2026-09-23", None, None, 2589.237, False, True),
            (480, 1973.681523449879, "2026-10-22", None, "Progression Run", 2971.925, True, True),
            (720, 2905.6868789866103, "2026-08-20", None, "Run", 2186.161, False, True),
            (1200, 4526.608531191341, "2026-09-30", "threshold_workout", None, 3326.564, False, True),
        ],
        "cs": 3.7181285870894323,
        "pace": 268.95250569663716,
        "pace_low": 260.5193987146589,
        "pace_high": 277.94984243407475,
        "d_prime": 133.87437866999107,
        "d_prime_low": 58.94298122682967,
        "d_prime_high": 208.8057761131525,
        "se": 0.07850030789562032,
        "se_d": 48.87235247530557,
        "t": 1.533206274059,
        "excess": 10.03983535916143,
        "unavailable": "longest_far_off",
        "label": None,
        "held": None,
    },
    "2024-11-30": {
        "stretches": [
            (120, 540.973160281942, "2024-10-08", None, "Run", 3232.9, False, True),
            (180, 798.0449387851238, "2024-10-13", "race", "Run", 11138.164, False, True),
            (300, 1289.9311782327204, "2024-09-13", None, "Run", 3826.657, False, True),
            (480, 2016.8934567340232, "2024-10-26", "race", "Run", 1232.761, False, True),
            (720, 3005.017767267197, "2024-09-26", None, "Run", 4900.816, False, True),
            (1200, 5002.6202330825545, "2024-11-09", "race", "Run", 1205.641, False, True),
        ],
        "cs": 4.12266204682288,
        "pace": 242.56172071408272,
        "pace_low": 241.6051418736522,
        "pace_high": 243.52590436267354,
        "d_prime": 47.58243231915321,
        "d_prime_low": 37.42031471420094,
        "d_prime_high": 57.744549924105485,
        "se": 0.010646129500859348,
        "se_d": 6.628017232181781,
        "t": 1.533206274059,
        "excess": -6.754016294736289,
        "unavailable": None,
        "label": "medium",
        "held": True,
    },
    "2025-01-31": {
        "stretches": [
            (120, 546.3600050967008, "2025-01-08", None, "Run", 2453.134, False, True),
            (180, 814.32400631371, "2025-01-16", None, "Run", 2700.402, False, True),
            (300, 1298.6926653138062, "2025-01-29", None, "Run", 2670.361, False, True),
            (480, 2061.841202898636, "2024-12-03", None, "Run", 1980.313, False, True),
            (720, 2831.3273836002936, "2024-11-03", None, "Run", 2342.354, False, False),
            (1200, 5002.6202330825545, "2024-11-09", "race", "Run", 1205.641, False, True),
        ],
        "cs": 4.117817384614028,
        "pace": 242.8470975270634,
        "pace_low": 241.25141196259918,
        "pace_high": 244.46403200937243,
        "d_prime": 67.04289515708456,
        "d_prime_low": 50.66859896768453,
        "d_prime_high": 83.41719134648459,
        "se": 0.016630236458634217,
        "se_d": 9.998078242460206,
        "t": 1.637744353696,
        "excess": -5.320321156985159,
        "unavailable": None,
        "label": "medium",
        "held": False,
    },
    "2024-11-24": {
        "stretches": [
            (120, 545.5606237053062, "2024-10-26", "race", "Run", 1232.761, False, True),
            (180, 798.0449387851238, "2024-10-13", "race", "Run", 11138.164, False, True),
            (300, 1289.9311782327204, "2024-09-13", None, "Run", 3826.657, False, True),
            (480, 2022.988052762574, "2024-08-30", None, "Tempo", 4520.216, False, True),
            (720, 3005.017767267197, "2024-09-26", None, "Run", 4900.816, False, True),
            (1200, 5002.6202330825545, "2024-11-09", "race", "Run", 1205.641, False, True),
        ],
        "cs": 4.1204029285798445,
        "pace": 242.6947114962527,
        "pace_low": 241.86534325693816,
        "pace_high": 243.52978719625918,
        "d_prime": 50.4923346826572,
        "d_prime_low": 41.69592742154184,
        "d_prime_high": 59.288741943772564,
        "se": 0.009215371685769224,
        "se_d": 5.737262761016404,
        "t": 1.5332062740589434,
        "excess": -6.754016294736289,
        "unavailable": None,
        "label": "high",
        "held": False,
    },
}
