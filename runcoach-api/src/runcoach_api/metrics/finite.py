"""The finite-number screen: one reading of "is this a real, finite number".

Pure: imports neither the store nor the configuration nor the web layer. The session time base
(``metrics.segments``), the race-tag matching rule (``race_tags``) and the threshold-pace qualifier
(``metrics.threshold_pace``) read numbers through ``finite``; ``hrv_classification._numeric`` keeps
its own ratified HRV semantics (``segments``' docstring says why).
"""

from __future__ import annotations

import math


def finite(value: object) -> float | None:
    """``value`` as a finite float, or ``None`` for a bool, a string, ``None``, a non-finite number
    or an int beyond float range (``json.loads`` keeps one as an int; ``float`` raises on it)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None
