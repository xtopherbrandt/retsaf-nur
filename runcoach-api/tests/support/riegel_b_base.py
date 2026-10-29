"""``tests/support/riegel_b_base.py``: the Riegel-b fit as ``metrics/riegel_b.py`` computed it at the
base commit (eebe91c, the sprint-022 release), frozen.

FROZEN AT THE BASE COMMIT. This file is never edited to make a test pass. It is the reference side
of ``test_riegel_b_byte_identity.py``, which pins ``riegel_b.estimate`` and the ``riegel_exponent``
entry of ``GET /state`` bit-exact (by ``float.hex()``) against it, in the same process, so a
refactor of the fit changes no served float. A change in the estimator that this file disagrees
with is a change in what is served, and the pin is meant to go red.

The arithmetic below is ``estimate``'s fit block (from the bar check through the residuals, the
error and t) copied verbatim: the same expressions in the same order over the same operands. Only
``math`` and the estimator's constants and two pure helpers (``effective_count``, ``t_multiplier``,
the t table lookup) are imported; nothing of the fit's arithmetic is.

``base_fit`` takes the efforts the fit read, as ``(d_eq_m, duration_s, weight)`` triples in the
estimator's effort order (start time, then session id), and returns a dict with the served figures
``value``, ``error``, ``interval_low``, ``interval_high``, ``t_multiplier``, ``residuals`` (a list
of ``exp(r) - 1`` or of ``None``) and the flags ``unavailable``, ``population_default``,
``provisional``, ``confidence``, ``below_one``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from runcoach_api.metrics.riegel_b import (
    COUNT_SLACK,
    ESTIMATE_NOT_FINITE,
    HIGH_MAX_ERROR,
    MEDIUM_MAX_ERROR,
    MIN_RACES,
    MIN_SPREAD,
    POPULATION_B,
    POPULATION_ERROR,
    Z,
    effective_count,
    t_multiplier,
)


def _label(error: float) -> str:
    if error <= HIGH_MAX_ERROR:
        return "high"
    if error <= MEDIUM_MAX_ERROR:
        return "medium"
    return "low"


def _all_finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def base_fit(efforts: Iterable[tuple[float, float, float]]) -> dict:
    """The base commit's fit over ``efforts`` as ``(d_eq_m, duration_s, weight)`` triples."""
    used = [(float(d_eq), float(duration), float(weight)) for d_eq, duration, weight in efforts]

    def unavailable() -> dict:
        return {
            "value": None,
            "interval_low": None,
            "interval_high": None,
            "error": None,
            "t_multiplier": None,
            "confidence": None,
            "provisional": False,
            "population_default": False,
            "below_one": False,
            "unavailable": ESTIMATE_NOT_FINITE,
            "residuals": [None] * len(used),
        }

    distances = [d_eq for d_eq, _duration, _weight in used]
    if not _all_finite(*distances) or not all(d_eq > 0 for d_eq in distances):
        return unavailable()  # an underflow to 0 has no finite logarithm either
    weighted = [item for item in used if item[2] > 0]
    served = [d_eq for d_eq, _duration, _weight in weighted]
    n_eff = effective_count([weight for _d_eq, _duration, weight in weighted])
    if n_eff + COUNT_SLACK < MIN_RACES or max(served) / min(served) < MIN_SPREAD:
        return {
            "value": POPULATION_B,
            "interval_low": POPULATION_B - Z * POPULATION_ERROR,
            "interval_high": POPULATION_B + Z * POPULATION_ERROR,
            "error": POPULATION_ERROR,
            "t_multiplier": Z,
            "confidence": "low",
            "provisional": True,
            "population_default": True,
            "below_one": False,
            "unavailable": None,
            "residuals": [None] * len(used),
        }

    w = [weight for _d_eq, _duration, weight in used]
    x = [math.log(d_eq) for d_eq in distances]
    y = [math.log(duration) for _d_eq, duration, _weight in used]
    total = sum(w)
    x_w = sum(wi * xi for wi, xi in zip(w, x, strict=True)) / total
    y_w = sum(wi * yi for wi, yi in zip(w, y, strict=True)) / total
    sxx = sum(wi * (xi - x_w) ** 2 for wi, xi in zip(w, x, strict=True))
    sxy = sum(wi * (xi - x_w) * (yi - y_w) for wi, xi, yi in zip(w, x, y, strict=True))
    b = sxy / sxx
    r = [yi - (y_w + b * (xi - x_w)) for xi, yi in zip(x, y, strict=True)]
    # The weighted standard error over the weights rescaled to sum to n_eff, w' = w * n_eff / sum(w):
    # sum(w' r^2) and sum(w' (x - x_w)^2) both carry the factor n_eff / sum(w), which cancels, so
    # se = sqrt(sum(w r^2) / (n_eff - 2) / sxx); b and the residuals do not move with the scale.
    error = math.sqrt(sum(wi * ri * ri for wi, ri in zip(w, r, strict=True)) / (n_eff - 2) / sxx)
    t = t_multiplier(math.floor(n_eff + COUNT_SLACK) - 2)  # the bar's own reading, so at least 1
    low, high = b - t * error, b + t * error
    try:
        residuals = [math.expm1(ri) for ri in r]
    except OverflowError:
        return unavailable()
    if not _all_finite(b, error, low, high, *residuals):
        return unavailable()
    return {
        "value": b,
        "interval_low": low,
        "interval_high": high,
        "error": error,
        "t_multiplier": t,
        "confidence": _label(error),
        "provisional": False,
        "population_default": False,
        "below_one": b < 1,
        "unavailable": None,
        "residuals": list(residuals),
    }
