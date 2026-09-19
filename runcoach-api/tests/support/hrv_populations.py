"""T150 -- the two populations T130 measured and no suite pinned, ported out of the harness.

``inter_rows`` and ``switch_away_rows`` are lifted from
``spec/references/T130-overlap-sweep-harness.py`` (lines 775 and 884 at sprint-006 planning)
with their signatures, constants, session-id prefixes, capture hours and values preserved,
so a row this module builds is the row the measurement tables in
``spec/references/T125-fix-form-measurements.md`` §T130(f) were computed over. What changed
is only where they live: a throwaway measurement script can be quoted but cannot go red, and
F006 AC20 requires both populations pinned **before** any candidate selection form is
measured, as the baseline AC21 compares against.

Loaded by ``test_hrv_dataset_populations.py`` by file path (``--import-mode=importlib``
makes nothing under ``tests/`` importable by name), and by nothing else. Every row is a
four-column dict in the shape ``hrv_trend.build_series`` reads and ``db.read_hrv_rows``
returns: ``session_id``, ``start_time`` (an aware UTC ISO instant), ``resting_rmssd_ms``,
``hrv_source_tier``.

**Axes every generator here holds constant**, stated once so each caller can cite them:
capture density is **daily** on every era they build, the values are the harness's fixed
patterns, the hours are fixed per role (06:00 / 07:00 / 08:00 local), the zone is
``Pacific/Auckland``. Density is the axis [[T161]]'s cross-product varies; this module
does not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

AUCK = ZoneInfo("Pacific/Auckland")

#: The harness's target day for the rectangle and the matched pairs.
D = date(2026, 9, 12)
#: ``trial_then_switch``'s ``base`` in ``test_hrv_trend_reset.py``, the target for the
#: switch-away rows.
SWITCH_BASE = date(2026, 9, 7)

STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"


def local(day: date, hh: int) -> str:
    """The stored ``start_time`` of a capture at local ``hh:00`` on ``day`` in Auckland,
    spelled as UTC the way ``mapping.py`` stores it."""
    return datetime(day.year, day.month, day.day, hh, tzinfo=AUCK).astimezone(UTC).isoformat()


def span(first: date, last: date) -> list[date]:
    """Every local day of the closed interval ``[first, last]``."""
    out = []
    d = first
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


def _alt(days: list[date]) -> list[float]:
    """The harness's ordinary-athlete dispersion: alternating 38/44 ms, wide enough that
    the band floor does not fire."""
    return [38.0 if i % 2 == 0 else 44.0 for i in range(len(days))]


def _row(session_id: str, day: date, hh: int, value: float, tier: str) -> dict:
    return {
        "session_id": session_id,
        "start_time": local(day, hh),
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


# --- the intermediate population -------------------------------------------

#: Era end and week shape are held fixed; only ``era_len`` and the mode vary.
INTER_ERA_END = 43  # the era's last day is ``D - 43``
INTER_W0 = 2  # the strap's first judged-week day is ``D - 2`` (resumed)


def inter_rows(era_len: int, mode: str, c: int = 0) -> list[dict]:
    """A matched pair at one era length (harness line 775, verbatim in shape).

    ``resumed``  -- carrier daily to ``D-3+c``, strap ``D-2, D-1, D``: the athlete put
                    the strap back on and (for ``c > 0``) kept the watch on. Truth: the
                    week is his own strap mornings; judging it on the carrier's is the
                    forbidden flip.
    ``abandoned`` -- carrier daily to ``D``, strap ``D-6, D-4, D-2``: the July-trial
                    shape. Truth: the carrier owns the week and its verdict stands;
                    withholding is a false withhold.

    Both carry the *same* strap era (``era_len`` days ending ``D-43`` at 79 ms), the same
    number of strap week days (3, at 25 ms), and a per-tier silence within 4 days of each
    other. An older daily snapshot runs from ``D-199`` to the day before the era, so the
    carrier has a history on both sides of it. Era length cannot tell the two modes
    apart; the question is whether anything conjoined with it can.

    ``c`` is **carrier days elapsed inside the return** at daily density, so days and
    captures coincide; it reaches only the ``resumed`` mode (the abandoned carrier
    already runs to ``D``).
    """
    if mode not in ("resumed", "abandoned"):
        raise ValueError(f"mode must be 'resumed' or 'abandoned', got {mode!r}")
    out = []
    era_last = D - timedelta(days=INTER_ERA_END)
    era_first = era_last - timedelta(days=era_len - 1)
    for d in span(era_first, era_last):
        out.append(_row("E-" + str(d), d, 6, 79.0, STRAP))
    if mode == "resumed":
        carrier_last = D - timedelta(days=3) + timedelta(days=c)
        week = [D - timedelta(days=INTER_W0), D - timedelta(days=1), D]
    else:
        carrier_last = D
        week = [D - timedelta(days=6), D - timedelta(days=4), D - timedelta(days=2)]
    c_days = span(era_last + timedelta(days=1), carrier_last)
    for d, v in zip(c_days, _alt(c_days)):
        out.append(_row("C-" + str(d), d, 7, v, SNAPSHOT))
    older = span(D - timedelta(days=199), era_first - timedelta(days=1))
    for d, v in zip(older, _alt(older)):
        out.append(_row("O-" + str(d), d, 7, v, SNAPSHOT))
    for d in week:
        out.append(_row("W-" + str(d), d, 6, 25.0, STRAP))
    return out


#: The 17 era lengths T130's ``matched`` table was computed over.
INTER_LENS = tuple(range(10, 95, 5))


# --- the athlete who left the tier -----------------------------------------


def switch_away_rows(era_len: int, stray_offsets: tuple[int, ...], new_from: int = 39) -> list[dict]:
    """``trial_then_switch``'s shape with the old tier's era length and its judged-week
    capture days as free axes (harness line 884, verbatim in shape).

    A daily ``health_snapshot`` era of ``era_len`` days ending ``base - 40`` (alternating
    38/44 ms at 06:00); a genuine switch to a daily ``chest_strap_raw`` from
    ``base - new_from`` through ``base`` at 07:00 -- 40 ms to ``base-7``, 33 ms through the
    judged week, so the strap covers the week outright and the week is suppressed against
    the strap's own era; and one 40 ms snapshot capture at 08:00 on each of
    ``stray_offsets`` days before ``base``.

    **Truth: this is not a return.** The athlete left the snapshot; the strap owns the
    week and its verdict stands. Withholding here is a false withhold. The ``(4, 3, 2)``
    row is the placement the reset suite pins
    (``test_the_band_does_not_step_...``, ``test_a_third_old_tier_capture_...``); every
    other placement in ``SWITCH_STRAYS`` was unpinned until T150.
    """
    base = SWITCH_BASE
    out = []
    old_last = base - timedelta(days=40)
    old_first = old_last - timedelta(days=era_len - 1)
    o_days = span(old_first, old_last)
    for d, v in zip(o_days, _alt(o_days)):
        out.append(_row("O-" + str(d), d, 6, v, SNAPSHOT))
    for d in span(base - timedelta(days=new_from), base):
        out.append(_row("N-" + str(d), d, 7, 40.0 if d <= base - timedelta(days=7) else 33.0, STRAP))
    for n in stray_offsets:
        d = base - timedelta(days=n)
        out.append(_row("S-" + str(d), d, 8, 40.0, SNAPSHOT))
    return out


#: The seven stray placements and eight era lengths of T130's switch-away table.
SWITCH_STRAYS = ((6, 5, 4), (5, 4, 3), (4, 3, 2), (3, 2, 1), (2, 1, 0), (6, 3, 0), (2, 1, 0, 3))
SWITCH_LENS = (14, 15, 20, 25, 29, 30, 50, 87)
