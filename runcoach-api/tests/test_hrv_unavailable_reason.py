"""T137 -- ``metrics/hrv_trend.py``/``schemas.py``: ``unavailable_reason``.

Closes F005's Negative Class row "the verdict still cannot say *why* it is
unavailable", open since review cycle 4 and re-confirmed at six causes by
T128 (``test_hrv_unavailable_causes.py``). A single enum on the verdict --
``no_tier_sustains_a_trend``, ``no_band``, ``week_too_thin``,
``week_not_representative``, ``baseline_unestablished``, ``day_not_happened``
-- names which of the six fired; null whenever ``verdict`` is not
``hrv_unavailable``.

**The defect this pins is two concrete days, not the mechanism in general**
(the task's own framing): T132's clean, gapless, permanent device-switch
geometry reads ``hrv_unavailable`` on 2026-09-03 and 09-04 while every field
the pre-T137 response exposed -- ``established``, ``baseline.n``,
``readings_in_window``, ``reset_reason`` -- said the week was judgeable. The
point of the new field is that it **distinguishes** those two opaque days
from 09-05, where ``readings_in_window`` already told the story; a test that
only asserted "non-null" could not tell an enum from a bare boolean.

``judge``'s guards fire in a fixed order (its own docstring), and this
module's ``_unavailable_reason`` reads exactly the values ``judge`` computed,
so T128's independent AST oracle is the authority on the six-cause set: if
this module and that oracle ever disagree about the count or the gating, the
oracle wins (module docstring, ``hrv_trend.py``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from runcoach_api.metrics import hrv_trend

AUCKLAND = ZoneInfo("Pacific/Auckland")

STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"

R = hrv_trend
NO_TIER = R.REASON_NO_TIER
NO_BAND = R.REASON_NO_BAND
WEEK_TOO_THIN = R.REASON_WEEK_TOO_THIN
WEEK_NOT_REPRESENTATIVE = R.REASON_WEEK_NOT_REPRESENTATIVE
BASELINE_UNESTABLISHED = R.REASON_BASELINE_UNESTABLISHED
DAY_NOT_HAPPENED = R.REASON_DAY_NOT_HAPPENED


# ---------------------------------------------------------------------------
# row builders (the same shapes as test_hrv_trend_reset.py; duplicated rather
# than imported across test modules -- contract-tables-need-an-independent-oracle)
# ---------------------------------------------------------------------------


def local(day: date, hh: int = 6, mm: int = 0, zone: ZoneInfo = AUCKLAND) -> str:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC).isoformat()


def row(start_time: str, tier: str | None, value: float | None, session_id: str | None = None) -> dict:
    return {
        "session_id": session_id or f"s-{start_time}-{tier}",
        "start_time": start_time,
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


def readings(tier: str, days: list[date], value: float = 40.0, prefix: str | None = None) -> list[dict]:
    return [row(local(day, 6), tier, value, f"{prefix}-{day}" if prefix else None) for day in days]


def span(first: date, last: date) -> list[date]:
    days = []
    day = first
    while day <= last:
        days.append(day)
        day += timedelta(days=1)
    return days


def build(rows: list[dict], target: date) -> hrv_trend.HrvSeries:
    return hrv_trend.build_series(rows, AUCKLAND, target)


# ---------------------------------------------------------------------------
# the two opaque days
# ---------------------------------------------------------------------------


def test_unavailable_reason_separates_the_opaque_withhold_days_from_the_thin_window_day() -> None:
    """The task's own geometry, verbatim (Pacific/Auckland): daily
    ``chest_strap_raw`` at 40.0 ms from 2026-01-01 to 2026-08-31, daily
    ``health_snapshot`` at 40.0 ms from 2026-09-01 on -- no gap, no
    suppression, identical values on both tiers. The athlete bought a new
    watch.

    At D = 2026-09-03 and 09-04 ``chest_strap_raw`` resolves (it still covers
    its judged week with >= ``MIN_WINDOW_READINGS`` days), the baseline is
    established at 60, and the verdict is ``hrv_unavailable`` solely because
    ``health_snapshot`` -- a tier never used in the baseline window -- holds a
    full judged week entirely after every one of ``chest_strap_raw``'s own
    (T125/T132's ``verdict_withheld``). Nothing else in the pre-T137 response
    said why. At 09-05 the resolved tier's own window has fallen to 2 days,
    below ``MIN_WINDOW_READINGS`` -- already explicable by
    ``readings_in_window`` alone, and the new field must report a
    **different** reason there, or it is not distinguishing anything.
    """
    strap_days = span(date(2026, 1, 1), date(2026, 8, 31))
    snapshot_days = span(date(2026, 9, 1), date(2026, 9, 10))
    rows = readings(STRAP, strap_days, 40.0, "strap") + readings(SNAPSHOT, snapshot_days, 40.0, "snap")

    d03 = hrv_trend.judge(build(rows, date(2026, 9, 3)))
    d04 = hrv_trend.judge(build(rows, date(2026, 9, 4)))
    d05 = hrv_trend.judge(build(rows, date(2026, 9, 5)))

    for label, verdict, expected_window in (("09-03", d03, 4), ("09-04", d04, 3)):
        assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE, label
        assert verdict.established is True, label
        assert verdict.baseline_n == 60, label
        assert verdict.readings_in_window == expected_window, label
        assert verdict.readings_in_window >= hrv_trend.MIN_WINDOW_READINGS, label
        # The opacity itself: every other exposed field says the week is
        # judgeable, and only the new field explains the silence.
        assert verdict.unavailable_reason == WEEK_NOT_REPRESENTATIVE, (label, verdict.unavailable_reason)

    assert d05.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert d05.readings_in_window == 2
    # Distinguished, not merely non-null: 09-05's reason is the ordinary
    # thin-window one, not the opaque withheld one 09-03/09-04 report.
    assert d05.unavailable_reason == WEEK_TOO_THIN, d05.unavailable_reason
    assert d05.unavailable_reason != WEEK_NOT_REPRESENTATIVE


def test_unavailable_reason_reports_the_withheld_week_before_an_unestablished_baseline() -> None:
    """Precedence, pinned directly: when a week is **both** withheld
    (T125/T132) **and** built on an unestablished baseline, ``judge``'s fixed
    order reports the withheld reason -- the ``and not series.withheld``
    guard is evaluated before the ``established`` one (module docstring,
    ``judge``). Perturbation: swapping that order (reporting
    ``baseline_unestablished`` ahead of ``week_not_representative``) reds
    this test, which is the point -- the precedence was previously unpinned.

    ``chest_strap_raw`` reads only 5 distinct days in the baseline window
    (below ``MIN_BASELINE_READINGS``, so ``established`` is false) and still
    covers 3 of the judged week's earliest days; ``health_snapshot`` --
    never read in the baseline window -- covers the week's 3 latest days, all
    strictly after the strap's. ``verdict_withheld`` fires (a tier with zero
    baseline-window days and >= ``MIN_WINDOW_READINGS`` judged-week days, all
    later than the resolved tier's own) on a baseline that is *also* thin.
    """
    D = date(2026, 9, 20)
    _, baseline_last = hrv_trend.baseline_window(D)
    # Contiguous with the judged week (ending on baseline_last == D-7, the
    # window's own last day), so no coverage-gap reset clips this thin block
    # before it can be counted: a silence of more than GAP_RESET_DAYS anywhere
    # in the read history re-establishes the baseline and would wipe these 5
    # readings before ``established`` was ever asked about them.
    strap_baseline_days = [baseline_last - timedelta(days=i) for i in range(4, -1, -1)]
    strap_week_days = [D - timedelta(days=6), D - timedelta(days=5), D - timedelta(days=4)]
    snapshot_week_days = [D - timedelta(days=2), D - timedelta(days=1), D]

    rows = (
        readings(STRAP, strap_baseline_days, 40.0, "strap-base")
        + readings(STRAP, strap_week_days, 40.0, "strap-week")
        + readings(SNAPSHOT, snapshot_week_days, 40.0, "snap-week")
    )

    series = build(rows, D)
    assert series.tier == STRAP, "the thin strap is still the densest/only baseline-window tier"
    verdict = hrv_trend.judge(series)

    assert verdict.established is False, "baseline_n should be 5, below MIN_BASELINE_READINGS"
    assert verdict.baseline_n == 5
    assert verdict.readings_in_window == 3
    assert series.withheld is True, "the never-used snapshot should trip verdict_withheld here"
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert verdict.unavailable_reason == WEEK_NOT_REPRESENTATIVE, verdict.unavailable_reason
    assert verdict.unavailable_reason != BASELINE_UNESTABLISHED


def test_unavailable_reason_is_null_whenever_the_verdict_is_asserted() -> None:
    """The invariant the field promises: null iff the verdict is not
    ``hrv_unavailable``. A settled athlete inside the band (``hrv_normal``)
    and one below it (``hrv_suppressed``) both carry ``None``."""
    baseline_days = span(date(2026, 1, 1), date(2026, 8, 27))
    D = date(2026, 9, 3)

    normal_rows = readings(STRAP, baseline_days, 40.0, "n-base") + readings(
        STRAP, span(D - timedelta(days=6), D), 40.0, "n-week"
    )
    normal = hrv_trend.judge(build(normal_rows, D))
    assert normal.verdict == hrv_trend.VERDICT_NORMAL
    assert normal.unavailable_reason is None

    suppressed_rows = readings(STRAP, baseline_days, 40.0, "s-base") + readings(
        STRAP, span(D - timedelta(days=6), D), 20.0, "s-week"
    )
    suppressed = hrv_trend.judge(build(suppressed_rows, D))
    assert suppressed.verdict == hrv_trend.VERDICT_SUPPRESSED
    assert suppressed.unavailable_reason is None


def test_unavailable_reason_no_tier_when_the_store_holds_no_reading_at_all() -> None:
    """The structural cause (T128's ``resolve_baseline_tier`` answering "no
    tier at all"): an empty series resolves ``tier`` to ``None``, so ``band``
    is trivially ``None`` too, and the reason is the structural one rather
    than the generic ``no_band``."""
    series = build([], date(2026, 9, 3))
    assert series.tier is None
    verdict = hrv_trend.judge(series)
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert verdict.unavailable_reason == NO_TIER


def test_unavailable_reason_no_band_when_a_resolved_tier_has_fewer_than_two_baseline_readings() -> None:
    """A resolved (non-null) tier with a single baseline-window reading:
    ``band`` is ``None`` (``build_band`` needs at least two), but the tier
    itself is real -- the generic ``no_band`` cause, not the structural one."""
    D = date(2026, 9, 3)
    baseline_first, _ = hrv_trend.baseline_window(D)
    rows = readings(STRAP, [baseline_first], 40.0, "one")
    series = build(rows, D)
    assert series.tier == STRAP
    verdict = hrv_trend.judge(series)
    assert verdict.band is None
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert verdict.unavailable_reason == NO_BAND
    assert verdict.unavailable_reason != NO_TIER


def test_unavailable_reason_baseline_unestablished_alone() -> None:
    """T116's population, in isolation: a computable but unestablished band
    (2 <= n < 14), a full judged week on the same tier, not withheld."""
    D = date(2026, 9, 3)
    _, baseline_last = hrv_trend.baseline_window(D)
    # Contiguous with the judged week, for the same reason as the precedence
    # test above: a thin, isolated baseline block far from the week would be
    # clipped away by the coverage-gap reset before ``established`` is asked.
    baseline_days_ = [baseline_last - timedelta(days=i) for i in range(4, -1, -1)]
    week_days = span(D - timedelta(days=6), D)
    rows = readings(STRAP, baseline_days_, 40.0, "base") + readings(STRAP, week_days, 40.0, "week")

    series = build(rows, D)
    verdict = hrv_trend.judge(series)
    assert verdict.band is not None
    assert verdict.established is False
    assert series.withheld is False
    assert verdict.readings_in_window >= hrv_trend.MIN_WINDOW_READINGS
    assert verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE
    assert verdict.unavailable_reason == BASELINE_UNESTABLISHED
