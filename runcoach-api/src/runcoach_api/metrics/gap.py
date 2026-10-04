"""Minetti's cost of gradient and the grade adjustment factor (F013 AC2, spec/03 section 3.3.2).

This is the pure half of grade-adjusted pace: the published cost polynomial
``C``, the adjustment factor ``g = C(i) / C(0)`` and the explicit clamp seam
``clamp_grade``. It takes no records, so the session transform and E005's
course stage (spec/01 section 1.4.4) reuse it unchanged.

**Pure, by design.** It imports neither ``config`` nor ``db`` nor ``fastapi``;
the caller brings a grade and gets a number or a ``ValueError``.

**Only where Minetti measured.** Minetti et al. (2002) fitted the polynomial
on treadmill grades of -0.45..+0.45 (10 runners). Outside that range it is
extrapolation, and bad extrapolation: C(-1.0) = -112, so a speed scaled by it
would come out negative. ``g`` therefore **refuses** any grade outside
``[-GRADE_DOMAIN, GRADE_DOMAIN]``, and refuses ``nan`` and ``inf``, rather than
clamping silently. The caller decides visibly through ``clamp_grade``, which
returns both the clamped grade and whether it clamped, so the session
transform can count clamped segments and flag them (F013 Decision Log,
2026-10-03: a clamp hidden inside g would have left E005 unprotected).

The finiteness check in ``g`` comes **before** the domain check on purpose:
``abs(nan) > 0.45`` is ``False``, so a domain check alone would let ``nan``
through and return ``nan``. ``clamp_grade`` rejects a non-finite grade for the
same reason -- a grade of ``nan`` has no clamped value. The domain boundary is
inclusive: exactly +-0.45 is in range and is not clamped.
"""

from __future__ import annotations

import math

GRADE_DOMAIN = 0.45
"""Minetti et al. (2002) measured -0.45..+0.45; spec/03 section 3.3.2."""

_FLAT_COST = 3.6
"""The published polynomial's intercept, C(0), kept as the normaliser (F013 reference, section 11)."""


def C(i: float) -> float:  # the spec's name for the cost polynomial
    """Minetti's cost of running at grade ``i``, in J/(kg*m).

    The raw polynomial, with no domain guard: ``g`` is the guarded entry point.
    """
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + _FLAT_COST


def g(i: float) -> float:
    """The grade adjustment factor ``C(i) / C(0)``, defined only on Minetti's measured domain.

    Raises ``ValueError`` for a grade outside ``[-GRADE_DOMAIN, GRADE_DOMAIN]``
    and for ``nan`` or ``inf``. Nothing is clamped here; see ``clamp_grade``.
    """
    if not math.isfinite(i) or abs(i) > GRADE_DOMAIN:
        raise ValueError(
            f"grade {i!r} is outside Minetti's measured domain [-{GRADE_DOMAIN}, {GRADE_DOMAIN}]"
        )
    return C(i) / _FLAT_COST


def clamp_grade(i: float) -> tuple[float, bool]:
    """Clamp a grade into Minetti's measured domain, saying whether it clamped.

    Returns ``(clamped_i, was_clamped)``. The boundary is inclusive, so exactly
    +-0.45 is returned unchanged with ``False``. A non-finite grade raises
    ``ValueError``: it has no clamped value, and silently mapping it to an
    edge would hide a broken input.
    """
    if not math.isfinite(i):
        raise ValueError(f"grade {i!r} is not finite")
    if i > GRADE_DOMAIN:
        return GRADE_DOMAIN, True
    if i < -GRADE_DOMAIN:
        return -GRADE_DOMAIN, True
    return i, False
