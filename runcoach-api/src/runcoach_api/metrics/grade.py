"""The gradient window over reconstructed distance (F013 AC3, spec/03 section 3.3.1 as amended).

For each counted segment of a ``segments.TimeBase`` this module reports the
**raw** grade ``i`` over a window of ``+-GRADE_HALF_WIDTH_M`` of reconstructed
distance ``s`` around the segment's midpoint, or ``None`` when the segment has
no grade. The rule, from the F013 reference (section 3):

    m = (s[k] + s[k+1]) / 2
    window = every record j with altitude present and m - w <= s[j] <= m + w
    i = (altitude[l] - altitude[f]) / (s[l] - s[f])     f, l = first, last window record

Fewer than two window records, or ``s[l] <= s[f]``, means **no grade**; so
does a segment that is not counted (a break or a dt = 0 duplicate). The
window clips one-sided at the session's ends. It walks ``s``, never the raw
``distance`` and never time, so a pause (``s`` does not advance across a
break) and a distance regression (which contributes 0 m) cannot make it
non-contiguous: both sides of a pause sit at one ``s`` and fall in one window.

**Why a distance window and not the gate's 3-sample smoothing window.** Every
fixture's altitude is quantized at 0.2 m, so a grade taken over three samples
reaches |i| = 4.13 on real data, far outside Minetti's measured domain. A
4 m step inside 3 m of distance is *diluted* over about 50 m here (|i| <= 0.09)
rather than clamped or dropped. The altitude is already the gate's smoothed
stream and is not re-smoothed here.

**What this module does not do.** It does not clamp and does not call
``gap.g``: the session transform clamps through ``gap.clamp_grade`` and counts
what it clamped, so the clamp stays a visible seam. It does not window on
record count, which is why the reference's measured ratios are approximations
of this rule rather than its output.

**Pure, by design.** It reads only ``tb.records[j].altitude``, ``tb.s`` and
``tb.segments[k].counted``; a non-finite altitude never reaches it because
``segments.screen`` has already made it absent. The sweep is two pointers over
the monotone ``s``, O(n): midpoints are non-decreasing in ``k``, so both window
edges only move forward.
"""

from __future__ import annotations

import math

from runcoach_api.metrics.segments import TimeBase

# The half-width of the gradient window, in metres of reconstructed distance:
# spec/03 section 3.3.1 as amended, a heuristic default ratified in F013's Decision Log
# ("gradient over +-25 m (heuristic default)"; 3 samples and +-50 m were rejected).
GRADE_HALF_WIDTH_M = 25.0


def segment_grades(tb: TimeBase, half_width_m: float = GRADE_HALF_WIDTH_M) -> list[float | None]:
    """One entry per segment of ``tb.segments``: the raw grade, or ``None`` when there is no grade.

    A segment has no grade when it is not counted, when fewer than two records
    with altitude present lie within ``half_width_m`` of its midpoint on ``s``,
    or when those records span no distance (``s[l] <= s[f]``, as when the
    runner stands still). Nothing is clamped here. ``half_width_m`` must be a
    positive finite number.
    """
    if not math.isfinite(half_width_m) or half_width_m <= 0.0:
        raise ValueError(f"half_width_m must be positive and finite, got {half_width_m!r}")

    s = tb.s
    # (s, altitude) of every record with altitude present, in record order, so s is non-decreasing.
    present = [(s[j], rec.altitude) for j, rec in enumerate(tb.records) if rec.altitude is not None]
    n = len(present)
    first = 0  # index into ``present``: the first record with s >= m - w
    end = 0  # index into ``present``: one past the last record with s <= m + w

    grades: list[float | None] = []
    for seg in tb.segments:
        if not seg.counted:
            grades.append(None)
            continue
        m = (s[seg.k] + s[seg.k + 1]) / 2
        low, high = m - half_width_m, m + half_width_m
        while first < n and present[first][0] < low:
            first += 1
        while end < n and present[end][0] <= high:
            end += 1
        if end - first < 2:
            grades.append(None)
            continue
        s_f, alt_f = present[first]
        s_l, alt_l = present[end - 1]
        ds = s_l - s_f
        if ds <= 0.0:
            grades.append(None)
            continue
        grades.append((alt_l - alt_f) / ds)
    return grades
