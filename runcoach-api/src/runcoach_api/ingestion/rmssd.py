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

T038 fixed only this seam -- the module, its import path and the
``resting_rmssd`` signature -- so that the F004 walking skeleton was
wired end to end. T042 supplies the arithmetic described above; the
adjacency rule and the ``None``/``0.0`` distinction are pinned by
``tests/test_resting_rmssd.py``.
"""

from __future__ import annotations

import itertools
import math

from runcoach_api.models import RRInterval


def resting_rmssd(rr_intervals: list[RRInterval]) -> float | None:
    """Root mean square of successive differences over a resting capture,
    in milliseconds, or ``None`` when no value can be derived.

    The walk is strictly pairwise over ``rr_intervals`` **as given**.
    ``rr_reconstruction.reconstruct()`` flags rather than excises and
    emits one beat per candidate in original merge order, so list order
    already *is* series adjacency: there is nothing to compact, and
    nothing to re-sort either -- ``seq`` is ``int | None``, so sorting
    on it would invite a subtle reorder for no gain.

    A pair contributes ``(curr.rr_ms - prev.rr_ms) ** 2`` only when both
    of its beats are usable, which means unflagged *and* carrying a
    value:

    * ``is_artefact`` is ``bool | None``, and only ``True`` excludes --
      a beat left ``None`` was never judged an artefact, so it counts.
    * ``rr_ms`` is ``float | None``, and a null beat is non-contributing
      rather than a ``TypeError``, exactly like a flagged one.
    * a **non-finite** ``rr_ms`` is non-contributing for the same reason. It
      is not a measurement, and it is not merely its own pair's problem: a
      single ``inf`` or ``nan`` propagates through the sum and poisons the
      whole capture's rMSSD. ``rr_reconstruction._out_of_band`` flags an
      ``inf`` but *not* a ``nan`` -- every comparison against a NaN is false,
      so it is never judged an artefact upstream and would otherwise arrive
      here unflagged.

    ``None`` -- not ``0.0`` -- is the "no value" answer, matching the
    ``rr_valid_fraction`` convention documented on ``models.Session``:
    zero is a real measurement, absence is not. So a series yielding
    fewer than one contributing pair (empty, a lone beat, every beat
    flagged, or flags placed so that every pair touches one) returns
    ``None``, while a clean two-beat series with no variation returns a
    genuine ``0.0``. E003 takes ``ln(rMSSD)``, so that distinction has
    to survive all the way out of this function.

    Pure over its argument: reads only the list handed to it, touches
    neither the DB, the session nor the messages, and mutates nothing.
    """
    squares = [
        (curr.rr_ms - prev.rr_ms) ** 2
        for prev, curr in itertools.pairwise(rr_intervals)
        if _contributes(prev) and _contributes(curr)
    ]

    if not squares:
        return None

    result = math.sqrt(sum(squares) / len(squares))

    # The contract this function advertises is ``float | None``, where the
    # float is a usable measurement -- and E003 is told it may ``ln()`` the
    # column this feeds. ``_contributes`` already refuses non-finite beats, so
    # this is the backstop for a finite stream whose squares overflow, not a
    # second line of defence against the same input. ``None`` is the honest
    # answer: a value was attempted and no usable one came out.
    if not math.isfinite(result):
        return None

    return result


def _contributes(beat: RRInterval) -> bool:
    """Whether ``beat`` may take part in a successive difference.

    Not an artefact judgement of its own -- detection is
    ``rr_reconstruction``'s job and this only reads its verdict. The
    finiteness test is not a second opinion on that verdict either: it is the
    same "carrying a value" question ``rr_ms is not None`` asks, for a value
    that is present but is not a number.
    """
    return (
        beat.is_artefact is not True
        and beat.rr_ms is not None
        and math.isfinite(beat.rr_ms)
    )
