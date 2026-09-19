"""T153 -- the unreported per-dataset band clip at an internal capture hole (F006 AC17, first half).

A dataset can hold readings at ``D-66..D-50`` and again at ``D-20..D-7`` -- one
tier, two eras, a 30-day hole -- and until T153 nothing cut them apart when
another tier bridged the silence: ``coverage_gap_reset`` needs the **whole
series** silent (AC16, global by design) and ``_era_boundary`` needs an
old-tier reading followed by a new-tier one, so on a single dataset it returns
``None`` (measured during planning). The band then spanned both eras.

**How a hole is counted.** A hole of ``N`` days is ``N`` silent local days
**strictly between** the last pre-hole reading and the first post-hole reading
(``hrv_trend._silence_between``: ``(later - earlier).days - 1``), and the clip
fires on **more than** ``GAP_RESET_DAYS`` of them -- 22 clips, 21 does not --
which is exactly how ``coverage_gap_reset`` counts and what
``test_a_22_day_gap_resets_and_a_21_day_gap_does_not`` already pins for the
constant. AC17's "at least ``GAP_RESET_DAYS``" is read as that rule and not as
``>= 21``: the constant has one meaning (research/00 §5.4: "21 does not reset
and 22 does"), and a per-dataset clip that fired one day earlier than the
global gap would make two rules disagree about the same number of days.
Pinned below by ``test_the_hole_is_counted_exactly_as_the_global_gap_counts_it``.

**What the clip sets and does not set.** It moves ``baseline_window[0]`` to
the resumption (composing with the gap and era clips as the later first day),
lists the pre-hole readings ``before_reset: coverage_gap`` out of the
dataset's ``series`` (research/00 §1.6: every non-contributing row is listed
exactly once), and so rebuilds ``baseline``, ``band``, ``n`` and
``established`` from the post-hole readings. It sets **no** ``reset_on`` and
**no** ``reset_reason``: the reported reset is the global gap's or
``tier_change_reset``'s (T154), never this clip's.

Every test prints the slice it compared (``[slice compared] ...``), so a green
run shows what was asserted and not only that something was.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import hrv_trend

AUCKLAND = ZoneInfo("Pacific/Auckland")

# The target date the other HRV suites use: baseline [2026-07-04, 2026-09-01],
# judged week [2026-09-02, 2026-09-08].
D = date(2026, 9, 8)
WINDOW_FIRST = D - timedelta(days=66)
#: The last pre-hole day the boundary sweep holds constant (``D-40``).
HOLE_LAST_BEFORE = D - timedelta(days=40)

STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"

BEFORE_RESET_COVERAGE_GAP = "before_reset: coverage_gap"


# ---------------------------------------------------------------------------
# row builders (the same shapes as test_hrv_trend_series.py; duplicated rather
# than imported across test modules)
# ---------------------------------------------------------------------------


def local(day: date, hh: int, mm: int = 0, zone: ZoneInfo = AUCKLAND) -> str:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC).isoformat()


def row(start_time: str, tier: str | None, value: float | None, session_id: str | None = None) -> dict:
    return {
        "session_id": session_id or f"s-{start_time}-{tier}",
        "start_time": start_time,
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


def readings(tier: str, days: list[date], value: float = 40.0, hh: int = 6, prefix: str | None = None) -> list[dict]:
    return [row(local(day, hh), tier, value, f"{prefix}-{day}" if prefix else None) for day in days]


def ago(n: int) -> date:
    return D - timedelta(days=n)


def days_between(first: date, last: date) -> list[date]:
    """Every local day of the closed interval ``[first, last]``."""
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def silent(last_before: date, silent_days: int) -> date:
    """The resumption day after exactly ``silent_days`` silent local days
    following ``last_before`` -- ``_silence_between(last_before, resumption)
    == silent_days``."""
    return last_before + timedelta(days=silent_days + 1)


def carrier(first: date = WINDOW_FIRST, last: date = D) -> list[dict]:
    """A daily Health Snapshot from ``first`` to ``last`` at 45 ms: the tier
    that bridges every strap silence so the *global* gap never fires and
    whatever clips the strap is this task's mechanism alone."""
    return readings(SNAPSHOT, days_between(first, last), 45.0, hh=7, prefix="snap")


def dataset(series: hrv_trend.HrvSeries, tier: str) -> hrv_trend.HrvDataset:
    return next(d for d in series.datasets if d.tier == tier)


def before_reset_ids(series: hrv_trend.HrvSeries, prefix: str) -> set[str]:
    return {e.session_id for e in series.excluded if e.reason == BEFORE_RESET_COVERAGE_GAP and e.session_id.startswith(prefix)}


def slice_of(series: hrv_trend.HrvSeries, tier: str) -> str:
    d = dataset(series, tier)
    days = [r.date for r in d.baseline]
    span = f"{days[0]}..{days[-1]}" if days else "(empty)"
    mean = None if d.band is None else round(d.band.mean, 4)
    return (
        f"[slice compared] tier={tier} gap_reset_on={series.gap_reset_on} window={d.baseline_window[0]}..{d.baseline_window[1]} "
        f"baseline_days={span} n={d.n} established={d.established} band.mean={mean} "
        f"reset=({d.reset_on}, {d.reset_reason}) series_first={d.series[0].date if d.series else None} "
        f"before_reset_coverage_gap={len([e for e in series.excluded if e.reason == BEFORE_RESET_COVERAGE_GAP])}"
    )


def accounted_once(rows: list[dict], series: hrv_trend.HrvSeries) -> None:
    """research/00 §1.6 under F006 (AC15): every row inside ``[D-66, D]`` is
    in exactly one dataset's ``series`` or in ``excluded``, never both."""
    listed = [r.session_id for d in series.datasets for r in d.series] + [e.session_id for e in series.excluded]
    assert sorted(listed) == sorted(r["session_id"] for r in rows), "every row listed exactly once"


# ---------------------------------------------------------------------------
# the first failing test
# ---------------------------------------------------------------------------


def two_era_strap() -> list[dict]:
    """The task's series: one tier, ``D-66..D-50`` at 60 ms and ``D-20..D-7``
    at 40 ms -- 29 silent local days between ``D-50`` and ``D-20`` -- and a
    40 ms judged week, so the dataset was **read yesterday** and still
    carries the hole (the clip is orthogonal to the global gap, AC16)."""
    rows = readings(STRAP, days_between(ago(66), ago(50)), 60.0, prefix="old")
    rows += readings(STRAP, days_between(ago(20), ago(7)), 40.0, prefix="new")
    rows += readings(STRAP, days_between(ago(6), D), 40.0, prefix="week")
    return rows


def test_a_bridged_internal_hole_clips_the_band_to_the_post_hole_readings() -> None:
    """The first failing test. With a daily snapshot bridging the strap's
    30-day hole, no global gap fires and no era boundary exists, so shipped
    F006 (785f89c) built the strap band over **both** eras: ``n`` 31,
    ``band.mean`` 3.9112 -- neither ``ln 60`` (4.0943) nor ``ln 40``
    (3.6889) -- on the unclipped window ``[D-66, D-7]``. After T153 the strap
    band is the post-hole era's alone: window ``[D-20, D-7]``, ``n`` 14,
    mean ``ln 40``; the 17 pre-hole readings are listed
    ``before_reset: coverage_gap`` and are in no dataset's series; the
    snapshot is untouched; and nothing is reported -- ``reset_on`` and
    ``reset_reason`` stay ``None`` on both datasets and ``gap_reset_on`` is
    ``None``. Perturbation: counting the hole ``>= GAP_RESET_DAYS`` does not
    move this test (29 > 21 either way); the boundary pins below do."""
    rows = two_era_strap() + carrier()
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    strap, snapshot = dataset(series, STRAP), dataset(series, SNAPSHOT)
    print(slice_of(series, STRAP))
    print(slice_of(series, SNAPSHOT))

    assert series.gap_reset_on is None, "the snapshot bridges the silence: no global gap (AC16)"
    assert strap.baseline_window == (ago(20), ago(7))
    assert [r.date for r in strap.baseline] == days_between(ago(20), ago(7))
    assert strap.n == 14 and strap.established is True
    assert strap.band is not None and strap.band.mean == pytest.approx(math.log(40.0))
    assert {r.rmssd_ms for r in strap.baseline} == {40.0}, "no pre-hole 60 ms reading is in the band"
    assert (strap.reset_on, strap.reset_reason) == (None, None), "the clip is unreported"
    assert [r.date for r in strap.window] == days_between(ago(6), D), "the judged week is untouched"
    assert strap.series[0].date == ago(20), "the pre-hole era has left the dataset's series"

    assert before_reset_ids(series, "old") == {f"old-{day}" for day in days_between(ago(66), ago(50))}
    assert not before_reset_ids(series, "new") and not before_reset_ids(series, "week")
    assert snapshot.baseline_window == (ago(66), ago(7)) and snapshot.n == 60
    assert (snapshot.reset_on, snapshot.reset_reason) == (None, None)
    accounted_once(rows, series)

    view = hrv_trend.selected_view(series)
    assert view.tier == STRAP and (view.reset_on, view.reset_reason) == (None, None)
    assert hrv_trend.judge(view).band == strap.band


def test_the_lone_tier_hole_is_the_global_gaps_and_the_clip_lists_nothing_twice() -> None:
    """The task's shape with **no** bridging tier is not this task's
    population: every tier is silent, so the global gap fires (shipped and
    unchanged: reported ``coverage_gap`` on ``D-20``, ``n`` 14). Degenerate
    because both mechanisms would clip at the same day: the gap rebinds the
    shared readings before the partition, so the dataset's series holds no
    pre-hole reading for the internal clip to see, and the 17 exclusions are
    listed once, not twice. The band is the same as the bridged clip's."""
    rows = two_era_strap()
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    strap = dataset(series, STRAP)
    print(slice_of(series, STRAP))

    assert series.gap_reset_on == ago(20)
    assert (strap.reset_on, strap.reset_reason) == (ago(20), "coverage_gap"), "the global gap is reported"
    assert strap.baseline_window == (ago(20), ago(7)) and strap.n == 14
    assert strap.band is not None and strap.band.mean == pytest.approx(math.log(40.0))
    assert len([e for e in series.excluded if e.reason == BEFORE_RESET_COVERAGE_GAP]) == 17
    accounted_once(rows, series)

    bridged = dataset(hrv_trend.build_series(rows + carrier(), AUCKLAND, D), STRAP)
    assert bridged.band == strap.band and bridged.baseline_window == strap.baseline_window


# ---------------------------------------------------------------------------
# the boundary, and its alignment with the global gap's counting
# ---------------------------------------------------------------------------


def holed_strap(silent_days: int, last_before: date = HOLE_LAST_BEFORE) -> list[dict]:
    """A strap read daily from ``D-66`` to ``last_before`` at 60 ms, silent
    for exactly ``silent_days`` local days, then daily at 40 ms to ``D``."""
    resume_on = silent(last_before, silent_days)
    rows = readings(STRAP, days_between(ago(66), last_before), 60.0, prefix="old")
    rows += readings(STRAP, days_between(resume_on, D), 40.0, prefix="new")
    return rows


@pytest.mark.parametrize(
    "silent_days, clipped",
    [(hrv_trend.GAP_RESET_DAYS + 1, True), (hrv_trend.GAP_RESET_DAYS, False), (hrv_trend.GAP_RESET_DAYS - 1, False)],
    ids=["22-silent-days-clips", "21-does-not", "20-does-not"],
)
def test_the_boundary_is_more_than_gap_reset_days_silent_local_days(silent_days: int, clipped: bool) -> None:
    """A hole of exactly ``GAP_RESET_DAYS`` silent days (21) is **not**
    clipped and one of 22 is -- the constant's own boundary
    (``test_a_22_day_gap_resets_and_a_21_day_gap_does_not``), so a hole the
    global gap would not call a break is not one here either. The 21-day
    row is the residual named in F006's Negative Class: the band still
    mixes both eras (mean strictly between ``ln 60`` and ``ln 40``). The
    20-day row is the task's ``GAP_RESET_DAYS - 1`` pin. Perturbation:
    ``>= GAP_RESET_DAYS`` reds the 21-day row."""
    rows = holed_strap(silent_days) + carrier()
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    strap = dataset(series, STRAP)
    resume_on = silent(ago(40), silent_days)
    print(f"[slice compared] silent_days={silent_days} last_before={ago(40)} resume_on={resume_on}")
    print(slice_of(series, STRAP))

    assert series.gap_reset_on is None
    assert (strap.reset_on, strap.reset_reason) == (None, None)
    assert strap.band is not None
    if clipped:
        assert strap.baseline_window == (resume_on, ago(7))
        assert [r.date for r in strap.baseline] == days_between(resume_on, ago(7))
        assert strap.band.mean == pytest.approx(math.log(40.0))
        assert before_reset_ids(series, "old") == {f"old-{day}" for day in days_between(ago(66), ago(40))}
    else:
        assert strap.baseline_window == (ago(66), ago(7))
        assert [r.date for r in strap.baseline] == days_between(ago(66), ago(40)) + days_between(resume_on, ago(7))
        assert math.log(40.0) < strap.band.mean < math.log(60.0), "the residual: a shorter hole still mixes eras"
        assert not before_reset_ids(series, "old")
    accounted_once(rows, series)


def test_the_hole_is_counted_exactly_as_the_global_gap_counts_it() -> None:
    """Alignment, swept over every silence from 0 to 40 days after ``D-40``:
    the internal clip fires on a bridged strap exactly when
    ``coverage_gap_reset`` fires on the same strap alone, and at the same
    resumption day -- **while the resumption is inside the baseline
    window** (0..32 silent days; ``D-40`` + 33 is ``D-7``). The two rules
    share one constant and must share one meaning of "a hole of N days".
    Axis held constant: the hole's last pre-hole day, ``D-40``. From 33
    silent days the resumption is in the judged week: the global gap still
    fires (it scans ``[D-66, D]``) and the internal clip does **not** --
    that hole is not internal to the window, and the straddle probe below
    names it as AC6's population, so the divergence is asserted here rather
    than left to be discovered."""
    compared = []
    for silent_days in range(41):
        resume_on = silent(ago(40), silent_days)
        lone = hrv_trend.build_series(holed_strap(silent_days), AUCKLAND, D)
        bridged = dataset(hrv_trend.build_series(holed_strap(silent_days) + carrier(), AUCKLAND, D), STRAP)
        global_fires = lone.gap_reset_on is not None
        clip_fires = bridged.baseline_window[0] != ago(66)
        compared.append((silent_days, global_fires, clip_fires))
        expected_clip = global_fires and resume_on <= ago(7)
        assert clip_fires == expected_clip, f"{silent_days} silent days: global {global_fires}, internal {clip_fires}"
        if clip_fires:
            assert bridged.baseline_window[0] == lone.gap_reset_on == resume_on
        assert (bridged.reset_on, bridged.reset_reason) == (None, None)
    print(f"[slice compared] silent_days x (global fires, internal fires) = {compared}")
    # 40 silent days resume on D+1, outside the span: the global rule's own
    # open-gap case, no reset on either side.
    assert [s for s, g, _ in compared if g] == list(range(hrv_trend.GAP_RESET_DAYS + 1, 40))
    assert [s for s, _, c in compared if c] == list(range(hrv_trend.GAP_RESET_DAYS + 1, 33))


# ---------------------------------------------------------------------------
# adversarial probes: the degenerate set ships with the predicate
# ---------------------------------------------------------------------------


def test_probe_a_hole_at_the_very_start_of_the_window_leaves_one_reading_behind() -> None:
    """A single strap morning on ``D-66``, 22 silent days (``D-65..D-44``),
    then daily from ``D-43``. Degenerate because the pre-hole era is one
    reading -- the smallest era that can be clipped away. Asserts: the
    window starts at ``D-43``, that one reading is listed
    ``before_reset: coverage_gap`` and is in no dataset, and the band is
    the post-hole era's. The twin with 21 silent days (daily from ``D-44``)
    keeps ``D-66`` in the band."""
    assert hrv_trend._silence_between(ago(66), ago(43)) == 22 and hrv_trend._silence_between(ago(66), ago(44)) == 21
    rows = readings(STRAP, [ago(66)], 60.0, prefix="old") + readings(STRAP, days_between(ago(43), D), 40.0, prefix="new")
    series = hrv_trend.build_series(rows + carrier(), AUCKLAND, D)
    strap = dataset(series, STRAP)
    print(slice_of(series, STRAP))

    assert strap.baseline_window == (ago(43), ago(7)) and strap.n == 37
    assert before_reset_ids(series, "old") == {f"old-{ago(66)}"}
    assert strap.band is not None and strap.band.mean == pytest.approx(math.log(40.0))
    assert (strap.reset_on, strap.reset_reason) == (None, None)

    twin = readings(STRAP, [ago(66)], 60.0, prefix="old") + readings(STRAP, days_between(ago(44), D), 40.0, prefix="new")
    kept = dataset(hrv_trend.build_series(twin + carrier(), AUCKLAND, D), STRAP)
    print(slice_of(hrv_trend.build_series(twin + carrier(), AUCKLAND, D), STRAP))
    assert kept.baseline_window == (ago(66), ago(7)) and kept.baseline[0].date == ago(66) and kept.n == 39


def test_probe_a_hole_at_the_very_end_of_the_window_leaves_a_band_less_dataset() -> None:
    """Daily strap ``D-66..D-30``, 22 silent days, one morning on ``D-7``
    (the window's last day), then a daily judged week. Degenerate because
    the post-hole era inside the window is **one** reading: the clip is
    unconditional, so the dataset is left with ``n`` 1, no band and
    ``established`` False -- below ``build_band``'s minimum -- and is not
    judgeable, so the snapshot is selected. Shipped F006 kept the 38-day
    pre-layoff band (``n`` 38, established) and selected the strap on it.
    This is the intended direction: a band from a layoff the athlete has
    come back from is the stale band AC6 exists to refuse."""
    rows = readings(STRAP, days_between(ago(66), ago(30)), 60.0, prefix="old")
    rows += readings(STRAP, [ago(7)], 40.0, prefix="new") + readings(STRAP, days_between(ago(6), D), 40.0, prefix="week")
    series = hrv_trend.build_series(rows + carrier(), AUCKLAND, D)
    strap = dataset(series, STRAP)
    print(slice_of(series, STRAP))

    assert strap.baseline_window == (ago(7), ago(7))
    assert strap.n == 1 and strap.band is None and strap.established is False
    assert before_reset_ids(series, "old") == {f"old-{day}" for day in days_between(ago(66), ago(30))}
    assert (strap.reset_on, strap.reset_reason) == (None, None)
    assert len(strap.window) == 7, "the judged week is not the clip's to touch"
    view = hrv_trend.selected_view(series)
    print(view.selection.describe() if view.selection is not None else "(no selection)")
    assert view.tier == SNAPSHOT, "not judgeable on the post-clip established (AC8)"


def test_probe_a_hole_that_straddles_d_minus_7_is_not_internal_and_is_not_clipped() -> None:
    """Daily strap ``D-66..D-30``, silent through ``D-6``, back on
    ``D-5..D``: the 24 silent days are **not** inside the baseline window,
    so the window spans no hole and the clip does not fire -- the band is
    the 37 pre-layoff readings, unclipped. Degenerate because it is one
    day away from the previous probe's shape and lands in a different
    mechanism's population: AC17 says AC6's recency gate, not the clip,
    defends the layoff-then-return series. Observed and pinned as the
    residual this leaves: the strap's latest window reading (``D-30``) is
    23 days behind the snapshot's (``D-7``), inside
    ``RECENCY_TOLERANCE_DAYS`` (28), so the gate does not skip it and the
    strap is selected on a band 30..66 days old. That is AC6's boundary and
    not this task's to move (IDEA-080 names the neighbouring geometry)."""
    rows = readings(STRAP, days_between(ago(66), ago(30)), 60.0, prefix="old")
    rows += readings(STRAP, days_between(ago(5), D), 40.0, prefix="week")
    series = hrv_trend.build_series(rows + carrier(), AUCKLAND, D)
    strap = dataset(series, STRAP)
    print(slice_of(series, STRAP))

    assert strap.baseline_window == (ago(66), ago(7)) and strap.n == 37 and strap.established is True
    assert not before_reset_ids(series, "old") and not series.excluded
    assert (strap.reset_on, strap.reset_reason) == (None, None)
    view = hrv_trend.selected_view(series)
    print(view.selection.describe() if view.selection is not None else "(no selection)")
    assert view.tier == STRAP, "the residual: 23 < 28, AC6 does not skip it either"


def test_probe_two_holes_clip_at_the_last_one() -> None:
    """Three strap eras -- ``D-66..D-60``, ``D-37..D-31``, ``D-8..D-7`` --
    separated by 22 silent days each. Degenerate because two resumptions
    compete: the scan runs backwards from the latest reading, as the global
    gap's does, so the **last** hole wins and both earlier eras leave the
    series. Asserts: window ``[D-8, D-7]``, ``n`` 2, a band over the two
    post-hole readings only, and 14 ``before_reset: coverage_gap`` entries."""
    eras = [days_between(ago(66), ago(60)), days_between(ago(37), ago(31)), days_between(ago(8), ago(7))]
    assert [hrv_trend._silence_between(a[-1], b[0]) for a, b in pairwise(eras)] == [22, 22]
    rows = readings(STRAP, eras[0], 60.0, prefix="first") + readings(STRAP, eras[1], 50.0, prefix="middle")
    rows += readings(STRAP, eras[2], 40.0, prefix="last") + readings(STRAP, days_between(ago(6), D), 40.0, prefix="week")
    series = hrv_trend.build_series(rows + carrier(), AUCKLAND, D)
    strap = dataset(series, STRAP)
    print(slice_of(series, STRAP))

    assert strap.baseline_window == (ago(8), ago(7)) and strap.n == 2 and strap.established is False
    assert strap.band is not None and strap.band.mean == pytest.approx(math.log(40.0))
    assert before_reset_ids(series, "first") | before_reset_ids(series, "middle") == {
        f"{prefix}-{day}" for prefix, era in (("first", eras[0]), ("middle", eras[1])) for day in era
    }
    assert not before_reset_ids(series, "last")
    assert (strap.reset_on, strap.reset_reason) == (None, None)
    accounted_once(rows + carrier(), series)


def test_probe_a_tier_absent_from_the_window_has_no_dataset_and_one_read_only_this_week_has_no_hole() -> None:
    """The dataset that is entirely one hole. Two shapes, both degenerate
    because there is nothing on one side of the "hole": (a) a tier with no
    reading anywhere in ``[D-66, D]`` gets **no dataset** -- there is no
    series to scan; (b) a strap read only in the judged week has an empty
    baseline, no days to scan, window ``[D-66, D-7]`` unchanged, ``n`` 0,
    no band and nothing excluded. Neither raises on the empty sequence."""
    absent = hrv_trend.build_series(carrier(), AUCKLAND, D)
    print(f"[slice compared] tiers with a dataset = {[d.tier for d in absent.datasets]}")
    assert [d.tier for d in absent.datasets] == [SNAPSHOT]

    rows = readings(STRAP, days_between(ago(6), D), 40.0, prefix="week") + carrier()
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    strap = dataset(series, STRAP)
    print(slice_of(series, STRAP))
    assert strap.baseline_window == (ago(66), ago(7)) and strap.n == 0 and strap.band is None
    assert strap.baseline == () and len(strap.window) == 7
    assert not series.excluded and (strap.reset_on, strap.reset_reason) == (None, None)


def test_probe_exactly_two_readings_either_side_is_build_bands_minimum_on_both_sides() -> None:
    """``D-66``/``D-65`` at 60 and 62 ms, 56 silent days, ``D-8``/``D-7`` at
    40 and 42 ms. Degenerate because two readings is the smallest set
    ``build_band`` accepts (sample SD needs ``n - 1 >= 1``), so both the
    unclipped band (four readings, shipped) and the clipped one (two) exist
    and differ only by the clip. Asserts the band is the mean of the two
    post-hole logs with a real (unfloored) half-width, ``n`` 2, and the two
    pre-hole readings listed."""
    rows = [row(local(ago(66), 6), STRAP, 60.0, "old-a"), row(local(ago(65), 6), STRAP, 62.0, "old-b")]
    rows += [row(local(ago(8), 6), STRAP, 40.0, "new-a"), row(local(ago(7), 6), STRAP, 42.0, "new-b")]
    assert hrv_trend._silence_between(ago(65), ago(8)) == 56
    series = hrv_trend.build_series(rows + carrier(), AUCKLAND, D)
    strap = dataset(series, STRAP)
    print(slice_of(series, STRAP))

    assert strap.baseline_window == (ago(8), ago(7)) and strap.n == 2 and strap.established is False
    assert strap.band is not None
    assert strap.band.mean == pytest.approx((math.log(40.0) + math.log(42.0)) / 2)
    assert strap.band.floored is False
    assert before_reset_ids(series, "old") == {"old-a", "old-b"}
    assert (strap.reset_on, strap.reset_reason) == (None, None)


def test_probe_the_clip_composes_with_the_global_gap_and_the_report_stays_the_gaps() -> None:
    """Every tier silent ``D-63..D-41`` (23 days: a global gap, reported,
    resumed ``D-40``), then the strap alone silent ``D-38..D-9`` (30 days)
    while the snapshot carries on. Degenerate because two clips land on one
    dataset: the gap's at ``D-40`` (reported) and the hole's at ``D-8``
    (unreported). The window is the later, ``[D-8, D-7]``; ``reset_on`` /
    ``reset_reason`` are the **gap's** (``D-40``, ``coverage_gap``) --
    ``window[0]`` after ``reset_on``, a state the contract already allows --
    and every clipped reading is listed exactly once."""
    rows = readings(STRAP, days_between(ago(66), ago(64)), 60.0, prefix="pre-gap")
    rows += readings(STRAP, days_between(ago(40), ago(39)), 60.0, prefix="pre-hole")
    rows += readings(STRAP, days_between(ago(8), D), 40.0, prefix="new")
    rows += carrier(ago(66), ago(64)) + carrier(ago(40), D)
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    strap, snapshot = dataset(series, STRAP), dataset(series, SNAPSHOT)
    print(slice_of(series, STRAP))
    print(slice_of(series, SNAPSHOT))

    assert series.gap_reset_on == ago(40)
    assert (strap.reset_on, strap.reset_reason) == (ago(40), "coverage_gap")
    assert strap.baseline_window == (ago(8), ago(7)) and strap.n == 2
    assert strap.band is not None and strap.band.mean == pytest.approx(math.log(40.0))
    assert before_reset_ids(series, "pre-gap") | before_reset_ids(series, "pre-hole") == {
        f"pre-gap-{day}" for day in days_between(ago(66), ago(64))
    } | {f"pre-hole-{day}" for day in days_between(ago(40), ago(39))}
    assert snapshot.baseline_window == (ago(40), ago(7)) and (snapshot.reset_on, snapshot.reset_reason) == (
        ago(40),
        "coverage_gap",
    )
    accounted_once(rows, series)


def test_probe_the_hole_is_scanned_on_the_era_clipped_window_and_the_tier_change_report_stays() -> None:
    """A genuine switch: the snapshot sustains the previous window and runs
    to ``D-45``; the strap begins ``D-44`` (the era boundary, reported
    ``tier_change``), reads daily to ``D-30``, is silent 22 days and is back
    on ``D-7``. Two stray snapshot mornings (``D-25``, ``D-15``) bridge the
    silence for the global gap without un-making the boundary (2 stray days,
    none in the week). Degenerate because the hole sits **after** an era
    boundary: the scan runs over the era-clipped window, the two clips
    compose as the later first day (``D-7``), and the report is untouched
    -- still ``tier_change`` on ``D-44``, now with ``window[0]`` after
    ``reset_on``, as a ``coverage_gap`` report already can be (T107). The
    pre-hole readings are ``before_reset: coverage_gap``; the snapshot's
    own 19-day silence (``D-45`` to ``D-25``) is under the threshold and
    nobody's hole."""
    rows = readings(SNAPSHOT, days_between(ago(126), ago(45)), 45.0, hh=7, prefix="snap")
    rows += readings(SNAPSHOT, [ago(25), ago(15)], 45.0, hh=7, prefix="stray")
    rows += readings(STRAP, days_between(ago(44), ago(30)), 60.0, prefix="era")
    rows += readings(STRAP, [ago(7)], 40.0, prefix="new") + readings(STRAP, days_between(ago(6), D), 40.0, prefix="week")
    series = hrv_trend.build_series(rows, AUCKLAND, D)
    strap, snapshot = dataset(series, STRAP), dataset(series, SNAPSHOT)
    print(slice_of(series, STRAP))
    print(slice_of(series, SNAPSHOT))

    assert series.gap_reset_on is None
    assert (strap.reset_on, strap.reset_reason) == (ago(44), "tier_change")
    assert snapshot.baseline_window == (ago(66), ago(7)) and snapshot.n == 24, "19 silent days is no hole"
    assert strap.baseline_window == (ago(7), ago(7)) and strap.n == 1 and strap.band is None
    assert before_reset_ids(series, "era") == {f"era-{day}" for day in days_between(ago(44), ago(30))}
    assert not [e for e in series.excluded if e.reason == "before_reset: tier_change"], "nothing preceded the era"
    accounted_once(rows, series)  # the pre-window snapshot rows are listed ``outside_windows``


def test_the_clip_reads_only_the_datasets_own_baseline_days_and_is_pure() -> None:
    """The predicate's inputs, enumerated: the sorted distinct local days of
    **this** dataset's post-era-clip baseline window and ``GAP_RESET_DAYS``.
    Direct pins on the helper: empty and singleton sequences return
    ``None``; 21 silent days ``None``; 22 the resumption; of two holes the
    later. And the module stays pure (no config, db or fastapi import)."""
    resumption = hrv_trend._internal_hole_resumption
    assert resumption([]) is None and resumption([ago(20)]) is None
    assert resumption([ago(60), silent(ago(60), 21)]) is None
    assert resumption([ago(60), silent(ago(60), 22)]) == silent(ago(60), 22)
    two = [ago(66), silent(ago(66), 22), silent(silent(ago(66), 22), 22)]
    assert resumption(two) == two[-1]
    print(f"[slice compared] helper on {two} -> {resumption(two)}")
    with open(hrv_trend.__file__, encoding="utf-8") as source:
        text = source.read()
    assert not any(f"import {name}" in text or f"from runcoach_api import {name}" in text for name in ("db", "config", "fastapi"))
