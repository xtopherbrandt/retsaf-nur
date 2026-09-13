"""T092 -- ``metrics/hrv_trend.py``: baseline re-establishment (F005).

The two reset triggers that survive the decision log: a **coverage gap** (more
than ``GAP_RESET_DAYS`` = 21 consecutive local days with no entry in the
post-exclusion series, the reset landing on the first reading after it) and
a **sustained tier change** (the resolved baseline tier sustains
``[D-66, D-7]``, differs from the tier that sustained the previous 60-day
window ``[D-126, D-67]``, and the two eras do not interleave over both
windows together: no reading of the new tier falls between the old tier's
first and last -- T093, restated by T094, judged over both windows since
sprint-005 review cycle 3). There is no timezone-change reset.

Pure unit tests over hand-built row dicts, like T083's. Nothing here computes
a band or a verdict (T084): "suppression is withheld" is asserted as *the
judged window carries no reading the band could be compared against*, and
"readings before the gap do not contribute to the band" as *they are not in
``baseline`` and are listed as excluded*.

**Both contract tables below were authored before the implementation**, from
the task text and the construction reference alone, and are stated as such
(contract-tables-need-an-independent-oracle):

* the gap table enumerates ``{gap length: 20, 21, 22, 60}`` x ``{position:
  inside the baseline, ending exactly on D-7, ending inside the judged
  week}`` -- the rule is "more than 21", so 21 does not reset and 22 does,
  wherever the gap sits;
* the tier-change table enumerates ``{previous tier} x {current tier} x
  {previous window thin?} x {current window thin?}`` -- a change is asserted
  only when the tiers differ *and* both windows sustain their tier (>= 14
  readings). The task text names only the previous window's thinness; the
  fourth axis is added because F005's scenario fires "when chest_strap_raw
  readings become dense enough to sustain a baseline", and the construction
  reference says the same ("the new tier now sustains a baseline"), so a
  thin current window is the population between the two documents' examples.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import hrv_trend

AUCKLAND = ZoneInfo("Pacific/Auckland")

# The target date F005's canonical response example uses: baseline
# [2026-07-04, 2026-09-01], judged week [2026-09-02, 2026-09-08], and the
# previous baseline window [2026-05-05, 2026-07-03].
D = date(2026, 9, 8)

STRAP = "chest_strap_raw"
OVERNIGHT = "health_api_overnight"
SNAPSHOT = "health_snapshot"

ESTABLISHED = 20
THIN = 5


# ---------------------------------------------------------------------------
# row builders (the same shapes as test_hrv_trend_series.py; duplicated
# rather than imported across test modules)
# ---------------------------------------------------------------------------


def local(day: date, hh: int, mm: int = 0, zone: ZoneInfo = AUCKLAND) -> str:
    """The stored ``start_time`` of a capture taken at local wall time
    ``hh:mm`` on ``day`` in ``zone``, spelled ``+00:00`` as ``mapping.py``
    stores it."""
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


def span(first: date, last: date, step: int = 1) -> list[date]:
    """Every ``step``-th local day of the closed interval ``[first, last]``."""
    days = []
    day = first
    while day <= last:
        days.append(day)
        day += timedelta(days=step)
    return days


def ago(n: int, target: date = D) -> date:
    return target - timedelta(days=n)


def in_previous_window(days: list[date], target: date) -> int:
    """How many of ``days`` fall inside ``previous_window(target)``."""
    first, last = hrv_trend.previous_window(target)
    return sum(1 for day in days if first <= day <= last)


def build(rows, zone: ZoneInfo = AUCKLAND, target: date = D) -> hrv_trend.HrvSeries:
    return hrv_trend.build_series(rows, zone, target)


def excluded_reasons(result: hrv_trend.HrvSeries) -> dict[str, str]:
    return {entry.session_id: entry.reason for entry in result.excluded}


def gapped_history(gap_days: int, resume_on: date, tier: str = STRAP, target: date = D) -> list[dict]:
    """Readings on every local day from ``target-90`` up to the day before a
    ``gap_days``-day silence that ends the day before ``resume_on``, then on
    every day from ``resume_on`` to ``target``. Pre-gap readings are 60 ms
    and post-gap readings 40 ms so the two eras are distinguishable."""
    gap_first = resume_on - timedelta(days=gap_days)
    before = readings(tier, span(ago(90, target), gap_first - timedelta(days=1)), 60.0, "before")
    after = readings(tier, span(resume_on, target), 40.0, "after")
    return before + after


# ---------------------------------------------------------------------------
# the first failing test: 22 resets, 21 does not
# ---------------------------------------------------------------------------


def test_a_22_day_gap_resets_and_a_21_day_gap_does_not() -> None:
    """Two series identical except that the silence between two runs of
    readings is 21 local days in one and 22 in the other. "More than 21"
    means 22 resets and 21 does not; the boundary must be exact.

    Perturbation: making the rule ``>= GAP_RESET_DAYS`` turns the 21-day
    half of this test red.
    """
    resume_on = ago(20)

    twenty_two = build(gapped_history(22, resume_on))
    twenty_one = build(gapped_history(21, resume_on))

    assert twenty_two.reset_on == resume_on
    assert twenty_two.reset_reason == "coverage_gap"
    assert twenty_two.baseline_window == (resume_on, ago(7))
    assert all(r.date >= resume_on for r in twenty_two.baseline)

    assert twenty_one.reset_on is None
    assert twenty_one.reset_reason is None
    assert twenty_one.baseline_window == (ago(66), ago(7))
    assert min(r.date for r in twenty_one.baseline) < resume_on, "the baseline spans a 21-day gap"


def test_a_series_without_a_reset_reports_neither_field() -> None:
    result = build(readings(STRAP, span(ago(66), D)))

    assert result.reset_on is None
    assert result.reset_reason is None
    assert result.baseline_window == (ago(66), ago(7))


# ---------------------------------------------------------------------------
# the gap is measured on the series, not on stored rows
# ---------------------------------------------------------------------------


def test_the_gap_is_measured_on_the_post_exclusion_series_not_on_stored_rows() -> None:
    """25 consecutive local days whose only captures are pre-amendment-window
    rows (a tier, no value) count as absent: F005's "a gap spanned only by
    excluded rows still counts as a gap". The reset the amendment window
    exists to trigger would never fire otherwise.

    Perturbation: measuring the gap on stored rows (bucketing every row's
    day, readings or not) turns this red -- the 25 rows bridge the gap.
    """
    resume_on = ago(20)
    history = gapped_history(25, resume_on)
    amendment_window = [
        row(local(day, 6), STRAP, None, f"pre-amendment-{day}")
        for day in span(resume_on - timedelta(days=25), resume_on - timedelta(days=1))
    ]

    result = build(history + amendment_window)

    assert result.reset_on == resume_on
    assert result.reset_reason == "coverage_gap"
    reasons = excluded_reasons(result)
    assert all(reasons[r["session_id"]] == "pre_amendment_window" for r in amendment_window)


def test_a_gap_bridged_by_off_tier_readings_is_not_a_gap() -> None:
    """The series the gap is measured on is post-exclusion, pre-filter: a
    Health Snapshot reading on a day is an entry even when the baseline is
    on the chest strap. An athlete who kept capturing on a lower tier has no
    coverage gap, and the strap baseline spans the stretch."""
    resume_on = ago(20)
    history = gapped_history(25, resume_on)
    bridge = readings(SNAPSHOT, span(resume_on - timedelta(days=25), resume_on - timedelta(days=1)), 38.0, "snap")

    result = build(history + bridge)

    assert result.reset_on is None
    assert result.reset_reason is None
    assert result.tier == STRAP
    assert min(r.date for r in result.baseline) == ago(66)


# ---------------------------------------------------------------------------
# what a reset does to the baseline
# ---------------------------------------------------------------------------


def test_readings_before_the_gap_do_not_contribute_to_the_band() -> None:
    """After a reset the baseline holds the resumption era only; every
    reading from before the gap contributed to neither the baseline nor the
    window, so each is listed as excluded with the reset named."""
    resume_on = ago(20)
    result = build(gapped_history(22, resume_on))

    assert result.reset_reason == "coverage_gap"
    assert {r.rmssd_ms for r in result.baseline} == {40.0}
    assert [r.date for r in result.baseline] == span(resume_on, ago(7))
    assert {r.rmssd_ms for r in result.window} == {40.0}
    before = {entry.session_id: entry for entry in result.excluded if entry.session_id.startswith("before-")}
    inside = {sid for sid, entry in before.items() if entry.date >= ago(66)}
    assert inside, "the pre-gap readings inside [D-66, D] must be listed, not silently dropped"
    assert {before[sid].reason for sid in inside} == {"before_reset: coverage_gap"}
    assert {entry.reason for sid, entry in before.items() if sid not in inside} == {"outside_windows"}
    assert {r.session_id for r in result.readings} == {r.session_id for r in result.series}
    assert not {r.session_id for r in result.readings} & set(before)


def test_the_fresh_baseline_resolves_its_tier_on_the_resumption_era_only() -> None:
    """A snapshot baseline, a 30-day gap, and a strap resumption nine days
    before D-7. Over the unclipped ``[D-66, D-7]`` the snapshot still has 16
    readings and would win the tier; resolved on the clipped window the
    fresh baseline is the strap's nine (thin, so T084 withholds suppression
    until it is established). The reason is the gap, not a tier change.
    Perturbation: resolving the tier before clipping turns this red."""
    resume_on = ago(15)
    before = readings(SNAPSHOT, span(ago(90), resume_on - timedelta(days=31)), 60.0, "snap")
    after = readings(STRAP, span(resume_on, D), 40.0, "strap")

    result = build(before + after)

    assert result.reset_on == resume_on
    assert result.reset_reason == "coverage_gap"
    assert result.tier == STRAP
    assert [r.date for r in result.baseline] == span(resume_on, ago(7))
    assert len(result.baseline) == 9 < hrv_trend.MIN_BASELINE_READINGS


def test_week_coverage_is_judged_on_the_gap_clipped_window_and_the_gap_is_the_only_reset() -> None:
    """Week coverage on a gap-clipped baseline window (sprint-005 review,
    S2). A snapshot era, a 22-day silence, then both devices resumed daily
    from ``D-22``: the strap through ``D-7`` (16 readings) and the snapshot
    through ``D`` (16 in the clipped window, 7 in the week). Over the
    clipped ``[D-22, D-7]`` both tiers are candidates and the strap is the
    tier the window *sustains* (highest fidelity with >= 14), but it holds
    nothing in the week, so rule 2 hands the baseline to the snapshot -- the
    candidate that covers it. The one reset is the gap, landing on the
    resumption; the strap the week rejected is no ``tier_change``, and every
    strap reading is off-tier. Perturbation: resolving the tier on baseline
    counts alone gives the strap with an empty week and turns this red."""
    resume_on = ago(22)
    before = readings(SNAPSHOT, span(ago(126), resume_on - timedelta(days=23)), 60.0, "snap-before")
    strap = readings(STRAP, span(resume_on, ago(7)), 40.0, "strap")
    after = readings(SNAPSHOT, span(resume_on, D), 40.0, "snap-after")

    result = build(before + strap + after)

    assert len(strap) == 16 and len(after) == 23
    assert result.reset_on == resume_on
    assert result.reset_reason == "coverage_gap"
    assert result.baseline_window == (resume_on, ago(7))
    assert result.tier == SNAPSHOT
    assert [r.date for r in result.baseline] == span(resume_on, ago(7))
    assert all(r.tier == SNAPSHOT for r in result.baseline)
    assert len(result.window) == 7
    reasons = excluded_reasons(result)
    assert {reasons[r["session_id"]] for r in strap} == {"off_baseline_tier: chest_strap_raw"}
    assert {reasons[r["session_id"]] for r in before if r["session_id"] >= f"snap-before-{ago(66)}"} == {
        "before_reset: coverage_gap"
    }


def test_a_gap_reset_takes_precedence_over_a_tier_change() -> None:
    """Both triggers at once -- a snapshot era, a 25-day silence, a strap era
    dense enough to sustain a baseline -- report the gap: it is the event the
    backward scan meets, and its resumption day is where the fresh baseline
    begins. Documented in the module; pinned here."""
    resume_on = ago(30)
    before = readings(SNAPSHOT, span(ago(126), resume_on - timedelta(days=26)), 60.0, "snap")
    after = readings(STRAP, span(resume_on, D), 40.0, "strap")

    result = build(before + after)

    assert result.tier == STRAP
    assert len(result.baseline) >= hrv_trend.MIN_BASELINE_READINGS
    assert result.reset_reason == "coverage_gap"
    assert result.reset_on == resume_on


# ---------------------------------------------------------------------------
# the gap contract table: {gap length} x {position}
#
# Authored before the implementation. ``resume_on`` fixes the position; the
# gap is the ``length`` local days before it. Expected: a reset iff
# length > 21, landing on ``resume_on``; the baseline is then the resumption
# era inside [resume_on, D-7] -- empty when the resumption is after D-7.
# ---------------------------------------------------------------------------

GAP_POSITIONS = {
    "inside-baseline": ago(20),  # the gap and the resumption both sit inside [D-66, D-7]
    "ends-on-D-7": ago(6),  # the gap's last empty day is D-7; the resumption opens the judged week
    "ends-in-judged-week": ago(2),  # the resumption is inside [D-6, D]
}

GAP_TABLE = [
    (length, position, length > 21) for length in (20, 21, 22, 60) for position in GAP_POSITIONS
]


@pytest.mark.parametrize(
    "length, position, resets", GAP_TABLE, ids=[f"{n}d|{p}|{'reset' if r else 'none'}" for n, p, r in GAP_TABLE]
)
def test_the_gap_rule_over_length_and_position(length: int, position: str, resets: bool) -> None:
    resume_on = GAP_POSITIONS[position]
    result = build(gapped_history(length, resume_on))

    if resets:
        assert result.reset_on == resume_on
        assert result.reset_reason == "coverage_gap"
        assert result.baseline_window == (resume_on, ago(7))
        assert [r.date for r in result.baseline] == span(resume_on, ago(7))
        assert all(reason != "before_reset: coverage_gap" for sid, reason in excluded_reasons(result).items()
                   if sid.startswith("after-"))
    else:
        assert result.reset_on is None
        assert result.reset_reason is None
        assert result.baseline_window == (ago(66), ago(7))
        assert "before_reset: coverage_gap" not in excluded_reasons(result).values()
        expected_days = [day for day in span(ago(66), ago(7)) if not resume_on - timedelta(days=length) <= day < resume_on]
        assert [r.date for r in result.baseline] == expected_days


# ---------------------------------------------------------------------------
# the corroboration athlete, and the sustained tier change
# ---------------------------------------------------------------------------


def test_a_single_off_tier_capture_is_corroboration_not_a_reset() -> None:
    """45 snapshot readings in the baseline (and a snapshot previous window)
    plus one chest-strap capture: the baseline stays on the snapshot, the
    strap reading is off-tier corroboration, and nothing resets."""
    previous = readings(SNAPSHOT, span(ago(126), ago(67), 2), 40.0, "prev")
    current = readings(SNAPSHOT, span(ago(51), ago(7)), 40.0, "snap")  # 45 days
    borrowed = [row(local(ago(30), 6, 30), STRAP, 55.0, "borrowed-strap")]

    result = build(previous + current + borrowed)

    assert len(current) == 45
    assert result.tier == SNAPSHOT
    assert len(result.baseline) == 45
    assert result.reset_on is None
    assert result.reset_reason is None
    assert excluded_reasons(result)["borrowed-strap"] == "off_baseline_tier: chest_strap_raw"


def test_a_sustained_tier_change_starts_a_fresh_baseline_and_withholds_suppression_until_established() -> None:
    """A snapshot baseline (daily from D-126) gives way to daily chest-strap
    captures from D-29, at a much lower rMSSD.

    While the strap has fewer than 14 baseline readings (target D-20: three),
    the tier is still the snapshot's, the strap readings are off-tier, and
    the judged window holds **no** reading the snapshot band could be
    compared against -- so no suppression can be asserted and the low strap
    values are never read as a physiological drop. Once the strap sustains a
    baseline (target D: 23 strap readings in ``[D-66, D-7]``), the tier
    changes, the reset is reported as ``tier_change`` on the first strap
    day, and the baseline window is clipped there.
    """
    snapshot_era = readings(SNAPSHOT, span(ago(126), ago(30)), 60.0, "snap")
    strap_era = readings(STRAP, span(ago(29), D), 25.0, "strap")
    rows = snapshot_era + strap_era

    transition = build(rows, target=ago(20))
    assert transition.tier == SNAPSHOT
    assert transition.reset_on is None
    assert transition.reset_reason is None
    assert transition.window == (), "no strap reading may be judged against the snapshot band"
    assert all(r.tier == SNAPSHOT for r in transition.baseline)
    strap_in_windows = {
        entry.session_id: entry.reason
        for entry in transition.excluded
        if entry.session_id.startswith("strap-") and entry.date <= ago(20)
    }
    assert len(strap_in_windows) == 10  # D-29 .. D-20
    assert set(strap_in_windows.values()) == {"off_baseline_tier: chest_strap_raw"}

    established = build(rows, target=D)
    assert established.tier == STRAP
    assert established.reset_on == ago(29)
    assert established.reset_reason == "tier_change"
    assert established.baseline_window == (ago(29), ago(7))
    assert [r.date for r in established.baseline] == span(ago(29), ago(7))
    assert all(r.tier == STRAP for r in established.baseline)
    assert len(established.baseline) >= hrv_trend.MIN_BASELINE_READINGS


def test_a_tier_that_differs_only_because_the_previous_window_is_thin_is_not_a_change() -> None:
    """Five snapshot readings in ``[D-126, D-67]`` resolve that window to
    the snapshot, but nothing sustained a baseline there; a strap baseline
    now is the athlete's first, not a change from anything."""
    previous = readings(SNAPSHOT, span(ago(79), ago(67), 3), 40.0, "prev")  # 5 readings
    current = readings(STRAP, span(ago(60), ago(22), 2), 40.0, "strap")  # 20 readings

    result = build(previous + current)

    assert len(previous) == 5 and len(current) == 20
    assert result.tier == STRAP
    assert result.reset_on is None
    assert result.reset_reason is None


# ---------------------------------------------------------------------------
# the tier-change contract table:
#   {previous tier} x {current tier} x {previous thin?} x {current thin?}
#
# Authored before the implementation. The previous window holds ``prev_n``
# readings of ``prev`` ending at D-67; the current baseline holds ``cur_n``
# of ``cur`` from D-60 every second day (so a reported reset would clip the
# window visibly, to D-60). Expected: ``tier_change`` iff the tiers differ
# and both windows are established (>= 14 readings of their tier).
# ---------------------------------------------------------------------------

TIER_TABLE = [
    (prev, cur, prev_n, cur_n)
    for prev in (STRAP, SNAPSHOT, OVERNIGHT)
    for cur in (STRAP, SNAPSHOT, OVERNIGHT)
    for prev_n in (ESTABLISHED, THIN)
    for cur_n in (ESTABLISHED, THIN)
]


def _tier_id(entry) -> str:
    prev, cur, prev_n, cur_n = entry
    return f"{prev}({prev_n})->{cur}({cur_n})"


@pytest.mark.parametrize("entry", TIER_TABLE, ids=_tier_id)
def test_the_tier_change_rule_over_both_windows(entry) -> None:
    prev, cur, prev_n, cur_n = entry
    previous = readings(prev, [ago(67 + 3 * i) for i in range(prev_n)], 40.0, "prev")
    current = readings(cur, [ago(60 - 2 * i) for i in range(cur_n)], 40.0, "cur")

    result = build(previous + current)

    assert result.tier == cur
    expected_change = prev != cur and prev_n >= 14 and cur_n >= 14
    if expected_change:
        assert result.reset_reason == "tier_change"
        assert result.reset_on == ago(60)
        assert result.baseline_window == (ago(60), ago(7))
    else:
        assert result.reset_reason is None
        assert result.reset_on is None
        assert result.baseline_window == (ago(66), ago(7))
    assert len(result.baseline) == cur_n
    assert all(reason == "outside_windows" for sid, reason in excluded_reasons(result).items() if sid.startswith("prev-"))


# ---------------------------------------------------------------------------
# T093: a week-driven tier is not a tier change
#
# The tier rule now asks that the baseline tier also cover the judged week
# (F005 "Negative Class"; decision log 2026-09-10). A tier the judged week
# chose while another sustains the window, or a fallback the empty week
# forced, is not a change of baseline. Each test went red against the
# earlier comparison (T093's Delivered note); the rule they pin is stated
# in T094's section below (the resolved tier sustains the window, differs
# from the previous window's sustained tier, and the eras do not
# interleave), under which every row here keeps its outcome.
# ---------------------------------------------------------------------------


def trial_then_abandon() -> list[dict]:
    """The critic's series (``CRITIC-F005.md``), as in the series suite: a
    daily snapshot for 200 days ending ``D``, suppressed at 25 ms on
    2026-08-20 .. 2026-09-02; a strap daily 2026-07-03 .. 2026-07-16."""
    snapshot_days = span(ago(199), D)
    suppressed = span(date(2026, 8, 20), date(2026, 9, 2))
    rows = [
        row(local(day, 7), SNAPSHOT, 25.0 if day in suppressed else 38.0 + 6.0 * (i % 2), f"snap-{day}")
        for i, day in enumerate(snapshot_days)
    ]
    rows += readings(STRAP, span(date(2026, 7, 3), date(2026, 7, 16)), 79.0, "strap")
    return rows


def test_a_trial_then_abandoned_strap_never_reports_a_tier_change() -> None:
    """For every target from 2026-07-23 to 2026-09-07 the strap sustains a
    baseline by count (14 in ``[D-66, D-7]``) while the previous window is
    all snapshot -- the sustained tier *did* change -- yet the strap holds
    no reading in any judged week and never owns the baseline. A reset here
    would clip a snapshot baseline at a strap day and name an event the
    athlete never made; none is reported, on any day, and the strap rows
    are listed as off-tier."""
    rows = trial_then_abandon()

    for target in span(date(2026, 7, 23), D):
        result = build(rows, target=target)
        assert result.tier == SNAPSHOT, target
        assert result.reset_on is None and result.reset_reason is None, target
        assert result.baseline_window == (ago(66, target), ago(7, target)), target
        strap_reasons = {sid: why for sid, why in excluded_reasons(result).items() if sid.startswith("strap-")}
        assert set(strap_reasons.values()) <= {"off_baseline_tier: chest_strap_raw", "outside_windows"}, target


def test_a_week_driven_fallback_is_not_a_tier_change() -> None:
    """An established strap era in ``[D-126, D-67]``; a mixed baseline now
    (20 strap to D-28, 30 snapshot to D-8); nothing captured in ``[D-6, D]``.
    No candidate covers the week, so the candidate read last -- the
    snapshot, at D-8 -- takes the baseline for this empty week (T094; under
    T093 it was the densest, the same tier here). The strap sustains the
    previous window and the snapshot's era interleaves with the strap's
    inside this one, so nothing resets."""
    previous = readings(STRAP, span(ago(126), ago(67)), 40.0, "prev")
    strap_now = readings(STRAP, span(ago(66), ago(28), 2), 40.0, "strap")  # 20
    snapshot_now = readings(SNAPSHOT, span(ago(66), ago(8), 2), 40.0, "snap")  # 30

    result = build(previous + strap_now + snapshot_now)

    assert len(strap_now) == 20 and len(snapshot_now) == 30
    assert result.tier == SNAPSHOT
    assert result.window == ()
    assert result.reset_on is None and result.reset_reason is None
    assert result.baseline_window == (ago(66), ago(7))


def test_a_genuine_switch_still_resets_on_the_first_day_of_the_new_tier() -> None:
    """The T092 scenario, unchanged: a daily snapshot to ``D-21`` and a daily
    strap from ``D-20`` -- 14 strap readings in the baseline and 7 in the
    week. The strap sustains the baseline *and* covers the week, so the
    change is reported as today, on the first strap day."""
    snapshot_era = readings(SNAPSHOT, span(ago(126), ago(21)), 60.0, "snap")
    strap_era = readings(STRAP, span(ago(20), D), 25.0, "strap")

    result = build(snapshot_era + strap_era)

    assert result.tier == STRAP
    assert result.reset_reason == "tier_change"
    assert result.reset_on == ago(20)
    assert result.baseline_window == (ago(20), ago(7))
    assert len(result.baseline) == 14
    assert len(result.window) == 7


def test_a_previous_tier_capture_at_the_very_instant_of_the_new_tiers_first_is_interleaved() -> None:
    """Clause (c)'s boundary (T094 re-dispatch). The genuine switch above
    plus one more snapshot capture on D-20: at 05:59, a minute before the
    strap's first capture, the snapshot era still ended before the strap's
    began and the reset is reported on D-20; at 06:00 exactly -- the same
    instant -- the old tier does not predate the new one, the eras are
    interleaved and nothing is reported. The comparison is on the
    captures' instants -- a new-tier reading at or before the old tier's
    last (``<= A_last``) is interleaved -- and the tie goes to "no reset".
    Perturbation (``<`` in place of ``<=``): the same-instant case reports
    a ``tier_change`` on D-20."""
    snapshot_era = readings(SNAPSHOT, span(ago(126), ago(21)), 60.0, "snap")
    strap_era = readings(STRAP, span(ago(20), D), 25.0, "strap")

    before = build(snapshot_era + strap_era + [row(local(ago(20), 5, 59), SNAPSHOT, 60.0, "snap-last")])
    assert before.tier == STRAP
    assert (before.reset_reason, before.reset_on) == ("tier_change", ago(20))

    same_instant = build(snapshot_era + strap_era + [row(local(ago(20), 6), SNAPSHOT, 60.0, "snap-tied")])
    assert same_instant.tier == STRAP
    assert same_instant.reset_reason is None and same_instant.reset_on is None


def test_the_documented_tier_oscillation_is_not_a_reset_in_either_direction() -> None:
    """A daily snapshot plus a strap on three days of one week and two of
    the next, back through the previous window. The strap sustains both
    windows, so neither the week judged on the strap (three readings) nor
    the week judged on the snapshot (two) is a change of the sustained
    tier: no reset either way. The tier alternation itself is pinned in
    the series suite as the documented cost."""
    rows = readings(SNAPSHOT, span(ago(126), D), 40.0, "snap")
    for k in range(18):
        week_first = ago(7 * k + 6)
        offsets = (0, 2, 4) if k % 2 == 0 else (0, 2)
        rows += readings(STRAP, [week_first + timedelta(days=o) for o in offsets], 79.0, f"strap-w{k}")

    on_strap = build(rows, target=D)
    on_snapshot = build(rows, target=ago(7))

    assert (on_strap.tier, on_snapshot.tier) == (STRAP, SNAPSHOT)
    assert on_strap.reset_on is None and on_strap.reset_reason is None
    assert on_snapshot.reset_on is None and on_snapshot.reset_reason is None


# ---------------------------------------------------------------------------
# T094: a reset needs non-interleaved eras, and reset_on is the era's true
# first day
#
# Rule 4 restated (sprint-005 review cycle 2; F005 verdict G3/G6/G8):
# ``tier_change`` is asserted when, and only when, the resolved baseline
# tier is a candidate (>= 14 in the clipped window), differs from the tier
# the previous window sustains, and the eras do not interleave -- over both
# windows together, no reading of the resolved tier falls between the
# previous tier's first and last (review cycle 3, M1; T094 judged this on
# the current window alone, which let a finished trial in the previous
# window through) -- the old era ended before the new one began.
# ``reset_on`` is the resolved tier's first reading over both windows
# after the previous tier's last, so it no longer slides one day per day
# once the era start ages past D-66. Each test went red against cf48c3a
# (T094's Delivered note records the run).
# ---------------------------------------------------------------------------


def test_the_reverse_transition_resets_the_day_the_snapshot_first_owns_the_baseline() -> None:
    """G3 (§3.7.3 "abandons the chest strap"). An owning strap daily to
    ``T``, a daily snapshot from ``T+1``, nothing else. Through ``T+20`` the
    strap is the only candidate and keeps the baseline (no strap reading
    in the week: ``hrv_unavailable``, never a reset -- the previous window
    sustains the strap too). On ``T+21`` the snapshot holds 14 in the
    window and covers the week; the strap's readings all predate ``T+1``,
    so the reset fires that day with ``reset_on = T+1`` and stays
    reported, unchanged, on every later day **until the previous window
    no longer sustains the strap** (sprint-005 review cycle 3, S1): rule
    4(b) reads ``sustained_tier`` on ``[D-126, D-67]``, the highest-
    fidelity tier with 14 there, so the *old* strap keeps that window --
    and the reset -- until it drops below 14 there, on ``T+114``; the
    snapshot reaching 14 there (``T+81``) changes nothing. The walk runs
    through that boundary: ``T+113`` reported, ``T+114`` not.
    Perturbation (``sustained_now == tier``, cf48c3a): the reset appears
    only at ``T+54``, when the strap drops below 14 in the window.
    Perturbation (the stop condition as previously documented, "as soon as
    the previous window holds 14 of the new tier"): withdrawn from
    ``T+81`` -- red on ``T+81``..``T+113``."""
    T = ago(60)
    strap_days = span(ago(190), T)
    rows = readings(STRAP, strap_days, 40.0, "strap")
    rows += readings(SNAPSHOT, span(T + timedelta(days=1), T + timedelta(days=120)), 40.0, "snap")

    for k in range(1, 115):
        target = T + timedelta(days=k)
        result = build(rows, target=target)
        if k < 21:
            assert result.tier == STRAP, k
            assert result.reset_reason is None and result.reset_on is None, k
        elif k <= 113:
            assert result.tier == SNAPSHOT, k
            assert result.reset_reason == "tier_change", k
            assert result.reset_on == T + timedelta(days=1), k
        else:
            assert result.tier == SNAPSHOT, k
            assert result.reset_reason is None and result.reset_on is None, k

    assert in_previous_window(strap_days, T + timedelta(days=113)) == 14
    assert in_previous_window(strap_days, T + timedelta(days=114)) == 13
    assert (T + timedelta(days=81) - timedelta(days=67)) - (T + timedelta(days=1)) == timedelta(days=13), (
        "the snapshot holds 14 in the previous window from T+81, a month before the reset clears"
    )

    first = build(rows, target=T + timedelta(days=21))
    assert first.baseline_window == (T + timedelta(days=1), T + timedelta(days=14))
    assert len(first.baseline) == hrv_trend.MIN_BASELINE_READINGS
    assert hrv_trend.judge(build(rows, target=T + timedelta(days=20))).verdict == "hrv_unavailable"


def test_reset_on_is_the_eras_true_first_day_and_does_not_slide_past_d_minus_66() -> None:
    """G8. A snapshot era to D-81 and a daily strap from D-80. From D-13 the
    era start is older than the window (``D'-66 > D-80``); ``reset_on`` is
    D-80 on every such day all the same, computed over the previous window
    too, and ``baseline.window[0]`` stays the clip ``max(D'-66, D-80)``.
    The row's "at D" is refined here: on D the previous window
    ``[D-126, D-67]`` holds the strap's first 14 readings, so the strap
    sustains both windows, rule 4(b) fails and nothing is reported -- the
    reset is reported for as long as the previous window still belongs to
    the snapshot, and on every one of those days it names D-80.
    Perturbation (earliest reading inside the window, cf48c3a): D-79 at
    D-13, D-67 at D-1."""
    switch = ago(80)
    rows = readings(SNAPSHOT, span(ago(126), ago(81)), 40.0, "snap") + readings(STRAP, span(switch, D), 25.0, "strap")

    for target in span(ago(14), ago(1)):
        result = build(rows, target=target)
        assert result.tier == STRAP, target
        assert result.reset_reason == "tier_change", target
        assert result.reset_on == switch, target
        assert result.baseline_window == (max(switch, ago(66, target)), ago(7, target)), target
        assert [r.date for r in result.baseline] == span(max(switch, ago(66, target)), ago(7, target)), target

    at_d = build(rows, target=D)
    assert at_d.tier == STRAP
    assert at_d.reset_reason is None and at_d.reset_on is None
    assert at_d.baseline_window == (ago(66), ago(7))


def test_reset_on_is_the_first_reading_after_the_previous_tier_s_last_not_an_older_era_of_the_same_tier() -> None:
    """A snapshot era to D-74, a 40-day strap era D-73..D-34, the snapshot
    again from D-33; target D+7, so the previous window ``[D-119, D-60]``
    holds 46 snapshot readings *and* the strap's first 14 and sustains the
    strap (rule 1: highest fidelity with 14). The snapshot owns the current
    window (27, read last, the strap era wholly before it): a genuine
    reverse transition, reported. Its first day is D-33 -- the first
    snapshot reading after the strap's last -- not 2026-05-12, the first
    snapshot reading in either window, which belongs to the era the strap
    replaced. Red against cf48c3a (no reset: the strap still "sustained"
    the window by fidelity) and against the literal "first reading over
    both windows" (D-119)."""
    target = D + timedelta(days=7)
    rows = readings(SNAPSHOT, span(ago(126), ago(74)), 40.0, "snap-old")
    rows += readings(STRAP, span(ago(73), ago(34)), 25.0, "strap")
    rows += readings(SNAPSHOT, span(ago(33), ago(7)), 40.0, "snap-new")

    result = build(rows, target=target)

    assert result.tier == SNAPSHOT
    assert result.reset_reason == "tier_change"
    assert result.reset_on == ago(33)
    assert result.baseline_window == (ago(33), target - timedelta(days=7))
    assert len(result.baseline) == 27


# ---------------------------------------------------------------------------
# Sprint-005 review cycle 3: interleaving is judged over both windows, and
# clause (c)'s own error direction is named
#
# T094 evaluated clause (c) on the current window only. A trial that has
# aged wholly into the previous window then satisfies (c) vacuously -- the
# old tier has no reading in the current window to fail it -- and, once the
# previous window sustains the trial's tier by fidelity, 4(b) holds too, so
# a phantom ``tier_change`` is reported for an athlete who never switched
# (M1, reproduced). Interleaving is now judged over ``[D-126, D-7]``: with
# ``A`` the previous window's sustained tier and ``B`` the resolved tier,
# the eras interleave iff any ``B`` reading falls in ``(A_first, A_last]``
# over both windows. The two rows after the pin are the rule's accepted
# costs, reproduced first and named in F005's Negative Class (M3).
# ---------------------------------------------------------------------------


FINISHED_TRIAL = span(date(2026, 4, 1), date(2026, 4, 21))


def snapshot_with_a_finished_strap_trial() -> list[dict]:
    """The review's series B: a daily snapshot from 2025-11-01 through
    2026-10-31 at 06:00, and a 21-day strap trial 2026-04-01 .. 04-21
    (``FINISHED_TRIAL``) captured an hour after each snapshot."""
    rows = readings(SNAPSHOT, span(date(2025, 11, 1), date(2026, 10, 31)), 40.0, "snap")
    rows += [row(local(day, 7), STRAP, 55.0, f"strap-{day}") for day in FINISHED_TRIAL]
    return rows


def test_a_trial_that_has_aged_into_the_previous_window_is_not_a_tier_change() -> None:
    """M1. From 2026-06-27 the trial sits wholly inside ``[D-126, D-67]``
    with 21 readings there, so ``sustained_tier`` gives the strap by
    fidelity (4(b) holds) while the current window is snapshot-only; on
    the current window alone (c) was vacuously true, and ``tier_change on
    2026-04-22`` was reported through 08-12 -- until the trial dropped to
    13 there -- and withdrawn on 08-13 without an event. Judged over both
    windows the snapshot's readings run daily through the trial, the eras
    interleave, and no day of the walk reports anything. The strap count
    in the previous window is asserted so the fixture is shown to cross 14
    inside the walk. Red against the current-window clause (c):
    ``tier_change on 2026-04-22`` on 06-27, 07-10, 08-05 and 08-12."""
    rows = snapshot_with_a_finished_strap_trial()
    walk = [
        date(2026, 6, 26), date(2026, 6, 27), date(2026, 7, 10), date(2026, 8, 5), date(2026, 8, 12), date(2026, 8, 13)
    ]

    assert [in_previous_window(FINISHED_TRIAL, t) for t in walk] == [20, 21, 21, 21, 14, 13]

    for target in walk:
        result = build(rows, target=target)
        assert result.tier == SNAPSHOT, target
        assert result.reset_reason is None, (target, result.reset_reason, result.reset_on)
        assert result.reset_on is None, target
        assert result.baseline_window == (ago(66, target), ago(7, target)), target
        assert len(result.baseline) == 60, target


def test_a_clean_ended_strap_trial_reads_as_a_switch_until_the_snapshot_covers_a_week_again() -> None:
    """M3(b), reproduced as the patterns scanner described it. A daily
    snapshot to ``D-26``, an 18-day strap trial ``D-25``..``D-8``, nothing
    in ``D-7``..``D`` (the thin week), and the snapshot resumed from
    ``D+1``. The trial ended cleanly -- every snapshot reading predates
    its first -- so from ``D-5``, when it holds 14 in the window and
    covers the week, it is a genuine-looking switch: ``tier_change`` on
    ``D-25``. Rule 3's recency keeps the strap (read last, ``D-8``)
    through the thin days and the snapshot's first two resumed mornings;
    on ``D+3`` the snapshot covers the week again, rule 2 hands it the
    baseline it sustains in both windows, and the reset is withdrawn
    without an event. Reported ``D-5``..``D+2``, gone at ``D+3``; the cost
    is named in F005's Negative Class. Perturbation (the densest fallback,
    cf48c3a): the snapshot takes ``D-3``..``D+2`` back on 46 readings and
    the reset lasts two days -- red."""
    rows = readings(SNAPSHOT, span(ago(131), ago(26)), 60.0, "snap")
    rows += readings(STRAP, span(ago(25), ago(8)), 25.0, "strap")
    rows += readings(SNAPSHOT, span(D + timedelta(days=1), D + timedelta(days=7)), 60.0, "snap-again")
    assert len(readings(STRAP, span(ago(25), ago(8)))) == 18

    reported = {}
    for target in span(ago(6), D + timedelta(days=3)):
        result = build(rows, target=target)
        reported[target] = (result.tier, result.reset_reason, result.reset_on)

    assert reported[ago(6)] == (SNAPSHOT, None, None), "13 strap readings in [D-72, D-13]: not yet a candidate"
    for target in span(ago(5), D + timedelta(days=2)):
        assert reported[target] == (STRAP, "tier_change", ago(25)), target
    assert reported[D + timedelta(days=3)] == (SNAPSHOT, None, None)


# ---------------------------------------------------------------------------
# T095: clause (c) tolerates isolated captures by density
#
# Sprint-005 review cycle 3 (F005 verdict G9, G12; IDEA-065) found that an
# exact clause (c) let one capture of either tier, on either side of a
# genuine switch, silence the switch's reset: a stray of the new tier
# inside the old era (G9, the modal "tried the strap the week before
# buying one"), a stale trial of the new tier (G12: 28 days of silence,
# then the reset 48 days late), and the stray old-tier capture after the
# switch that cycle 3 had named as an accepted cost (IDEA-065). Decision
# log 2026-09-12, "density tolerance": readings on the wrong side of the
# era boundary are corroboration unless, together, they would themselves
# be a candidate (14 distinct local days) or cover a judged week (3 within
# one 7-day span) -- ``hrv_trend._isolated`` is the one statement of it.
# The same-instant switch-day tie stays interleaved, ``reset_on`` stays
# the new tier's first reading after the old era's last, and a habit dense
# enough to be a candidate still interleaves (G6, the tolerance's own error
# direction). Each walk went red at 0891061 (T095's Delivered note records
# the run and the decision table).
# ---------------------------------------------------------------------------


def genuine_switch(switch: date, until: date, snapshot_from: date = D - timedelta(days=190)) -> list[dict]:
    """A daily snapshot at 06:00 from ``snapshot_from`` to ``switch`` and a
    daily strap at 06:00 from the day after to ``until``: the control
    series every T095 walk perturbs by one capture or one trial."""
    rows = readings(SNAPSHOT, span(snapshot_from, switch), 60.0, "snap")
    rows += readings(STRAP, span(switch + timedelta(days=1), until), 25.0, "strap")
    return rows


def reported(rows: list[dict], targets: list[date]) -> list[tuple[str | None, str | None, date | None]]:
    """``(tier, reset_reason, reset_on)`` on each target, for a walk."""
    out = []
    for target in targets:
        result = build(rows, target=target)
        out.append((result.tier, result.reset_reason, result.reset_on))
    return out


def test_one_new_tier_capture_before_a_genuine_switch_does_not_silence_its_reset() -> None:
    """G9. A daily snapshot to ``SW``, a daily strap from ``SW+1``, and
    **one** strap capture at ``SW-11`` -- the athlete tried the strap the
    week before buying one. At 0891061 the stray fell inside the snapshot
    era's span, so (c) read the eras as interleaved and the switch's
    reset was reported on no day at all; the stray also supplied the 14th
    baseline capture, so the tier flipped a day early with nothing to
    explain the band step. Under the tolerance the stray is one isolated
    day: the old era still ends at ``SW`` and the new one begins at
    ``SW+1``, so ``tier_change on SW+1`` is reported from the day the strap
    first owns the baseline -- ``SW+20`` with the stray (it is a distinct
    day, and candidacy has no recency: IDEA-064, untouched here), ``SW+21``
    without -- and on every later day of the era. On ``SW+20`` the reset
    also clips the stray out of the reported era, so the response says
    what is true of it: ``baseline.n`` 13 and ``established: false`` --
    the band steps a day early, the reset explains the step, and no
    suppression can be asserted on it. Red at 0891061 on all three walked
    days."""
    switch = ago(60)
    control = genuine_switch(switch, switch + timedelta(days=70))
    stray = [row(local(switch - timedelta(days=11), 6, 30), STRAP, 25.0, "strap-stray")]
    walk = [switch + timedelta(days=20), switch + timedelta(days=21), switch + timedelta(days=60)]

    assert reported(control, walk) == [
        (SNAPSHOT, None, None),
        (STRAP, "tier_change", switch + timedelta(days=1)),
        (STRAP, "tier_change", switch + timedelta(days=1)),
    ]
    assert reported(control + stray, walk) == [(STRAP, "tier_change", switch + timedelta(days=1))] * 3
    early = build(control + stray, target=walk[0])
    assert early.baseline_window == (switch + timedelta(days=1), walk[0] - timedelta(days=7))
    assert hrv_trend.judge(early).established is False and len(early.baseline) == 13


def test_an_older_trial_of_the_new_tier_does_not_delay_a_genuine_switchs_reset() -> None:
    """G12. A daily snapshot to ``S``, a daily strap from ``S+1``, and a
    14-day strap trial ``S-90``..``S-77`` captured an hour after the
    snapshot. The control reports ``tier_change on S+1`` from ``S+21`` to
    ``S+80`` and clears on ``S+81``, when the strap sustains the previous
    window. At 0891061 the trial's readings sat inside the snapshot era's
    span, so (c) suppressed the reset until the trial's last day had left
    both windows (``S+50``): 29 days of silence, then a reset naming a day
    49 days earlier. Under the tolerance the trial's readings inside the
    windows are corroboration once they are fewer than 14 distinct days --
    from ``S+37``, when its first day ages out of ``[D-126, D-67]`` -- and
    the reset is reported from then on, unchanged. Through ``S+36`` the
    trial still holds 14 days there and rule 1 hands the previous window
    to the strap by fidelity, so clause (b) reads no change: that is a
    stale candidate in the previous window (IDEA-064's shape, untouched
    by this decision), not an interleaving, and it is pinned here as the
    tolerance's boundary rather than hidden. Red at 0891061 on ``S+37``,
    ``S+48`` and ``S+49``."""
    switch = date(2026, 5, 1)
    control = genuine_switch(switch, switch + timedelta(days=90), snapshot_from=switch - timedelta(days=200))
    trial_days = span(switch - timedelta(days=90), switch - timedelta(days=77))
    trial = [row(local(day, 7), STRAP, 25.0, f"strap-trial-{day}") for day in trial_days]
    offsets = [21, 36, 37, 48, 49, 50, 80, 81]
    walk = [switch + timedelta(days=k) for k in offsets]
    reset = (STRAP, "tier_change", switch + timedelta(days=1))

    assert len(trial_days) == 14
    assert [in_previous_window(trial_days, t) for t in walk] == [14, 14, 13, 2, 1, 0, 0, 0]
    assert reported(control, walk) == [reset] * 7 + [(STRAP, None, None)]
    assert reported(control + trial, walk) == [(STRAP, None, None)] * 2 + [reset] * 5 + [(STRAP, None, None)]


def test_a_stray_old_tier_capture_after_a_genuine_switch_does_not_hide_the_switch() -> None:
    """IDEA-065, closed. A daily snapshot to ``D-30``, a daily strap from
    ``D-29``, and one stray snapshot capture on ``D-25`` at 07:00 (the
    phone auto-captured once after the switch). Review cycle 3 named the
    outcome an accepted cost: the stray was the old era's last reading, so
    the strap's first four days sat inside the old era and the switch's
    reset was never reported -- ``established: true`` on a baseline that
    plainly began at ``D-29`` with ``reset_reason`` null on every day.
    Under the tolerance the stray is one isolated day after the boundary:
    the old era ends at ``D-30``, ``reset_on`` is ``D-29``, and the walk
    reports exactly what the control does -- ``tier_change on D-29`` at
    ``D`` and ``D+42``, cleared at ``D+52`` and ``D+60`` when the strap
    sustains the previous window and (b) fails. Red at 0891061 on ``D``
    and ``D+42``."""
    switch = ago(30)
    control = genuine_switch(switch, D + timedelta(days=70), snapshot_from=ago(126))
    stray = [row(local(ago(25), 7), SNAPSHOT, 60.0, "snap-stray")]
    walk = [D, D + timedelta(days=42), D + timedelta(days=52), D + timedelta(days=60)]
    expected = [(STRAP, "tier_change", ago(29))] * 2 + [(STRAP, None, None)] * 2

    assert reported(control, walk) == expected
    assert reported(control + stray, walk) == expected
    assert all(hrv_trend.judge(build(control + stray, target=t)).established for t in walk)


def test_thirteen_stray_days_inside_the_old_era_are_corroboration_and_fourteen_are_an_era() -> None:
    """The tolerance's candidacy threshold, from both sides. The genuine
    switch at ``D-30`` plus a strap trial captured an hour after the
    snapshot, wholly inside the old era and straddling ``D-67`` so that
    the strap never sustains the previous window and clause (b) holds
    either way: 13 trial days are corroboration and the reset is reported
    on ``D-29``; 14 would themselves be a candidate -- the strap was in
    use -- so the eras interleave and nothing is reported. Perturbation
    (a count tolerance of one or two captures): the 13-day case is
    interleaved -- red; (no tolerance, 0891061): the same."""
    switch = ago(30)
    control = genuine_switch(switch, D, snapshot_from=ago(126))

    def with_trial(days: int) -> list[dict]:
        trial_days = span(ago(72), ago(72) + timedelta(days=days - 1))
        assert in_previous_window(trial_days, D) < hrv_trend.MIN_BASELINE_READINGS
        return control + [row(local(day, 7), STRAP, 25.0, f"strap-trial-{day}") for day in trial_days]

    thirteen = build(with_trial(13))
    fourteen = build(with_trial(14))

    assert (thirteen.tier, thirteen.reset_reason, thirteen.reset_on) == (STRAP, "tier_change", ago(29))
    assert (fourteen.tier, fourteen.reset_reason, fourteen.reset_on) == (STRAP, None, None)


def test_old_tier_readings_that_cover_the_judged_week_are_use_and_the_week_slides_past_them() -> None:
    """The tolerance's week threshold, judged on ``[D-6, D]`` as rule 2
    judges week coverage. The genuine switch at ``D-30`` plus snapshot
    captures after it at 07:00 on days of the judged week: two are
    corroboration and ``tier_change on D-29`` stands; three cover the
    week -- the old device was in use this week -- so the eras interleave
    and nothing is reported. The week is the sliding one, so the clause
    carries rule 2's own edge, pinned here and named in F005's Negative
    Class as the tolerance's cost: the same three captures, seen from
    ``D+7`` when they have moved into the baseline window and no longer
    cover a week, are three isolated days again and the reset returns.
    Three captures a week *every* week is the young-oscillation habit,
    which the candidacy half keeps interleaved (G6). Red at 0891061 on the
    two-day case and on ``D+7``: an exact (c) reads every stray as the old
    era's last reading."""
    switch = ago(30)
    control = genuine_switch(switch, D + timedelta(days=7), snapshot_from=ago(126))
    two_this_week = [row(local(ago(n), 7), SNAPSHOT, 60.0, f"snap-again-{n}") for n in (2, 0)]
    three_this_week = two_this_week + [row(local(ago(1), 7), SNAPSHOT, 60.0, "snap-again-1")]
    walk = [D, D + timedelta(days=7)]

    assert reported(control + two_this_week, walk) == [(STRAP, "tier_change", ago(29))] * 2
    assert reported(control + three_this_week, walk) == [(STRAP, None, None), (STRAP, "tier_change", ago(29))]


def test_the_old_eras_first_day_bounds_the_new_tiers_strays_not_its_first_instant() -> None:
    """Clause (c)'s ``A_first`` boundary (review cycle 3, G16: ``a_first
    <=`` survived because no fixture had a new-tier reading near that
    instant; the boundary is now the old era's first local day, the unit
    every count is taken in). The older-era series -- a snapshot era to
    ``D-74``, a strap era ``D-73``..``D-34``, the snapshot again from
    ``D-33``, target ``D+7`` -- with 13 snapshot captures inside the strap
    era at 07:00 and one more: on ``D-74``, the day before the strap era,
    it belongs to the era the strap replaced, the strays number 13 and are
    tolerated -- ``tier_change on D-33``; on ``D-73`` at 05:00, an hour
    *before* the strap's first capture, it is a day the snapshot was in
    use inside the strap era, the strays number 14 and the eras interleave.
    Perturbation (the strap's first instant as the bound): the 05:00
    capture is not a stray and the second series reports a reset -- red;
    (``>`` on the day): the same."""
    target = D + timedelta(days=7)
    rows = readings(SNAPSHOT, span(ago(126), ago(75)), 40.0, "snap-old")
    rows += readings(STRAP, span(ago(73), ago(34)), 25.0, "strap")
    rows += [row(local(day, 7), SNAPSHOT, 40.0, f"snap-inside-{day}") for day in span(ago(72), ago(60))]
    rows += readings(SNAPSHOT, span(ago(33), ago(7)), 40.0, "snap-new")
    day_before = build(rows + [row(local(ago(74), 6), SNAPSHOT, 40.0, "snap-day-before")], target=target)
    same_day = build(rows + [row(local(ago(73), 5), SNAPSHOT, 40.0, "snap-same-day")], target=target)

    assert len(span(ago(72), ago(60))) == 13
    assert (day_before.tier, day_before.reset_reason, day_before.reset_on) == (SNAPSHOT, "tier_change", ago(33))
    assert day_before.baseline_window == (ago(33), target - timedelta(days=7))
    assert (same_day.tier, same_day.reset_reason, same_day.reset_on) == (SNAPSHOT, None, None)


# ---------------------------------------------------------------------------
# T098 (D4a, 2026-09-13): the clip is not the report
#
# The era boundary and the baseline clip are a property of the athlete's
# capture history; whether rule 4 *reports* a ``tier_change`` is a separate
# question about what the athlete is told. Until T098 the two were one
# branch, so the tolerance's week half -- judged on the **sliding** week --
# reached ``baseline`` and could flip an athlete-facing verdict with no new
# data (review cycle 4, G-C4-1). These three pins are that axis; all three
# are red at ccf44ef.
# ---------------------------------------------------------------------------


def trial_then_switch(base: date, strays: tuple[int, ...]) -> list[dict]:
    """G-C4-1's series: a daily ``health_snapshot`` at 06:00 from
    ``base-126`` to ``base-40``; a **10-day** ``chest_strap_raw`` trial at
    07:00 over ``[base-60, base-51]`` whose readings are deeply suppressed
    (25 ms), inside the 13-day tolerance T095 grants; a genuine switch to a
    daily strap from ``base-39``, ordinary to ``base-7`` and suppressed
    (33 ms) through the judged week; and one snapshot capture at 08:00 on
    each of ``strays`` days before ``base``."""
    rows = readings(SNAPSHOT, span(ago(126, base), ago(40, base)), 40.0, "snap")
    rows += [row(local(day, 7), STRAP, 25.0, f"trial-{day}") for day in span(ago(60, base), ago(51, base))]
    rows += [row(local(day, 7), STRAP, 40.0, f"strap-{day}") for day in span(ago(39, base), ago(7, base))]
    rows += [
        row(local(day, 7), STRAP, 33.0, f"strap-{day}")
        for day in span(ago(6, base), base + timedelta(days=4))
    ]
    rows += [row(local(ago(n, base), 8), SNAPSHOT, 40.0, f"snap-stray-{n}") for n in strays]
    return rows


def test_the_band_does_not_step_when_the_judged_week_slides_past_the_old_tiers_captures() -> None:
    """G-C4-1's day-by-day walk, with the three snapshot captures fixed on
    ``base-4``, ``base-3``, ``base-2`` and **no new data at all**. At
    ccf44ef the third capture covered the judged week, the eras read as
    interleaved, the reset was withdrawn and ``baseline`` was un-clipped
    from ``[base-39, D-7]`` back to ``[D-66, D-7]``, dragging the
    seven-week-old trial back into the band: ``hrv_normal`` on ``base``,
    ``base+1`` and ``base+2`` (``lo`` 3.4791, ``n`` 43) and
    ``hrv_suppressed`` from ``base+3`` (``lo`` 3.6459, ``n`` 36) once the
    week had slid past the captures -- the under-calling direction
    ``research/00`` §1.7 tolerates least and ``judge``'s own docstring
    names as forbidden.

    Under D4a the clip is unconditional: the baseline begins on the era's
    true first day on every one of the five days, ``n`` grows by exactly
    one a day as the window slides, the band drifts by less than 0.02 a
    day with no step, and the verdict is the era-correct
    ``hrv_suppressed`` throughout. Perturbation: restore the conditional
    clip (clip only when the reset is reported) and this goes red."""
    base = date(2026, 9, 7)
    rows = trial_then_switch(base, (4, 3, 2))
    walk = [base + timedelta(days=k) for k in range(5)]
    era_first_day = ago(39, base)

    results = [build(rows, target=target) for target in walk]
    verdicts = [hrv_trend.judge(result) for result in results]

    assert [result.baseline_window[0] for result in results] == [era_first_day] * 5
    assert [verdict.baseline_n for verdict in verdicts] == [33, 34, 35, 36, 37]
    assert [verdict.verdict for verdict in verdicts] == [hrv_trend.VERDICT_SUPPRESSED] * 5
    los = [verdict.band.lo for verdict in verdicts]
    assert max(abs(b - a) for a, b in pairwise(los)) < 0.02


def test_a_third_old_tier_capture_in_the_judged_week_moves_the_report_and_nothing_else() -> None:
    """G-C4-1's pair, at one target date: the same series with **two**
    snapshot captures in the judged week and with **three**. The third is a
    capture on a tier that is not the baseline tier -- it contributes
    nothing to the week mean -- and under D4a it decides only what the
    athlete is *told*: the band, ``baseline.n``, the reported window and
    the verdict are identical on both sides, and ``reset_reason`` /
    ``reset_on`` are the one difference. At ccf44ef the pair read
    ``tier_change`` / ``n`` 33 / ``lo`` 3.6789 / ``hrv_suppressed`` against
    ``null`` / ``n`` 43 / ``lo`` 3.4791 / ``hrv_normal``."""
    base = date(2026, 9, 7)
    two = build(trial_then_switch(base, (4, 3)), target=base)
    three = build(trial_then_switch(base, (4, 3, 2)), target=base)
    era_first_day = ago(39, base)

    assert two.baseline_window == three.baseline_window == (era_first_day, base - timedelta(days=7))
    two_verdict, three_verdict = hrv_trend.judge(two), hrv_trend.judge(three)
    assert two_verdict.baseline_n == three_verdict.baseline_n == 33
    assert math.isclose(two_verdict.band.lo, three_verdict.band.lo, rel_tol=0, abs_tol=1e-12)
    assert two_verdict.verdict == three_verdict.verdict == hrv_trend.VERDICT_SUPPRESSED
    assert (two.reset_reason, two.reset_on) == ("tier_change", era_first_day)
    assert (three.reset_reason, three.reset_on) == (None, None)


@pytest.mark.parametrize("strays", [(4, 3), (4, 3, 2)])
def test_the_clipped_readings_are_listed_before_reset_tier_change(strays: tuple[int, ...]) -> None:
    """G-C4-3. The readings the clip removes are neither in ``baseline``
    nor nowhere: they are listed ``before_reset: tier_change``, which is
    what ``contracts/openapi.yaml`` publishes as a value clients may
    receive and what ``_exclude_before_reset``'s disjoint-and-exhaustive
    invariant requires. Exhaustiveness is asserted over ``[D-66, D]``:
    every stored row there is in the series or in ``excluded``, exactly
    once. Parameterised over both sides of the pair above, because the
    listing is a property of the clip and not of the report -- at ccf44ef
    the ten trial readings were in neither list on either side."""
    base = date(2026, 9, 7)
    rows = trial_then_switch(base, strays)
    result = build(rows, target=base)
    trial_ids = {r["session_id"] for r in rows if r["session_id"].startswith("trial-")}

    assert {entry.session_id for entry in result.excluded if entry.reason == "before_reset: tier_change"} == trial_ids
    assert trial_ids.isdisjoint({r.session_id for r in result.series})

    in_windows = {
        r["session_id"]
        for r in rows
        if ago(66, base) <= datetime.fromisoformat(r["start_time"]).astimezone(AUCKLAND).date() <= base
    }
    listed = [r.session_id for r in result.series] + [entry.session_id for entry in result.excluded]
    assert sorted(session_id for session_id in listed if session_id in in_windows) == sorted(in_windows)
    assert len(listed) == len(set(listed))


def test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of() -> None:
    """The one place T098's selection can differ from a plain "fewest stray
    days": two candidate boundaries, one with **fewer** strays that the
    judged week is not clear of and one with **more** that it is. The rule
    orders on the week half first, and that is not decoration -- it is what
    makes T098 change no reported reset: at ccf44ef only the week-clear
    boundary was a candidate at all, so it was returned, and it still is.
    Dropping the term (``key=(-stray_days, a_end)``) returns the 07-01
    boundary with ``reported`` false and **withdraws a reset rule 4 reports
    today** -- red here, green over all five F005 suites, which is why it is
    pinned directly on ``_era_boundary`` rather than through a series:
    a boundary that the judged week is clear of needs a ``B_start`` after
    the week's old-tier captures, which puts 14+ stray days of the new tier
    behind it in every series ``build_series`` can be handed.

    ``A`` (snapshot) reads once on 07-01 and again on 09-03/04/05, inside
    the judged week; ``B`` (strap) reads on 07-01 (an hour later) through
    07-04 and again on 09-06. The 07-01 boundary's strays are the three
    week captures (3 days, 3 in the week); the 09-05 boundary's are the
    four early strap days (4 days, none in the week).
    """
    def reading(day: date, hh: int, tier: str) -> hrv_trend.Reading:
        start_time = local(day, hh)
        return hrv_trend.Reading(day, f"{tier}-{day}-{hh}", tier, 40.0, datetime.fromisoformat(start_time))

    old = [reading(date(2026, 7, 1), 6, SNAPSHOT)]
    old += [reading(day, 6, SNAPSHOT) for day in span(date(2026, 9, 3), date(2026, 9, 5))]
    new = [reading(day, 7, STRAP) for day in span(date(2026, 7, 1), date(2026, 7, 4))]
    new += [reading(date(2026, 9, 6), 7, STRAP)]

    assert hrv_trend._era_boundary(old, new, hrv_trend.judged_window(D)) == hrv_trend.EraBoundary(
        first_day=date(2026, 9, 6), reported=True
    )


# ---------------------------------------------------------------------------
# adversarial rows
# ---------------------------------------------------------------------------


def test_one_resumption_era_is_reported_coverage_gap_then_tier_change_then_nothing() -> None:
    """G14 (review cycle 3, critic B5), pinned so the three phases cannot
    drift silently. A daily snapshot to 2026-04-01, thirty silent local
    days, then a daily strap from ``R`` = 2026-05-02. One era, one
    ``reset_on``, three stories and no event between them:

    * ``R`` .. ``R+66`` -- ``coverage_gap on R``: the resumption day is
      inside ``[D-66, D]`` and the silence before it is measured from the
      previous window's last reading;
    * ``R+67`` .. ``R+79`` -- ``tier_change on R``: ``R`` has left the
      window, so the gap rule sees no silence, and rule 4 reads the same
      era as a switch -- the strap sustains the window, the previous window
      ``[D-126, D-67]`` is still sustained by the snapshot, and no reading
      lies across the boundary;
    * ``R+80`` on -- nothing: the strap now holds 14 days in the previous
      window, so rule 4(b) fails (rule 5's forward stop, ``S+80``).

    The two reports' ``reset_on`` differs in one thing the schema states:
    ``coverage_gap``'s can never precede ``window[0]`` (the resumption is
    the clip's first day while it is reported at all), ``tier_change``'s
    can (the era's true first day, ``R``, against a window clipped at
    ``D-66``). The re-attribution is an accepted cost, named in F005's
    Negative Class; the baseline is established from ``R+20`` on, so
    nothing downstream is misled. T095's tolerance does not move the
    ``R+67`` hand-off: the boundary has no strays on either side.
    """
    R = date(2026, 5, 2)
    rows = readings(SNAPSHOT, span(date(2026, 1, 1), date(2026, 4, 1)), 60.0, "snap")
    rows += readings(STRAP, span(R, R + timedelta(days=90)), 25.0, "strap")

    def at(offset: int) -> hrv_trend.HrvSeries:
        return build(rows, target=R + timedelta(days=offset))

    for offset in (0, 14, 66):
        result = at(offset)
        assert (result.reset_reason, result.reset_on) == ("coverage_gap", R), offset
        assert result.reset_on >= result.baseline_window[0], offset
    for offset in (67, 79):
        result = at(offset)
        assert (result.reset_reason, result.reset_on) == ("tier_change", R), offset
        assert result.reset_on < result.baseline_window[0], offset
    for offset in (80, 90):
        result = at(offset)
        assert (result.reset_reason, result.reset_on) == (None, None), offset
    assert {at(offset).tier for offset in (0, 14, 66, 67, 79, 80, 90)} == {STRAP}
    assert all(r.date >= R for r in at(67).baseline) and len(at(67).baseline) == 60


def test_the_gap_is_counted_in_local_days_across_29_february() -> None:
    """A 22-day silence that contains 2028-02-29 resets; the same silence
    shortened by one day to 21 does not. Windows and gaps are ``date``
    arithmetic, so the leap day is one ordinary day of the gap."""
    target = date(2028, 3, 20)
    resume_on = date(2028, 3, 8)
    twenty_two = build(gapped_history(22, resume_on, target=target), target=target)
    twenty_one = build(gapped_history(21, resume_on, target=target), target=target)

    assert date(2028, 2, 29) > resume_on - timedelta(days=22)
    assert twenty_two.reset_on == resume_on
    assert twenty_one.reset_on is None


def test_the_gap_is_counted_in_local_days_across_a_dst_transition() -> None:
    """Pacific/Auckland springs forward on 2026-09-27. A silence of exactly 21
    local days straddling it is 21 local days (no reset) even though the UTC
    span between the bounding captures is an hour shorter than 22 days; 22
    local days resets. Measured on dates, never on UTC deltas."""
    target = date(2026, 10, 20)
    resume_on = date(2026, 10, 5)
    twenty_one = build(gapped_history(21, resume_on, target=target), target=target)
    twenty_two = build(gapped_history(22, resume_on, target=target), target=target)

    assert resume_on - timedelta(days=21) < date(2026, 9, 27) < resume_on
    assert twenty_one.reset_on is None
    assert twenty_two.reset_on == resume_on


def test_health_api_overnight_can_be_the_previous_tier() -> None:
    """The tier the classifier never writes still ranks: an overnight
    baseline giving way to a sustained strap baseline is a tier change."""
    previous = readings(OVERNIGHT, span(ago(126), ago(67), 2), 40.0, "prev")
    current = readings(STRAP, span(ago(60), ago(7), 2), 40.0, "strap")

    result = build(previous + current)

    assert result.tier == STRAP
    assert result.reset_reason == "tier_change"
    assert result.reset_on == ago(60)


def test_an_open_gap_with_no_resumption_is_not_yet_a_reset() -> None:
    """The last reading is 30 days old and nothing has been captured since.
    The reset lands on the first reading *after* a gap, and there is none:
    the baseline stands until the athlete resumes. The judged week is empty,
    so the verdict is T084's ``unavailable`` regardless."""
    result = build(readings(STRAP, span(ago(66), ago(30))))

    assert result.reset_on is None
    assert result.reset_reason is None
    assert result.window == ()
    assert [r.date for r in result.baseline] == span(ago(66), ago(30))


def test_a_new_athletes_empty_first_weeks_are_not_a_gap() -> None:
    """The first reading ever is at D-40, with nothing stored before it. The
    26 empty days at the start of ``[D-66, D]`` are the start of history,
    not a silence between two eras: no reading precedes them, so nothing
    could contribute across them and there is no reset to report."""
    result = build(readings(STRAP, span(ago(40), D)))

    assert result.reset_on is None
    assert result.reset_reason is None
    assert result.baseline_window == (ago(66), ago(7))


def test_a_gap_that_opened_before_the_baseline_window_is_measured_from_the_last_reading_before_it() -> None:
    """Readings up to D-70 (before the baseline window), then silence, then
    resumption. The silence is measured from the last reading *before*
    ``[D-66, D]`` when one is known: 22 empty days (resumption at D-47)
    reset; 21 (resumption at D-48) do not. Perturbation: measuring the
    leading stretch only from D-66 makes both halves say "no reset"."""
    before = readings(STRAP, span(ago(90), ago(70)), 60.0, "before")

    twenty_two = build(before + readings(STRAP, span(ago(47), D), 40.0, "after"))
    twenty_one = build(before + readings(STRAP, span(ago(48), D), 40.0, "after"))

    assert twenty_two.reset_on == ago(47)
    assert twenty_two.reset_reason == "coverage_gap"
    assert twenty_two.baseline_window == (ago(47), ago(7))
    assert twenty_one.reset_on is None
    assert twenty_one.baseline_window == (ago(66), ago(7))


def test_a_gap_ending_in_the_judged_week_leaves_an_empty_baseline() -> None:
    """The resumption is at D-2. The reset is reported there, the clipped
    baseline window ``[D-2, D-7]`` is empty (its first day is after its
    last), and the judged window carries only the resumption readings -- so
    T084 reads the day as ``unavailable``, not as a verdict about the reset."""
    resume_on = ago(2)
    result = build(gapped_history(30, resume_on))

    assert result.reset_on == resume_on
    assert result.reset_reason == "coverage_gap"
    assert result.baseline_window == (resume_on, ago(7))
    assert result.baseline_window[0] > result.baseline_window[1]
    assert result.baseline == ()
    assert [r.date for r in result.window] == span(resume_on, D)


def test_rows_before_the_previous_window_are_still_listed_as_outside_windows() -> None:
    """The previous window ``[D-126, D-67]`` is read for the tier rule only:
    its rows stay ``outside_windows`` in ``excluded`` (T083's contract, and
    T085's ``excluded[]`` spans ``[to-66, to]``), and a row before D-126 is
    neither a reading nor a previous-window reading."""
    rows = [
        row(local(ago(127), 6), STRAP, 40.0, "before-previous"),
        row(local(ago(126), 6), STRAP, 40.0, "previous-first"),
        row(local(ago(67), 6), STRAP, 40.0, "previous-last"),
    ] + readings(STRAP, span(ago(66), D), 40.0, "cur")

    result = build(rows)

    reasons = excluded_reasons(result)
    assert reasons["before-previous"] == "outside_windows"
    assert reasons["previous-first"] == "outside_windows"
    assert reasons["previous-last"] == "outside_windows"
    assert {r.session_id for r in result.readings} == {r["session_id"] for r in rows if r["session_id"].startswith("cur-")}


def test_previous_window_readings_are_post_exclusion_too() -> None:
    """A previous window full of pre-amendment rows, null-tier rows and
    unusable values sustains nothing: with 20 such rows on the snapshot side
    and one real snapshot reading, a sustained strap baseline now is not a
    tier change (the previous window is thin)."""
    junk = []
    for i, day in enumerate(span(ago(126), ago(88), 2)):
        kind = i % 3
        if kind == 0:
            junk.append(row(local(day, 6), SNAPSHOT, None, f"amendment-{day}"))
        elif kind == 1:
            junk.append(row(local(day, 6), None, None, f"run-{day}"))
        else:
            junk.append(row(local(day, 6), SNAPSHOT, 0.0, f"zero-{day}"))
    one_real = readings(SNAPSHOT, [ago(70)], 40.0, "prev")
    current = readings(STRAP, span(ago(60), ago(7), 2), 40.0, "strap")

    result = build(junk + one_real + current)

    assert len(junk) == 20
    assert result.tier == STRAP
    assert result.reset_reason is None


def test_unusable_previous_window_values_sustain_no_tier() -> None:
    """Sixteen snapshot rows in ``[D-126, D-67]`` whose values ``ln`` cannot
    take -- ``0.0``, ``-5.0``, ``inf`` and ``nan`` -- pass the same value
    screen the exclusion chain applies inside ``[D-66, D]``: none is a
    previous-window reading, so an established strap baseline now is the
    athlete's first, not a tier change, and the silence before it is bounded
    by no earlier reading, so it is not a gap either.

    Perturbation (wave-3 mutation M9): screening the previous window on
    ``is not None`` alone lets the sixteen sustain a snapshot tier there and
    bound a 35-day gap; the existing suite stays green because its junk mix
    never carries fourteen unusable values, and this test goes red.
    """
    values = [0.0, -5.0, math.inf, math.nan]
    junk = [
        row(local(day, 6), SNAPSHOT, values[i % 4], f"junk-{day}")
        for i, day in enumerate(span(ago(126), ago(96), 2))
    ]
    current = readings(STRAP, span(ago(60), ago(7), 2), 40.0, "strap")

    result = build(junk + current)

    assert len(junk) == 16
    assert result.tier == STRAP
    assert result.reset_on is None
    assert result.reset_reason is None


def test_readings_before_the_previous_window_sustain_no_tier() -> None:
    """Twenty snapshot readings on ``[D-146, D-127]`` -- every one a day
    before the previous window opens -- five thin snapshot readings inside
    the previous window at ``[D-80, D-76]``, and an established strap
    baseline now. The previous window is the closed interval ``[D-126,
    D-67]``, and a reading before it is not read as the tier that held the
    previous era: the twenty do not make the snapshot the previous
    window's sustained tier, so this is a first established baseline, not
    a change from a snapshot era. (Until T096 this test also pinned "the
    empty days before the strap readings are not a gap" with nothing at
    all in the previous window -- which was review cycle 3's G13, the
    long layoff read as the start of history; the five readings here keep
    the leading silence at 15 days so the tier half is what this test
    isolates, and the layoff is the walk below.)

    Perturbation (wave-3 mutation M12): dropping the previous window's lower
    bound lets the twenty sustain a snapshot tier there and a
    ``tier_change`` is asserted; the existing suite stays green because its
    one pre-window row is a single reading, and this test goes red.
    """
    older = readings(SNAPSHOT, span(ago(146), ago(127)), 40.0, "older")
    thin = readings(SNAPSHOT, span(ago(80), ago(76)), 40.0, "thin")
    current = readings(STRAP, span(ago(60), ago(7), 2), 40.0, "strap")

    result = build(older + thin + current)

    assert len(older) == 20
    assert result.tier == STRAP
    assert result.reset_on is None
    assert result.reset_reason is None
    assert {excluded_reasons(result)[r["session_id"]] for r in older} == {"outside_windows"}


def test_a_layoff_longer_than_the_read_window_still_reports_the_coverage_gap() -> None:
    """G13 (review cycle 3; AC 19 "A coverage gap re-establishes the
    baseline even with no tier change", implemented as written by T096).
    The same 34-reading snapshot era, ending at ``D-100``, ``D-127`` or
    ``D-160``, then silence, then a daily resumption on the same tier from
    ``D-40``: 60, 87 and 120 days from the last pre-gap reading to the
    resumption, every one a layoff longer than ``GAP_RESET_DAYS``, and the
    baseline is re-established at the resumption on all three.

    At ``726b6db`` only the first row reported it. The leading stretch of
    ``[D-66, D]`` was bounded by the previous window's readings alone, so
    an era that ended before ``D-126`` read as the start of history and the
    discriminator was not the silence's length but whether the last
    pre-gap reading happened to fall inside ``[D-126, D-67]``. Now the
    **earliest known reading** -- among the rows, or handed in from the
    store as ``earliest_start_time`` -- tells a layoff (a reading precedes
    the window) from a new athlete (none does). Red first on the 87- and
    120-day rows.
    """
    resume_on = ago(40)
    resumed = readings(SNAPSHOT, span(resume_on, D), 40.0, "after")

    for era_end in (ago(100), ago(127), ago(160)):
        era = readings(SNAPSHOT, span(era_end - timedelta(days=33), era_end), 60.0, "before")
        assert len(era) == 34

        result = build(era + resumed)

        layoff = (resume_on - era_end).days
        assert layoff in (60, 87, 120)
        assert result.reset_reason == "coverage_gap", layoff
        assert result.reset_on == resume_on, layoff
        assert result.baseline_window == (resume_on, ago(7)), layoff
        assert result.tier == SNAPSHOT, layoff
        assert all(r.date >= resume_on for r in result.baseline), layoff
        assert {excluded_reasons(result)[r["session_id"]] for r in era} == {"outside_windows"}, layoff


def test_a_genuinely_new_athlete_keeps_reporting_no_reset() -> None:
    """The other side of G13's discriminator. The first reading ever is at
    ``D-40``; before it the store holds only rows that are **not** readings
    -- ordinary runs (null tier), pre-amendment rows (a tier, no value),
    unusable values (``0.0``, ``-5.0``, ``inf``, ``nan``) and an unknown
    tier -- going back 300 days. None of them is a reading a layoff could
    be measured from, so the 26 empty days at the start of ``[D-66, D]``
    are the start of history and nothing resets; and the same holds when no
    earlier row of any kind is known (``earliest_start_time`` ``None``) and
    when the earliest reading known to the store *is* the first row.

    Perturbation: counting any earlier stored row as a reading reports a
    ``coverage_gap`` on ``D-40`` for this athlete.
    """
    unusable = [0.0, -5.0, math.inf, math.nan]
    junk = []
    for i, day in enumerate(span(ago(300), ago(130), 5)):
        kind = i % 4
        if kind == 0:
            junk.append(row(local(day, 6), None, None, f"run-{day}"))
        elif kind == 1:
            junk.append(row(local(day, 6), SNAPSHOT, None, f"amendment-{day}"))
        elif kind == 2:
            junk.append(row(local(day, 6), STRAP, unusable[(i // 4) % 4], f"unusable-{day}"))
        else:
            junk.append(row(local(day, 6), "wrist_ppg", 40.0, f"unknown-{day}"))
    first_ever = readings(STRAP, span(ago(40), D), 40.0, "first")

    with_junk = build(junk + first_ever)
    nothing_known = hrv_trend.build_series(first_ever, AUCKLAND, D, earliest_start_time=None)
    first_row_is_earliest = hrv_trend.build_series(first_ever, AUCKLAND, D, earliest_start_time=local(ago(40), 6))

    assert len(junk) == 35
    for result in (with_junk, nothing_known, first_row_is_earliest):
        assert result.reset_on is None
        assert result.reset_reason is None
        assert result.baseline_window == (ago(66), ago(7))
        assert result.tier == STRAP


def test_the_earliest_known_reading_may_come_from_the_store_rather_than_the_rows() -> None:
    """The route reads rows back to ``D-126`` only and does not widen that
    read; it hands ``build_series`` the store's earliest reading instant
    (``db.earliest_hrv_reading``) instead. With the same 41 rows -- a daily
    resumption from ``D-40`` -- an earliest reading in March 2025 makes the
    resumption a layoff's end (``coverage_gap`` on ``D-40``), and no
    earliest reading makes it the start of history. The instant is
    bucketed into the zone like every row, and a naive one is refused for
    the same reason a naive row is.
    """
    rows = readings(STRAP, span(ago(40), D), 40.0, "after")

    layoff = hrv_trend.build_series(rows, AUCKLAND, D, earliest_start_time=local(date(2025, 3, 1), 6))
    new_athlete = hrv_trend.build_series(rows, AUCKLAND, D)

    assert layoff.reset_reason == "coverage_gap"
    assert layoff.reset_on == ago(40)
    assert layoff.baseline_window == (ago(40), ago(7))
    assert new_athlete.reset_reason is None and new_athlete.reset_on is None
    assert [r.session_id for r in layoff.series] == [r.session_id for r in new_athlete.series]
    with pytest.raises(ValueError, match="naive"):
        hrv_trend.build_series(rows, AUCKLAND, D, earliest_start_time="2025-03-01T06:00:00")


def test_two_gaps_inside_the_window_reset_on_the_later_resumption() -> None:
    """Readings at D-66 and D-65, a 22-day silence, one reading at D-42, a
    second 22-day silence, then daily readings from D-19. The scan runs
    **backwards** from the target, so the reset lands on the resumption
    after the *later* gap (D-19): that is where the current era began, and
    the D-42 reading is a stranded era of its own that contributes to
    nothing. A forward scan would land on D-42 and let the baseline span
    the second gap -- the very break the rule exists to cut.

    Perturbation (wave-3 mutation M5): scanning the reading days forwards
    leaves the existing suite green -- no other fixture holds two gaps --
    and turns this test red on ``reset_on``.
    """
    rows = (
        readings(STRAP, [ago(66), ago(65)], 60.0, "first")
        + readings(STRAP, [ago(42)], 50.0, "middle")
        + readings(STRAP, span(ago(19), D), 40.0, "current")
    )

    result = build(rows)

    assert result.reset_on == ago(19)
    assert result.reset_reason == "coverage_gap"
    assert result.baseline_window == (ago(19), ago(7))
    assert [r.date for r in result.baseline] == span(ago(19), ago(7))
    reasons = excluded_reasons(result)
    assert reasons[f"middle-{ago(42)}"] == "before_reset: coverage_gap"
    assert {reasons[f"first-{day}"] for day in (ago(66), ago(65))} == {"before_reset: coverage_gap"}


def test_the_reset_constants_are_the_construction_references() -> None:
    assert hrv_trend.GAP_RESET_DAYS == 21
    assert hrv_trend.REASON_COVERAGE_GAP == "coverage_gap"
    assert hrv_trend.REASON_TIER_CHANGE == "tier_change"
    assert hrv_trend.previous_window(D) == (ago(126), ago(67))
