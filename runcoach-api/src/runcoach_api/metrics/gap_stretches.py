"""A run's best GAP stretch at each length (F033, reference section 3): the input of the critical
speed fit.

**Pure, by design**, on the pattern of ``metrics/riegel_b.py``: ``best_stretches`` takes the list
``session_features.segment_rows`` returns (counted segments only, in time order) and returns frozen
dataclasses; it imports neither ``config`` nor ``db`` nor ``fastapi``. Each segment is read by key:
``t_start``, ``dt``, ``v_actual``, ``g`` and ``moving_gap``.

The rules:

- A segment's GAP distance is ``v_actual * g * dt``.
- For each length L, the best stretch is the largest GAP distance over a window of consecutive
  segments whose summed ``dt`` first reaches L, scaled to exactly L (``D * L / T``). Ties go to the
  earlier window.
- A window is not eligible when it holds a segment with ``moving_gap`` true (distance across a moving
  gap is not trusted), or when any of its segments after the first starts more than
  ``pause_break_s`` after the previous one ended (``t_start - (prev.t_start + prev.dt) >
  pause_break_s``): a pause. A pause of ``pause_break_s`` or less is spanned, and its time is not
  counted, since only the segments' ``dt`` is summed.
- A segment whose ``t_start``, ``dt``, ``v_actual`` or ``g`` is not a finite number (read through
  ``metrics.finite``), or whose ``dt`` is not positive, is treated as a moving gap: no window holding
  it is eligible, and it enters the running sums as zero, so it never voids a later window.
- A run with no eligible window reaching L (a run whose counted time is under L among them) has no
  key L.

One two-pointer pass per length over prefix sums of ``dt`` (``T``) and GAP distance (``D``), with
prefix counts of moving-gap segments (``B``) and of breaks (``Q``, counted on the later segment of
the pair): the window ``i..j`` is eligible when ``B[j+1] - B[i] == 0`` and ``Q[j+1] - Q[i+1] == 0``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from runcoach_api.metrics.finite import finite

#: The stretch lengths read, in seconds (reference section 1).
STRETCH_LENGTHS_S: tuple[int, ...] = (120, 180, 300, 480, 720, 1200)
#: A gap between segments longer than this, in seconds, is a pause a stretch never spans.
PAUSE_BREAK_S = 10.0


@dataclass(frozen=True)
class BestStretch:
    """A run's best stretch at one length.

    ``distance_m`` is the window's GAP distance scaled to exactly ``length_s``; ``counted_s`` is the
    window's summed ``dt`` (at least ``length_s``); ``first_t_start`` is its first segment's
    ``t_start``.
    """

    length_s: int
    distance_m: float
    counted_s: float
    first_t_start: float


def best_stretches(
    segments: Sequence[Mapping[str, object]],
    lengths: Iterable[int] = STRETCH_LENGTHS_S,
    pause_break_s: float = PAUSE_BREAK_S,
) -> dict[int, BestStretch]:
    """The best stretch at each of ``lengths`` over ``segments``, keyed by length (module docstring)."""
    n = len(segments)
    T = [0.0] * (n + 1)
    D = [0.0] * (n + 1)
    B = [0] * (n + 1)
    Q = [0] * (n + 1)
    prev_end: float | None = None
    for k, seg in enumerate(segments):
        t_start = finite(seg["t_start"])
        dt = finite(seg["dt"])
        v = finite(seg["v_actual"])
        g = finite(seg["g"])
        bad = t_start is None or dt is None or v is None or g is None or dt <= 0.0
        if bad:
            T[k + 1] = T[k]
            D[k + 1] = D[k]
            B[k + 1] = B[k] + 1
            Q[k + 1] = Q[k]
            prev_end = None
            continue
        broken = prev_end is not None and t_start - prev_end > pause_break_s
        T[k + 1] = T[k] + dt
        D[k + 1] = D[k] + v * g * dt
        B[k + 1] = B[k] + (1 if seg["moving_gap"] else 0)
        Q[k + 1] = Q[k] + (1 if broken else 0)
        prev_end = t_start + dt

    out: dict[int, BestStretch] = {}
    for length in lengths:
        best: BestStretch | None = None
        e = 0  # the window i..e-1: the smallest e whose T[e] - T[i] reaches the length
        for i in range(n):
            e = max(e, i + 1)
            while e <= n and T[e] - T[i] < length:
                e += 1
            if e > n:
                break
            if B[e] - B[i] or Q[e] - Q[i + 1]:
                continue
            counted = T[e] - T[i]
            distance = (D[e] - D[i]) * length / counted
            if best is None or distance > best.distance_m:
                best = BestStretch(length, distance, counted, float(segments[i]["t_start"]))
        if best is not None:
            out[length] = best
    return out
