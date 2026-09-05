"""Resting rMSSD -- the statistic, on its own.

Deliberately separate from ``hrv_classification.py``, mirroring the
existing seam between ``rr_reconstruction.py`` (a pure computation over
the beat stream) and ``quality_gates.py`` (the policy that consumes it).
This module knows about RR intervals and arithmetic; it knows nothing
about tiers, routing, quality flags or the ``Session`` shape. Keeping
the two disjoint is what lets the Tier-1 computation be built without
queueing behind the Tier-1 discriminator.

**The adjacency rule this module exists to enforce** (F004 reference
document §4): the successive differences must be walked *pairwise over
the original reconstructed series*, a pair contributing only when
neither of its two beats is flagged ``is_artefact``. Filtering the
flagged beats out first and differencing the compacted list is a
different -- and wrong -- statistic: it forms a difference between two
beats that were never adjacent, manufacturing a measurement that does
not exist. It interpolates nothing, so a "do not interpolate" guard
does not catch it. Measured divergence on a synthetic resting series
with one 6-beat doubled burst: 74.98 (pairwise, correct) vs 76.43
(compact-then-diff) against a true 75.39.

T038 fixes only this seam -- the module, its import path and the
``resting_rmssd`` signature -- so that the F004 walking skeleton is
wired end to end. The arithmetic above is T042's; until it lands
``resting_rmssd`` reports "no value available" for every input, which
is exactly what a Tier-2-only build should see.
"""

from __future__ import annotations

from runcoach_api.models import RRInterval


def resting_rmssd(rr_intervals: list[RRInterval]) -> float | None:
    """Root mean square of successive differences over a resting capture,
    in milliseconds, or ``None`` when no value can be derived.

    ``None`` -- not ``0.0`` -- is the "no value" answer, matching the
    ``rr_valid_fraction`` convention documented on ``models.Session``:
    zero is a real measurement, absence is not.

    T038 ships the seam without the arithmetic (see the module
    docstring); every input therefore yields ``None`` today. T042
    supplies the pairwise computation and the tests that pin it to the
    adjacency rule.
    """
    return None
