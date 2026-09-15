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
from typing import NamedTuple
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


def parsed_reading(day: date, hh: int, tier: str) -> hrv_trend.Reading:
    """One already-parsed ``Reading`` taken at local ``hh:00`` on ``day``:
    what ``build_series`` hands the era rules, for the pins that call
    ``_era_boundary`` directly. The value is the same for every reading, so
    only the day, the hour and the tier tell them apart."""
    return hrv_trend.Reading(day, f"{tier}-{day}-{hh}", tier, 40.0, datetime.fromisoformat(local(day, hh)))


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
    begins. Documented in the module; pinned here.

    The precedence is over the **report**, and since T107 over that alone
    (review cycle 6, G-C6-5). This series cannot see the difference: the
    resumption and the switch are the **same day**, so the gap's clip and
    the era's coincide and no baseline day separates them. That is why the
    two pins near ``gap_and_switch`` put them on different days."""
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
# one 7-day span) -- ``hrv_trend._isolated`` states that conjunction.
# Since T098 the predicate has two halves with two consequences, stated in
# two places (T104, review cycle 5 G-C5-6): the candidacy half, fewer than
# 14 stray days, is restated at ``_era_boundary``'s own gate and decides
# whether a boundary -- and hence the baseline clip -- exists at all;
# ``_isolated`` decides whether an admitted boundary is *reported*, and --
# as the first ordering term in ``_era_boundary``'s selection key -- which
# of several admitted boundaries is taken, hence where the clip lands
# (T104 iteration 2; pinned by
# ``test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of``).
# Both sites count with ``_days`` against ``MIN_BASELINE_READINGS``, so no
# threshold has drifted; the pins below read the reported half unless they
# say otherwise.
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


class Judged(NamedTuple):
    """One target date's whole answer: what rule 4 **reports** and what the
    tolerance actually **controls**.

    T099 (review cycle 4, G-C4-4). Until T099 this helper returned the
    report alone -- ``(tier, reset_reason, reset_on)`` -- and all six
    density-tolerance pins consumed it, so the rule was pinned on its
    reporting projection while the thing it decides, whether
    ``build_series`` clips ``baseline`` and hence which readings enter the
    band, was unasserted on every one of them. That is exactly why cycle 4's
    five mutants died and the sixth lived: all five were aimed at the
    asserted projection. The record is widened rather than duplicated so a
    later pin cannot reach for the narrow one by accident.

    ``report`` keeps the old triple, so every expectation written before
    T099 stands verbatim; the four fields beside it are what T098's D4a made
    independent of it -- the clip happens whenever an era boundary exists,
    and only ``reset_reason`` / ``reset_on`` wait on the judged week (and,
    since T107, on a coverage gap not having already claimed the report).
    """

    tier: str | None
    reset_reason: str | None
    reset_on: date | None
    baseline_window: tuple[date, date]
    baseline_n: int
    band_lo: float | None
    verdict: str

    @property
    def report(self) -> tuple[str | None, str | None, date | None]:
        """The pre-T099 triple: what the athlete is *told*."""
        return (self.tier, self.reset_reason, self.reset_on)


def flat_band_lo(value: float) -> float:
    """``band.lo`` for a baseline every one of whose readings carries
    ``value``: the sample SD is 0, so the half-width is ``BAND_FLOOR`` and
    the band is ``ln(value) +/- 0.01``. The tolerance walks below hold one
    value per tier on purpose -- a value that moved with the reading count
    would make a band assertion unreadable -- so the band's *level* is the
    resolved tier's, and its interest is which window it was built over."""
    return math.log(value) - hrv_trend.BAND_FLOOR


def reported(rows: list[dict], targets: list[date]) -> list[Judged]:
    """The full ``Judged`` record on each target, for a walk: the report,
    and the ``baseline_window``, ``baseline.n``, ``band.lo`` and verdict the
    density tolerance controls through the clip."""
    out = []
    for target in targets:
        result = build(rows, target=target)
        verdict = hrv_trend.judge(result)
        out.append(
            Judged(
                tier=result.tier,
                reset_reason=result.reset_reason,
                reset_on=result.reset_on,
                baseline_window=result.baseline_window,
                baseline_n=verdict.baseline_n,
                band_lo=None if verdict.band is None else verdict.band.lo,
                verdict=verdict.verdict,
            )
        )
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
    days.

    **What the tolerance controls here** (T099). The stray moves the clip,
    so on ``SW+20`` the whole answer changes, not only the report: the
    window opens at the era's first day instead of at ``t-66``, the band is
    built over 13 strap readings instead of 47 snapshot ones, and its level
    steps from ``ln(60)`` to ``ln(25)``. *If the rule were wrong* -- if the
    single stray day made the eras interleave, as it did at 0891061 -- the
    control column would be the assertion on both series: window
    ``(t-66, t-7)`` on ``SW+20``, ``band_lo`` at the snapshot's ``ln(60)``
    level and ``hrv_unavailable``, because the snapshot era's last reading
    is ``SW`` and the judged week would hold none of it. The three walked
    days are the band's step, its cause and its persistence."""
    switch = ago(60)
    control = genuine_switch(switch, switch + timedelta(days=70))
    stray = [row(local(switch - timedelta(days=11), 6, 30), STRAP, 25.0, "strap-stray")]
    walk = [switch + timedelta(days=20), switch + timedelta(days=21), switch + timedelta(days=60)]
    era_first_day = switch + timedelta(days=1)

    control_walk = reported(control, walk)
    stray_walk = reported(control + stray, walk)

    assert [judged.report for judged in control_walk] == [
        (SNAPSHOT, None, None),
        (STRAP, "tier_change", era_first_day),
        (STRAP, "tier_change", era_first_day),
    ]
    assert [judged.report for judged in stray_walk] == [(STRAP, "tier_change", era_first_day)] * 3

    # The control: no boundary on SW+20 (the snapshot still owns the
    # baseline, so clause (b) is never asked), the clip from SW+21 on.
    assert [judged.baseline_window for judged in control_walk] == [
        (walk[0] - timedelta(days=66), walk[0] - timedelta(days=7)),
        (era_first_day, walk[1] - timedelta(days=7)),
        (era_first_day, walk[2] - timedelta(days=7)),
    ]
    assert [judged.baseline_n for judged in control_walk] == [47, 14, 53]
    assert [judged.band_lo for judged in control_walk] == pytest.approx(
        [flat_band_lo(60.0), flat_band_lo(25.0), flat_band_lo(25.0)]
    )
    assert [judged.verdict for judged in control_walk] == [
        hrv_trend.VERDICT_UNAVAILABLE,
        hrv_trend.VERDICT_NORMAL,
        hrv_trend.VERDICT_NORMAL,
    ]

    # With the stray: the clip is the era's first day on all three days,
    # and SW+20's baseline is the reported era alone -- 13 readings, one
    # short of established, which is the whole point of reporting it.
    assert [judged.baseline_window for judged in stray_walk] == [
        (era_first_day, target - timedelta(days=7)) for target in walk
    ]
    assert [judged.baseline_n for judged in stray_walk] == [13, 14, 53]
    assert [judged.band_lo for judged in stray_walk] == pytest.approx([flat_band_lo(25.0)] * 3)
    assert [judged.verdict for judged in stray_walk] == [hrv_trend.VERDICT_NORMAL] * 3

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
    ``S+48`` and ``S+49``.

    **What the tolerance controls here** (T099; corrected by T104, review
    cycle 5 G-C5-7). The stale candidacy is a clause-(b) refusal, so on
    ``S+21`` and ``S+36`` ``_era_boundary`` is never reached at all and
    the window is the **un-clipped** ``[t-66, t-7]``. With ``S`` =
    2026-05-01 that window opens on ``S+21`` at 2026-03-17 -- 45 days
    before the switch, 46 before the era's first day -- and it still holds
    **only era readings**: the resolved tier is the strap, so the band is
    the 14 strap days 2026-05-02..05-15 and nothing else. The trial
    (2026-01-31..02-13) ends 32 days before that window opens and cannot
    enter ``[t-66, t-7]`` for *any* walked target; it lives only in the
    **previous** window, which is exactly the clause-(b) stale-candidacy
    case this test names. *If the rule were wrong* -- if clause (b) let
    the stale trial through, or if the clip followed the report -- the
    first two rows would read the clipped ``(S+1, t-7)``, that is
    ``(2026-05-02, t-7)``, with ``baseline_n`` **unchanged**: the assertions
    below show the same list ``[14, 29, 30, 41, 42, 43, 60, 60]`` on the
    clipped control walk and the un-clipped trial walk, so the era's ``n``
    and the un-clipped ``n`` are the same number on every walked day.
    Among the four fields beside ``report`` -- window, ``n``, ``band_lo``
    and verdict -- ``baseline_window`` is the sole discriminator here,
    which is why the window assertion is the one that must not be
    weakened. ``report`` discriminates too, on those same first two rows
    (control ``[reset] * 7 + [(STRAP, None, None)]``, trial
    ``[(STRAP, None, None)] * 2 + [reset] * 5 + [(STRAP, None, None)]``),
    so the counterfactual named above -- clause (b) letting the stale
    trial through -- is caught by the report assertion independently of
    the window one (T104 iteration 2, narrowing a "sole discriminator"
    claim that was false across all five fields). From ``S+37``
    the clip binds until ``t-66`` reaches the era's
    first day at ``S+67`` (``t-66 == S+1``) and passes it from ``S+68``;
    the walk's next stop is ``S+80``, where it first observes that, which
    is why ``window[0]`` stops being the era's first day there and the
    last two rows are the plain sliding window. The band's
    level never moves: every reading in every one of these windows is a
    25 ms strap reading, so ``band_lo`` is the tripwire that says the clip
    never let a *snapshot* reading in."""
    switch = date(2026, 5, 1)
    control = genuine_switch(switch, switch + timedelta(days=90), snapshot_from=switch - timedelta(days=200))
    trial_days = span(switch - timedelta(days=90), switch - timedelta(days=77))
    trial = [row(local(day, 7), STRAP, 25.0, f"strap-trial-{day}") for day in trial_days]
    offsets = [21, 36, 37, 48, 49, 50, 80, 81]
    walk = [switch + timedelta(days=k) for k in offsets]
    era_first_day = switch + timedelta(days=1)
    reset = (STRAP, "tier_change", era_first_day)

    control_walk = reported(control, walk)
    trial_walk = reported(control + trial, walk)

    assert len(trial_days) == 14
    assert [in_previous_window(trial_days, t) for t in walk] == [14, 14, 13, 2, 1, 0, 0, 0]
    assert [judged.report for judged in control_walk] == [reset] * 7 + [(STRAP, None, None)]
    assert [judged.report for judged in trial_walk] == (
        [(STRAP, None, None)] * 2 + [reset] * 5 + [(STRAP, None, None)]
    )

    # The control: clipped at the era's first day until ``t-66`` reaches it
    # at ``S+67`` and passes it from ``S+68``; the walk first observes the
    # plain sliding window at its next stop, ``S+80``.
    clipped = [(max(era_first_day, t - timedelta(days=66)), t - timedelta(days=7)) for t in walk]
    assert [judged.baseline_window for judged in control_walk] == clipped
    assert [judged.baseline_n for judged in control_walk] == [14, 29, 30, 41, 42, 43, 60, 60]

    # With the trial: on S+21 and S+36 clause (b) refuses before clause (c)
    # is asked, so there is no boundary and no clip -- the window is the
    # un-clipped one and the band spans the trial as well as the era.
    assert [judged.baseline_window for judged in trial_walk] == [
        (walk[0] - timedelta(days=66), walk[0] - timedelta(days=7)),
        (walk[1] - timedelta(days=66), walk[1] - timedelta(days=7)),
    ] + clipped[2:]
    assert [judged.baseline_n for judged in trial_walk] == [14, 29, 30, 41, 42, 43, 60, 60]

    for judged_walk in (control_walk, trial_walk):
        assert [judged.band_lo for judged in judged_walk] == pytest.approx([flat_band_lo(25.0)] * 8)
        assert [judged.verdict for judged in judged_walk] == [hrv_trend.VERDICT_NORMAL] * 8


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
    and ``D+42``.

    **What the tolerance controls here** (T099). The stray is on the *old*
    tier, so it is excluded from the series as ``off_baseline_tier`` either
    way and cannot reach the band by contributing a reading; the only route
    it has to the band is the clip, and this pin closes it: the window,
    ``n``, band and verdict are identical on the two series, day for day,
    and the stray changes nothing at all. *If the rule were wrong* -- if
    the stray made the eras interleave, as at 0891061 -- the stray column
    would read ``(t-66, t-7)`` with ``n`` 33 on ``D`` instead of the era's
    ``(D-29, D-7)`` and 23, and the band would be built partly over the
    snapshot era the athlete has left. The band's level is ``ln(25)``
    throughout because the resolved tier is the strap on every walked day;
    asserting it is what would catch a clip that re-admitted 60 ms
    snapshot readings without moving ``window[0]``."""
    switch = ago(30)
    control = genuine_switch(switch, D + timedelta(days=70), snapshot_from=ago(126))
    stray = [row(local(ago(25), 7), SNAPSHOT, 60.0, "snap-stray")]
    walk = [D, D + timedelta(days=42), D + timedelta(days=52), D + timedelta(days=60)]
    era_first_day = ago(29)
    expected = [(STRAP, "tier_change", era_first_day)] * 2 + [(STRAP, None, None)] * 2

    control_walk = reported(control, walk)
    stray_walk = reported(control + stray, walk)

    assert [judged.report for judged in control_walk] == expected
    assert [judged.report for judged in stray_walk] == expected

    # Clipped while the reset is reported, the plain sliding window once
    # (b) fails -- and identical on both series, which is the claim.
    windows = [(max(era_first_day, t - timedelta(days=66)), t - timedelta(days=7)) for t in walk[:2]]
    windows += [(t - timedelta(days=66), t - timedelta(days=7)) for t in walk[2:]]
    assert [judged.baseline_window for judged in control_walk] == windows
    assert [judged.baseline_window for judged in stray_walk] == windows
    assert [judged.baseline_n for judged in control_walk] == [23, 60, 60, 60]
    assert [judged.baseline_n for judged in stray_walk] == [23, 60, 60, 60]
    for judged_walk in (control_walk, stray_walk):
        assert [judged.band_lo for judged in judged_walk] == pytest.approx([flat_band_lo(25.0)] * 4)
        assert [judged.verdict for judged in judged_walk] == [hrv_trend.VERDICT_NORMAL] * 4

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
    interleaved -- red; (no tolerance, 0891061): the same.

    **What the tolerance controls here** (T099, and the wave-10 survivor
    this pin was widened to kill). This is the **candidacy** half, which
    T098 made the half that decides the *clip*; the week half decides only
    the report. The two sides therefore differ in far more than the
    report, and until T099 nothing said so: on 13 the boundary exists, the
    baseline is clipped to the era and ``n`` is 23; on 14 the eras
    interleave, **no boundary exists at all**, and the window must be the
    un-clipped ``baseline_window(D)`` with ``n`` counting the whole of it
    -- the 8 trial days inside ``[D-66, D-7]`` as well as the era's 23.

    *If the rule were wrong* -- specifically, if the candidacy gate were
    ``stray_days <= MIN_BASELINE_READINGS``, the mutant that survived all
    358 tests at ``4d2f156`` -- the 14 row would still report
    ``(STRAP, None, None)``, because ``_isolated`` keeps its own ``< 14``
    and nothing is reported either way; its window would read
    ``(D-29, D-7)`` and its ``n`` 23. The 14-side window and ``n`` below
    are that mutant's death certificate, and they are the only assertions
    in the suite that can sign it.

    The band cannot separate the two sides here -- every reading in either
    window is a 25 ms strap reading, so ``band_lo`` is ``ln(25) - 0.01``
    on both -- and it is asserted anyway: a clip that admitted the
    *snapshot* era would move it, and an assertion that cannot change
    under the rule is still a tripwire under a wrong one."""
    switch = ago(30)
    control = genuine_switch(switch, D, snapshot_from=ago(126))

    def with_trial(days: int) -> list[dict]:
        trial_days = span(ago(72), ago(72) + timedelta(days=days - 1))
        assert in_previous_window(trial_days, D) < hrv_trend.MIN_BASELINE_READINGS
        return control + [row(local(day, 7), STRAP, 25.0, f"strap-trial-{day}") for day in trial_days]

    (thirteen,) = reported(with_trial(13), [D])
    (fourteen,) = reported(with_trial(14), [D])

    assert thirteen.report == (STRAP, "tier_change", ago(29))
    assert fourteen.report == (STRAP, None, None)

    assert thirteen.baseline_window == (ago(29), ago(7))
    assert thirteen.baseline_n == 23
    # The survivor's death certificate: un-clipped window, whole-window n.
    assert fourteen.baseline_window == hrv_trend.baseline_window(D)
    assert fourteen.baseline_n == 31

    assert thirteen.band_lo == pytest.approx(flat_band_lo(25.0))
    assert fourteen.band_lo == pytest.approx(flat_band_lo(25.0))
    assert thirteen.verdict == fourteen.verdict == hrv_trend.VERDICT_NORMAL


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
    era's last reading.

    **What the tolerance controls here** (T099). This is the **week** half,
    which T098 confined to the report -- so the interesting assertion is
    the one that says the withdrawal stops there. On ``D`` the two series
    differ in ``reset_reason`` and ``reset_on`` and **in nothing else**:
    the same clipped window ``(D-29, D-7)``, the same ``n`` 23, the same
    band and the same verdict. That is G-C4-1 stated at this population.

    *If the rule were wrong* -- if the clip still followed the report, as
    it did through ``ccf44ef`` -- the three-capture row on ``D`` would read
    the un-clipped ``baseline_window(D)`` with a larger ``n``, and its band
    would be the snapshot era's rather than the strap era's. The stray
    captures are on the old tier, so they never enter the band as
    readings; the only way a third one can reach it is by un-clipping,
    which is exactly what these assertions forbid."""
    switch = ago(30)
    control = genuine_switch(switch, D + timedelta(days=7), snapshot_from=ago(126))
    two_this_week = [row(local(ago(n), 7), SNAPSHOT, 60.0, f"snap-again-{n}") for n in (2, 0)]
    three_this_week = two_this_week + [row(local(ago(1), 7), SNAPSHOT, 60.0, "snap-again-1")]
    walk = [D, D + timedelta(days=7)]
    era_first_day = ago(29)

    two_walk = reported(control + two_this_week, walk)
    three_walk = reported(control + three_this_week, walk)

    assert [judged.report for judged in two_walk] == [(STRAP, "tier_change", era_first_day)] * 2
    assert [judged.report for judged in three_walk] == [
        (STRAP, None, None),
        (STRAP, "tier_change", era_first_day),
    ]

    # The report is the only difference: the clip is the era's first day on
    # every row of both walks, reported or withdrawn.
    windows = [(era_first_day, t - timedelta(days=7)) for t in walk]
    assert [judged.baseline_window for judged in two_walk] == windows
    assert [judged.baseline_window for judged in three_walk] == windows
    assert [judged.baseline_n for judged in two_walk] == [23, 30]
    assert [judged.baseline_n for judged in three_walk] == [23, 30]
    for judged_walk in (two_walk, three_walk):
        assert [judged.band_lo for judged in judged_walk] == pytest.approx([flat_band_lo(25.0)] * 2)
        assert [judged.verdict for judged in judged_walk] == [hrv_trend.VERDICT_NORMAL] * 2


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
    (``>`` on the day): the same.

    **What the tolerance controls here** (T099). The 05:00 capture moves
    the strays from 13 to 14, which is the **candidacy** half again, so it
    decides the clip and not only the report: the ``D-73`` series must have
    the un-clipped ``baseline_window(D+7)``, opening on ``D-59``, because
    no era boundary exists for it at all.

    *If the rule were wrong* -- under the candidacy ``<=`` mutant, or if
    the bound were the strap's first *instant* rather than the old era's
    first day -- the same-day row would keep its ``(SNAPSHOT, None, None)``
    report (the ``<=`` mutant) or gain one (the instant bound) while its
    window slid to ``(D-33, D)``; the window assertion is what separates
    the two failures from the rule.

    ``n`` and the band are the same on both sides -- 27 snapshot readings
    at 40 ms, the new era's, because the 13 captures inside the strap era
    all sit before ``D-59`` and so fall outside the un-clipped window too
    -- and both are asserted anyway. This is the one tolerance population
    whose band genuinely cannot move with the rule: the clip's only effect
    here is on ``window[0]``, and the assertion that ``n`` and ``band_lo``
    stay put is the statement that the clip removed **nothing**, which is
    the reason the two sides' verdicts agree. Both are
    ``hrv_unavailable``: the snapshot era ends at ``D-7`` and the judged
    week ``[D+1, D+7]`` holds no reading of any tier."""
    target = D + timedelta(days=7)
    rows = readings(SNAPSHOT, span(ago(126), ago(75)), 40.0, "snap-old")
    rows += readings(STRAP, span(ago(73), ago(34)), 25.0, "strap")
    rows += [row(local(day, 7), SNAPSHOT, 40.0, f"snap-inside-{day}") for day in span(ago(72), ago(60))]
    rows += readings(SNAPSHOT, span(ago(33), ago(7)), 40.0, "snap-new")
    (day_before,) = reported(rows + [row(local(ago(74), 6), SNAPSHOT, 40.0, "snap-day-before")], [target])
    (same_day,) = reported(rows + [row(local(ago(73), 5), SNAPSHOT, 40.0, "snap-same-day")], [target])

    assert len(span(ago(72), ago(60))) == 13
    assert day_before.report == (SNAPSHOT, "tier_change", ago(33))
    assert day_before.baseline_window == (ago(33), target - timedelta(days=7))
    assert same_day.report == (SNAPSHOT, None, None)
    # 13 strays: a boundary, and the clip. 14: no boundary, so the window
    # is the un-clipped one -- the half of the rule nothing read before.
    assert same_day.baseline_window == hrv_trend.baseline_window(target)
    assert day_before.baseline_n == same_day.baseline_n == 27
    assert day_before.band_lo == pytest.approx(flat_band_lo(40.0))
    assert same_day.band_lo == pytest.approx(flat_band_lo(40.0))
    assert day_before.verdict == same_day.verdict == hrv_trend.VERDICT_UNAVAILABLE


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

    Under D4a the clip is unconditional -- of the report here, and of a
    coverage gap since T107: the baseline begins on the era's
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


def test_a_fourteenth_stray_day_outside_the_judged_week_moves_the_band_and_the_verdict() -> None:
    """G-C5-1 (review cycle 5; decision log D5, T102): the **candidacy**
    half of the tolerance is a cliff, and it is on the band. This is a
    characterisation pin of an accepted, priced cost -- no behaviour
    changed under D5 -- and it exists so that the price F005's Negative
    Class and AC 17 state is a number the suite can check.

    The same ``trial_then_switch`` series as the pair above, but the extra
    snapshot captures sit **outside** the judged week (``base-10``,
    ``base-12``, ``base-14``, then ``base-16``), so the week half holds on
    both sides and only the candidacy count moves: the 10-day strap trial
    inside the snapshot era plus 3 old-tier captures after the switch is
    13 stray days, plus a fourth is 14. Both directions are pooled into
    one count (``strays = (*old[i+1:], *new[...:j])``), so the 14th day is
    supplied by an **old**-tier capture -- one that says nothing about
    whether the strap's trial was an era, and contributes nothing to the
    week mean -- and it is what certifies the trial as one.

    On 13 a boundary exists: the baseline is clipped at the switch,
    ``tier_change on 07-30``, window ``(07-30, 08-31)``, ``n`` 33,
    ``band.lo`` 3.6789, ``hrv_suppressed``. On 14 ``_era_boundary`` returns
    ``None`` -- **no boundary and therefore no clip at all**, not an
    unreported one -- so the trial's ten 25 ms readings enter the band:
    null, window ``(07-03, 08-31)``, ``n`` 43, ``band.lo`` 3.4791,
    ``hrv_normal``. The 7-day mean is 3.4965 on both, which is what makes
    the flip indefensible on the data; and ``n`` 43 / ``lo`` 3.4791 /
    ``hrv_normal`` are verbatim the ccf44ef numbers the pair above forbids
    through the *week* half. Reproduced at HEAD (537d055) for this pin;
    the numbers match the reviewer's table in T102 exactly.

    *If the rule were what the pre-T102 Negative Class row said* -- "the
    band and the verdict no longer move with the report", the candidacy
    half off the band as the week half is -- the 14 row would read the
    13 row's ``(07-30, 08-31)`` / 33 / 3.6789 / ``hrv_suppressed`` with
    only ``reset_reason`` differing, and the four 14-side assertions
    below go red. Under D5's rejected option (a) -- counting only the new
    tier's strays for candidacy (perturbed here: ``strays =
    tuple(new[from_old_first_day:j])``) -- the pin goes red at its *first*
    line instead: with the old tier's captures uncounted, the boundary at
    the trial's own start (``A_end`` 07-08, ``B_start`` 07-09, zero
    strays) beats the switch on fewest strays on **both** sides, and
    ``reset_on`` reads 2026-07-09. (a) is not a local fix but a different
    rule, which is why D5 sends it back to a decision-table pass."""
    base = date(2026, 9, 7)
    era_first_day = ago(39, base)
    thirteen_rows = trial_then_switch(base, (10, 12, 14))
    fourteen_rows = trial_then_switch(base, (10, 12, 14, 16))
    (thirteen,) = reported(thirteen_rows, [base])
    (fourteen,) = reported(fourteen_rows, [base])

    assert thirteen.report == (STRAP, "tier_change", era_first_day)
    assert fourteen.report == (STRAP, None, None)

    assert thirteen.baseline_window == (era_first_day, ago(7, base))
    assert thirteen.baseline_n == 33
    assert thirteen.band_lo == pytest.approx(3.6789, abs=5e-5)
    assert thirteen.verdict == hrv_trend.VERDICT_SUPPRESSED

    # The cliff: no boundary, no clip, the seven-week-old trial in the band.
    assert fourteen.baseline_window == hrv_trend.baseline_window(base)
    assert fourteen.baseline_n == 43
    assert fourteen.band_lo == pytest.approx(3.4791, abs=5e-5)
    assert fourteen.verdict == hrv_trend.VERDICT_NORMAL

    # The same week, the same mean, judged on two different bands.
    thirteen_mean = hrv_trend.judge(build(thirteen_rows, target=base)).ln_rmssd_7d_mean
    fourteen_mean = hrv_trend.judge(build(fourteen_rows, target=base)).ln_rmssd_7d_mean
    assert thirteen_mean == fourteen_mean == pytest.approx(3.4965, abs=5e-5)
    assert fourteen.band_lo < thirteen_mean < thirteen.band_lo


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
    old = [parsed_reading(date(2026, 7, 1), 6, SNAPSHOT)]
    old += [parsed_reading(day, 6, SNAPSHOT) for day in span(date(2026, 9, 3), date(2026, 9, 5))]
    new = [parsed_reading(day, 7, STRAP) for day in span(date(2026, 7, 1), date(2026, 7, 4))]
    new += [parsed_reading(date(2026, 9, 6), 7, STRAP)]

    assert hrv_trend._era_boundary(old, new, hrv_trend.judged_window(D)) == hrv_trend.EraBoundary(
        first_day=date(2026, 9, 6), reported=True
    )


def stray_day_tie(last_snapshot: date, first_era_value: float = 25.0, target: date = D) -> list[dict]:
    """The series that makes two era boundaries **tie on stray days**: a
    daily snapshot at 06:00 up to ``E`` (``last_snapshot``), a daily strap
    at 07:00 from ``E+1`` to ``target``, and two isolated snapshot strays
    on ``E+2`` and ``E+4``. The strap reading on ``E+1`` carries
    ``first_era_value`` so that a boundary one day too early is visible in
    the band as well as in the window.

    Three boundaries, hand-derived from the definition (an old-tier
    ``A_end``, the first new-tier reading after it, no old-tier reading in
    between; the strays are every old-tier reading after ``B_start`` and
    every new-tier reading from the old era's first local day up to
    ``A_end``):

    * ``A_end`` = ``E`` 06:00 -> ``B_start`` = ``E+1``; strays ``{E+2,
      E+4}`` -- 2 days;
    * ``A_end`` = ``E+2`` 06:00 -> ``B_start`` = ``E+2`` 07:00; strays
      ``{E+4}`` and the strap on ``E+1`` -- 2 days;
    * ``A_end`` = ``E+4`` 06:00 -> ``B_start`` = ``E+4`` 07:00; strays the
      strap on ``E+1``, ``E+2``, ``E+3`` -- 3 days.

    The first two tie at two stray days, and both are ``_isolated`` (2 < 14
    in all, 0 < 3 inside ``[D-6, D]``), so the selection's week half ties
    too and **only the tie-break separates them**: ``E+2`` if ties go to
    the later boundary, ``E+1`` if they go to the earlier."""
    first_era_day = last_snapshot + timedelta(days=1)
    rows = readings(SNAPSHOT, span(ago(126, target), last_snapshot), 60.0, "snap")
    rows += [
        row(
            local(day, 7),
            STRAP,
            first_era_value if day == first_era_day else 25.0,
            f"strap-{day}",
        )
        for day in span(first_era_day, target)
    ]
    rows += [
        row(local(last_snapshot + timedelta(days=k), 6), SNAPSHOT, 60.0, f"stray-{k}") for k in (2, 4)
    ]
    return rows


def test_the_era_boundary_tie_on_stray_days_goes_to_the_later_boundary() -> None:
    """The tie-break `_era_boundary` publishes and nothing could break
    until now (review cycle 4, G-C4-2): of several era boundaries the one
    whose strays the judged week is clear of is taken first, then the one
    with the fewest stray days -- the switch that explains the most
    readings -- **ties to the later one, the younger baseline being the
    cautious reading** (``research/00`` §1.7).

    The cycle-4 critic's series, target ``D``: a daily snapshot to
    ``E`` = ``D-70`` = 2026-06-30, a daily strap from ``E+1`` = 07-01, and
    isolated snapshot strays on 07-02 and 07-04. Two boundaries tie at two
    stray days -- ``B_start`` 07-01 and ``B_start`` 07-02 -- and, as the
    first two assertions state, they tie on the week half as well, both
    sets of strays being ``_isolated``. So the third ordering term is the
    only thing that answers, and the answer is the later boundary,
    ``first_day = 2026-07-02``.

    *Inverted* -- ties to the earlier ``A_end``, which is also what a plain
    ``max`` on ``(-stray_days,)`` returns, since ``max`` keeps the first
    maximal element -- this assertion would read
    ``EraBoundary(first_day=date(2026, 7, 1), reported=True)``: the era
    would be dated to the day the old device was still reading daily, and
    the athlete's band would be built over a reading from the era that
    ended.
    """
    last_snapshot = ago(70)
    strays = [last_snapshot + timedelta(days=k) for k in (2, 4)]
    old = [parsed_reading(day, 6, SNAPSHOT) for day in span(ago(126), last_snapshot) + strays]
    new = [parsed_reading(day, 7, STRAP) for day in span(last_snapshot + timedelta(days=1), D)]
    judged = hrv_trend.judged_window(D)

    # The two tying boundaries' strays: the later boundary's are the second
    # snapshot stray and the strap reading that now sits inside the old era.
    earlier_strays = tuple(r for r in old if r.date in strays)
    later_strays = (old[-1], new[0])
    assert len(hrv_trend._days(earlier_strays)) == len(hrv_trend._days(later_strays)) == 2
    assert hrv_trend._isolated(earlier_strays, judged) and hrv_trend._isolated(later_strays, judged)

    assert hrv_trend._era_boundary(old, new, judged) == hrv_trend.EraBoundary(
        first_day=date(2026, 7, 2), reported=True
    )


def test_the_stray_day_tie_puts_the_band_on_the_younger_era() -> None:
    """The same tie one month later, where the clip binds, so the
    tie-break is asserted on its consequence and not only on the reported
    day (T099's lesson): ``E`` = ``D-40`` = 2026-07-30, the strap era from
    07-31, snapshot strays on 08-01 and 08-03, and the era's first strap
    reading carrying 50 ms where every later one carries 25.

    Ties to the later boundary -- the younger baseline being the cautious
    reading (``research/00`` §1.7) -- so the era begins on ``E+2`` =
    08-01, ``baseline`` is clipped to ``[08-01, D-7]``, and the 50 ms
    reading of 07-31 is *before* the reset: 32 readings, all 25 ms, a flat
    band.

    **What the tie-break controls here.** *Inverted* -- ties to the
    earlier boundary -- every line below moves together:
    ``tier_change on 2026-07-31``, ``baseline_window`` ``(07-31, D-7)``,
    ``baseline_n`` 33 and ``band_lo`` 3.1796 rather than
    ``ln(25) - 0.01``, because the day whose era is in dispute is drawn
    into the band. That is the §1.7 asymmetry in one series: the earlier
    boundary can only *add* a reading whose era is unknown to the baseline
    the athlete is judged against, and a baseline pulled down by a foreign
    era reads a suppressed week as normal."""
    era_first_day = ago(40) + timedelta(days=2)

    (judged,) = reported(stray_day_tie(ago(40), first_era_value=50.0), [D])

    assert judged.report == (STRAP, "tier_change", era_first_day)
    assert judged.baseline_window == (era_first_day, ago(7))
    assert judged.baseline_n == 32
    assert judged.band_lo == pytest.approx(flat_band_lo(25.0))
    assert judged.verdict == hrv_trend.VERDICT_NORMAL


# ---------------------------------------------------------------------------
# T108 (review cycle 6, G-C5-2, raised in cycle 5 and scheduled after cycle 6
# measured its scope): the unit the tolerance counts in is **distinct local
# days**, not captures, and three sites say so -- ``_era_boundary``'s
# candidacy gate (``stray_days = len(_days(strays))``) and both halves of
# ``_isolated``. Every stray fixture written before this one seeds exactly
# one capture per stray day, so a regression to captures at any of the three
# survived the whole F005 suite: verified in-process at 7103fd7, 387/387
# green under each of the three mutants, against a control mutant (``<`` ->
# ``<=`` at the same gate) that killed 3 and proved the harness live.
#
# The population is F004's own: **an athlete who re-takes a morning after a
# bad reading.** Thirteen stray days are fourteen stray captures, and under a
# regression to captures the boundary is refused, the pre-switch trial
# re-enters the band and a suppressed week reads normal -- D5's accepted
# cliff, crossed by a re-take rather than by a capture, which is what F005's
# Negative Class and AC 17 now name this unit as deciding.
#
# Each pin below therefore asserts values that **differ between the two
# readings of the unit**. A fixture with one capture per stray day cannot;
# that is precisely how these three sites went undefended for five cycles.
# ---------------------------------------------------------------------------


def with_a_retaken_morning(trial_days: int, retake_index: int | None) -> list[dict]:
    """``test_thirteen_stray_days_...``'s series -- a genuine switch at
    ``D-30``, plus a strap trial at 07:00 wholly inside the snapshot era and
    straddling ``D-67`` so the strap never sustains the previous window --
    with one trial morning optionally **re-taken** at 08:00 after its 07:00
    capture. The re-take adds a stray *capture* and no stray *day*: it is the
    only difference between the two series the pin below compares."""
    days = span(ago(72), ago(72) + timedelta(days=trial_days - 1))
    assert in_previous_window(days, D) < hrv_trend.MIN_BASELINE_READINGS
    rows = genuine_switch(ago(30), D, snapshot_from=ago(126))
    rows += [row(local(day, 7), STRAP, 25.0, f"strap-trial-{day}") for day in days]
    if retake_index is not None:
        retaken = days[retake_index]
        rows += [row(local(retaken, 8), STRAP, 25.0, f"strap-trial-retake-{retaken}")]
    return rows


def test_a_retaken_trial_morning_is_one_more_stray_capture_and_no_more_stray_day() -> None:
    """The candidacy half counts **days**, so an athlete who re-takes a bad
    morning inside the old era still has 13 stray days -- and 14 stray
    captures. The pair below is one series and the same series with a second
    capture at 08:00 on ``D-60``, the last trial morning and one that sits
    inside ``[D-66, D-7]``, so the extra capture is real and reaches the era
    rules: it is listed as ``same_day_later_capture`` before the collapse,
    and the 07:00 capture of that day is then clipped ``before_reset:
    tier_change`` like every other trial reading. Both sides read
    ``tier_change`` on ``D-29``, ``baseline`` clipped to ``[D-29, D-7]``,
    ``n`` 23 and a flat 25 ms band.

    **The observable that differs between days and captures** is every line
    of the report and of the clip.

    *If the rule counted captures* -- ``stray_days = len(strays)`` at
    ``hrv_trend.py:1099``, the mutant this pin kills, which survived 387/387
    at 7103fd7 -- the re-taken side would read 14 stray captures, ``14 < 14``
    would fail, **no boundary would exist at all**, and this side alone would
    read ``(STRAP, None, None)`` on the un-clipped ``baseline_window(D)``
    with ``n`` 30: the abandoned snapshot era back in the band because the
    athlete took one reading twice.

    *If ``_isolated``'s candidacy half counted captures* --
    ``len(readings) < MIN_BASELINE_READINGS`` at ``hrv_trend.py:986``, the
    second mutant this pin kills -- the gate would still admit the boundary
    on 13 days, so the clip would stand at ``[D-29, D-7]`` with ``n`` 23 and
    only ``reset_reason`` / ``reset_on`` would go to ``(None, None)``. The
    two mutants are told apart here: the first moves the window and ``n``,
    the second moves the report alone."""
    (once,) = reported(with_a_retaken_morning(13, retake_index=None), [D])
    (retaken,) = reported(with_a_retaken_morning(13, retake_index=12), [D])
    retaken_on = ago(60)

    # :1099's death certificate -- the window and n it alone would move.
    assert retaken.baseline_window == once.baseline_window == (ago(29), ago(7))
    assert retaken.baseline_n == once.baseline_n == 23
    # :986's death certificate -- the report, which both mutants would move.
    assert retaken.report == once.report == (STRAP, "tier_change", ago(29))
    assert retaken.band_lo == pytest.approx(flat_band_lo(25.0))
    assert retaken.verdict == once.verdict == hrv_trend.VERDICT_NORMAL

    # The re-take is a real second capture of that morning, listed twice over.
    listing = excluded_reasons(build(with_a_retaken_morning(13, retake_index=12), target=D))
    assert listing[f"strap-trial-retake-{retaken_on}"] == hrv_trend.REASON_SAME_DAY_LATER_CAPTURE
    assert listing[f"strap-trial-{retaken_on}"] == f"{hrv_trend.REASON_BEFORE_RESET}: tier_change"


def test_isolated_counts_distinct_days_in_both_halves_not_captures() -> None:
    """``_isolated`` stated directly, which is the cleaner statement of a
    two-term predicate: through ``build_series`` each half is only visible in
    the projection it happens to control, and the week half's own count is
    then buried under the candidacy half's. Both halves are asserted here on
    stray sets whose day count and capture count **differ**, which is the
    whole of what this pin adds -- every other ``_isolated`` fixture in the
    suite seeds one capture per day, so both readings of the unit agree on
    it.

    The candidacy set is a 13-morning trial of which ``D-69`` was re-taken:
    13 days, 14 captures, none inside ``[D-6, D]``. The week set is two
    old-tier mornings inside ``[D-6, D]`` of which ``D-3`` was re-taken: 2
    days, 3 captures, and 2 days in all.

    *If the candidacy half counted captures* (``len(readings) <
    MIN_BASELINE_READINGS``, ``hrv_trend.py:986``) the first assertion would
    read ``False`` on ``14 < 14``. *If the week half counted captures*
    (``len(_within(readings, judged)) < MIN_WINDOW_READINGS``,
    ``hrv_trend.py:987``) the second would read ``False`` on ``3 < 3``. Both
    mutants survived 387/387 at 7103fd7."""
    judged = hrv_trend.judged_window(D)

    trial_days = span(ago(72), ago(60))
    candidacy = [parsed_reading(day, 7, STRAP) for day in trial_days]
    candidacy.append(parsed_reading(ago(69), 8, STRAP))
    assert (len(candidacy), len(hrv_trend._days(candidacy))) == (14, 13)
    assert hrv_trend._within(candidacy, judged) == ()
    assert hrv_trend._isolated(candidacy, judged) is True

    week = [parsed_reading(ago(4), 6, SNAPSHOT), parsed_reading(ago(3), 6, SNAPSHOT)]
    week.append(parsed_reading(ago(3), 9, SNAPSHOT))
    inside = hrv_trend._within(week, judged)
    assert (len(inside), len(hrv_trend._days(inside))) == (3, 2)
    assert len(hrv_trend._days(week)) == 2 < hrv_trend.MIN_BASELINE_READINGS
    assert hrv_trend._isolated(week, judged) is True


def test_a_retaken_morning_in_the_judged_week_is_two_old_tier_days_not_three() -> None:
    """The week half -- ``EraBoundary.reported`` -- counts **days** inside
    ``[D-6, D]``, so the third *capture* of a re-taken old-tier morning does
    not do what the third old-tier *day* does. ``trial_then_switch``'s series
    with snapshot strays on ``base-4`` and ``base-3``, then the same series
    with ``base-3`` re-taken at 09:00: 2 stray days, 3 stray captures inside
    the judged week. The switch is still reported.

    **The observable that differs between days and captures** is
    ``reset_reason`` / ``reset_on``, and only those: the candidacy half is
    untouched at 12 stray days (13 captures), so ``baseline_window``, ``n``,
    the band and the verdict are identical on both sides and under every one
    of the three mutants. The companion pin
    ``test_a_third_old_tier_capture_in_the_judged_week_moves_the_report_and_nothing_else``
    is the three-*day* side of the same threshold, and it cannot tell the two
    readings apart because its third capture is also a third day.

    *If the week half counted captures* -- ``len(_within(readings, judged)) <
    MIN_WINDOW_READINGS`` at ``hrv_trend.py:987``, the mutant this pin kills
    and the only one of the three it moves, which survived 387/387 at
    7103fd7 -- the re-taken side would read ``3 < 3`` false, the boundary
    would be admitted but not reported, and the athlete would be told
    ``(None, None)`` because they took one morning twice."""
    base = date(2026, 9, 7)
    era_first_day = ago(39, base)
    retaken_on = ago(3, base)
    once = trial_then_switch(base, (4, 3))
    retaken = [*once, row(local(retaken_on, 9), SNAPSHOT, 40.0, f"snap-stray-retake-{retaken_on}")]

    (plain,) = reported(once, [base])
    (twice,) = reported(retaken, [base])

    # :987's death certificate: the report survives the re-taken morning.
    assert twice.report == plain.report == (STRAP, "tier_change", era_first_day)
    # What the re-take does not move, under the correct rule or any mutant.
    assert twice.baseline_window == plain.baseline_window == (era_first_day, ago(7, base))
    assert twice.baseline_n == plain.baseline_n == 33
    assert twice.band_lo == pytest.approx(plain.band_lo)
    assert twice.verdict == plain.verdict == hrv_trend.VERDICT_SUPPRESSED


# ---------------------------------------------------------------------------
# T107 (review cycle 6, G-C6-5): a coverage gap must not cancel the era clip
#
# Until T107 ``build_series`` asked rule 4 only ``if reset_on is None``, so a
# coverage gap anywhere in ``[D-66, D]`` meant ``tier_change_reset`` was
# never called, no era boundary was computed and **nothing was clipped** --
# three lines below a comment reading "D4a (T098): the clip is
# unconditional". The clip and the report are two consequences of the era
# boundary (``research/00`` §5.4), and only the *report* was ever the gap's
# to win. The existing precedence pin,
# ``test_a_gap_reset_takes_precedence_over_a_tier_change``, puts the
# resumption and the switch on the **same day**, where the two clips
# coincide and the defect is invisible -- the branch beside the bug. These
# two put them on different days.
# ---------------------------------------------------------------------------

#: The target date the pair below is judged on: baseline ``[2026-07-03,
#: 2026-08-31]``, judged week ``[2026-09-01, 2026-09-07]``, previous window
#: ``[2026-05-04, 2026-07-02]``.
GAP_AND_SWITCH_D = date(2026, 9, 7)
#: The strap trial inside the snapshot era -- six stray days, comfortably
#: below the candidacy half's 14, so the era boundary is admitted and
#: reported on both series. Its readings are the ones the clip must remove.
GAP_AND_SWITCH_TRIAL = span(date(2026, 8, 1), date(2026, 8, 6))
#: The strap era proper: the switch day through the last baseline day.
GAP_AND_SWITCH_ERA = span(date(2026, 8, 13), date(2026, 8, 31))
#: The resumption after the 24-day silence, and the era's first day. The
#: whole point of the pair is that they are different days.
GAP_AND_SWITCH_RESUMPTION = date(2026, 7, 29)
GAP_AND_SWITCH_FIRST_DAY = GAP_AND_SWITCH_ERA[0]


def gap_and_switch(*, with_gap: bool) -> list[dict]:
    """A daily health_snapshot era, a six-day strap trial inside it, and a
    genuine switch to a daily strap on 2026-08-13 -- optionally with a
    **24-day silence** (2026-07-05 .. 2026-07-28) inside the snapshot era.

    The two series differ in that silence and in nothing else: the strap
    trial, the strap era and the judged week are the same rows in both, so
    the 7-day mean is byte-identical and only the baseline can move.
    ``with_gap=True`` makes the resumption 2026-07-29 the gap reset. Three
    days are then distinct on purpose: the window opens on 2026-07-03, the
    gap clips at 2026-07-29 and the era boundary is 2026-08-13 either way,
    so each clip has readings only it can remove -- the snapshot readings
    of 07-03 and 07-04 are the gap's alone, the six trial readings of
    08-01 .. 08-06 the era's alone -- and no one clip can stand in for the
    composition.
    """
    if with_gap:
        snapshot_days = span(date(2026, 5, 4), date(2026, 7, 4)) + span(
            GAP_AND_SWITCH_RESUMPTION, date(2026, 8, 12)
        )
    else:
        snapshot_days = span(date(2026, 5, 4), date(2026, 8, 12))
    rows = readings(
        SNAPSHOT, [day for day in snapshot_days if day not in GAP_AND_SWITCH_TRIAL], 60.0, "snap"
    )
    rows += readings(STRAP, GAP_AND_SWITCH_TRIAL, 26.0, "trial")
    rows += [
        row(local(day, 6), STRAP, 41.0 if i % 2 == 0 else 39.0, f"era-{day}")
        for i, day in enumerate(GAP_AND_SWITCH_ERA)
    ]
    rows += readings(STRAP, span(date(2026, 9, 1), GAP_AND_SWITCH_D), 39.0, "week")
    return rows


def test_a_coverage_gap_does_not_cancel_the_era_clip() -> None:
    """G-C6-5 (review cycle 6, HIGH): the critic's two series, differing
    only by a 24-day silence, with the judged week byte-identical.

    Reproduced at 79b4d1c before this pin was written. The silence resumes
    the **old** tier at 2026-07-29 and the switch to the strap is on
    2026-08-13, so the gap's clip (07-29) is *earlier* than the era's
    (08-13) and the two do not coincide. At 79b4d1c:

    * with the 24-day silence -- ``coverage_gap``, window 07-29 .. 08-31,
      ``n`` 25, ``band.lo`` 3.4915, ``hrv_normal``;
    * without it, the same switch -- ``tier_change``, window
      08-13 .. 08-31, ``n`` 19, ``band.lo`` 3.6771, ``hrv_suppressed``.

    The 7-day mean is 3.6636 on both. The six 26 ms readings of a strap
    trial the athlete abandoned in August -- readings ``research/00`` §5.4
    says are **never** in the band -- were pulled back into it by the gap
    alone, and a genuinely suppressed week read ``hrv_normal``: §1.7's
    least-tolerated direction, through a door neither G-C4-1 nor G-C5-1
    touched and with **no threshold to cross**.

    *If the rule were what the gate at ``hrv_trend.py:554`` implemented* --
    a gap cancels the era clip rather than only winning the report -- the
    gap row below would read ``(07-29, 08-31)`` / 25 / 3.4915 /
    ``hrv_normal`` and every assertion under "the era clip survives" goes
    red. *If instead the gap's clip were dropped in favour of the era's*
    (``baseline[0] = boundary.first_day`` rather than the later of the
    two), nothing here moves -- 08-13 is the later of the two on this
    series -- which is why the sibling pin below asserts the composition
    on the readings each clip alone would leave behind.
    """
    (with_gap,) = reported(gap_and_switch(with_gap=True), [GAP_AND_SWITCH_D])
    (without_gap,) = reported(gap_and_switch(with_gap=False), [GAP_AND_SWITCH_D])

    # The report still belongs to the gap: that half of precedence is unchanged.
    assert with_gap.report == (STRAP, "coverage_gap", GAP_AND_SWITCH_RESUMPTION)
    assert without_gap.report == (STRAP, "tier_change", GAP_AND_SWITCH_FIRST_DAY)

    # The era clip survives the gap: the same band, on the same window, on both.
    assert (
        with_gap.baseline_window
        == without_gap.baseline_window
        == (GAP_AND_SWITCH_FIRST_DAY, ago(7, GAP_AND_SWITCH_D))
    )
    assert with_gap.baseline_n == without_gap.baseline_n == 19
    assert with_gap.band_lo == pytest.approx(3.6771, abs=5e-5)
    assert with_gap.band_lo == pytest.approx(without_gap.band_lo)
    assert with_gap.verdict == without_gap.verdict == hrv_trend.VERDICT_SUPPRESSED

    # The same week, the same mean, and it is below both bands.
    means = [
        hrv_trend.judge(build(gap_and_switch(with_gap=flag), target=GAP_AND_SWITCH_D)).ln_rmssd_7d_mean
        for flag in (True, False)
    ]
    assert means[0] == means[1] == pytest.approx(3.6636, abs=5e-5)
    assert means[0] < with_gap.band_lo


def test_the_gap_keeps_the_report_while_the_era_keeps_the_clip() -> None:
    """The invariant T107 exists to establish, asserted on the clip's own
    evidence rather than on the band: ``reset_reason`` is ``coverage_gap``
    and ``reset_on`` is the resumption, **and yet** the pre-era readings
    are absent from ``baseline`` and listed ``before_reset: tier_change``.

    The two clips compose as the **later** of the two first days: neither
    pre-gap nor pre-era readings may be in the band, so the baseline's
    first day is ``max(resumption, era first day)``. Both halves are
    asserted here -- the six trial readings sit *after* the resumption
    (07-29) and *before* the era (08-13), so they survive the gap's clip
    and can only be removed by the era's, while the snapshot readings of
    07-03 and 07-04 lie inside the window and before the resumption, so
    they are the gap's alone -- which is what makes this a pin on the
    composition and not on either clip standing in for both.

    ``research/00`` §1.6's "each reading in exactly one list" is asserted
    too, over every stored row in ``[D-66, D]``: the gap branch moves
    readings out of the pre-filter ``readings`` it rebinds, and the era
    branch out of the collapsed ``series`` built from what the gap left,
    so no reading can reach both lists. It is asserted rather than argued
    because the two branches now run on the same request for the first
    time.

    *If the rule were "the gap wins the clip as well as the report"* the
    first three assertions go red -- ``baseline_window`` reads
    ``(07-29, 08-31)``, the trial ids are in ``series``, and no exclusion
    names ``before_reset: tier_change``. *If the gap branch stopped
    clipping and only reported*, the pre-resumption snapshot readings would
    carry no ``before_reset: coverage_gap`` entry, which is why they are
    named explicitly rather than left to the exhaustiveness check.

    What this pin **cannot** see is the other direction of the
    composition. The era boundary is the later of the two clips on this
    series, so replacing ``max(boundary.first_day, baseline[0])`` with
    ``boundary.first_day`` leaves every assertion here green -- including
    the ``before_reset: coverage_gap`` entries above, which the gap branch
    writes at line 557 before the composition is reached and which are
    therefore blind to how the two clips compose. The gap-later direction
    is pinned by
    ``test_the_era_clip_does_not_replace_the_gaps_when_the_gap_is_later``.
    """
    rows = gap_and_switch(with_gap=True)
    result = build(rows, target=GAP_AND_SWITCH_D)
    trial_ids = {r["session_id"] for r in rows if r["session_id"].startswith("trial-")}

    assert (result.reset_reason, result.reset_on) == ("coverage_gap", GAP_AND_SWITCH_RESUMPTION)
    assert result.baseline_window == (GAP_AND_SWITCH_FIRST_DAY, ago(7, GAP_AND_SWITCH_D))
    assert trial_ids.isdisjoint({r.session_id for r in result.series})

    reasons = excluded_reasons(result)
    assert {reasons[session_id] for session_id in trial_ids} == {"before_reset: tier_change"}
    # The gap's own clip is still doing its half: the pre-resumption
    # snapshot readings are excluded under the gap, not under the era.
    pre_gap = {
        session_id for session_id, reason in reasons.items() if reason == "before_reset: coverage_gap"
    }
    assert pre_gap and all(session_id.startswith("snap-") for session_id in pre_gap)
    assert max(session_id[len("snap-") :] for session_id in pre_gap) < str(GAP_AND_SWITCH_RESUMPTION)

    # research/00 §1.6: exactly one list, over every stored row in [D-66, D].
    in_windows = {
        r["session_id"]
        for r in rows
        if ago(66, GAP_AND_SWITCH_D)
        <= datetime.fromisoformat(r["start_time"]).astimezone(AUCKLAND).date()
        <= GAP_AND_SWITCH_D
    }
    listed = [r.session_id for r in result.series] + [entry.session_id for entry in result.excluded]
    assert sorted(session_id for session_id in listed if session_id in in_windows) == sorted(in_windows)
    assert len(listed) == len(set(listed))


#: The gap-later counterpart of the pair above: here the era boundary
#: (2026-06-25) is **earlier** than the resumption (2026-08-15), so the
#: composition at ``hrv_trend.py:617`` must keep the *gap's* first day.
#: Same target date as the pair above; baseline ``[2026-07-03, 2026-08-31]``
#: before any clip, previous window ``[2026-05-04, 2026-07-02]``.
ERA_THEN_GAP_D = date(2026, 9, 7)
#: The switch off the snapshot era: the strap's -- and the baseline era's --
#: true first day, on both variants. It sits **eight days before** ``D-66``
#: on purpose. The era's first reading must fall outside the baseline
#: window, in ``previous_readings``, because ``tier_change_reset`` reads
#: ``baseline_readings`` *after* the gap branch has clipped them: an era
#: beginning inside the window would have its first day moved to the
#: resumption by the gap's own clip and the composition would have nothing
#: to compose. Eight days is also few enough that the strap does not
#: sustain the previous window, so rule 4(b) still reads the old tier there.
ERA_THEN_GAP_ERA_FIRST_DAY = date(2026, 6, 25)
#: The resumption after the 35-day silence (2026-07-11 .. 2026-08-14),
#: inside the strap era and 51 days *after* its first day.
ERA_THEN_GAP_RESUMPTION = date(2026, 8, 15)
#: The strap days inside the baseline window that the silence swallows --
#: the gap's alone to remove, and the ones the era clip could not touch.
ERA_THEN_GAP_PRE_SILENCE = span(date(2026, 7, 3), date(2026, 7, 10))


def era_then_gap(*, with_gap: bool) -> list[dict]:
    """A daily health_snapshot era to 2026-06-24, a switch to a daily strap
    on 2026-06-25, and -- with ``with_gap=True`` -- a **35-day silence**
    (2026-07-11 .. 2026-08-14) *inside* the strap era, resuming on
    2026-08-15.

    The mirror image of ``gap_and_switch``: there the era boundary was the
    later of the two clips, here the resumption is. The two variants differ
    in the silence and in nothing else, and the judged week is the same
    rows on both.
    """
    if with_gap:
        strap_days = span(ERA_THEN_GAP_ERA_FIRST_DAY, date(2026, 7, 10)) + span(
            ERA_THEN_GAP_RESUMPTION, date(2026, 8, 31)
        )
    else:
        strap_days = span(ERA_THEN_GAP_ERA_FIRST_DAY, date(2026, 8, 31))
    rows = readings(SNAPSHOT, span(date(2026, 3, 1), date(2026, 6, 24)), 60.0, "snap")
    rows += readings(STRAP, strap_days, 40.0, "era")
    rows += readings(STRAP, span(date(2026, 9, 1), ERA_THEN_GAP_D), 39.0, "week")
    return rows


def test_the_era_clip_does_not_replace_the_gaps_when_the_gap_is_later() -> None:
    """The other direction of the same composition, which nothing in the
    tree pinned before this: the era boundary is **earlier** than the
    resumption, so ``max(boundary.first_day, baseline[0])`` must keep the
    *gap's* first day.

    ``with_gap=False`` is the control and is what makes this pin
    non-vacuous: the same rows without the silence report ``tier_change``
    on 2026-06-25 -- the era's true first day, eight days before ``D-66``,
    which is why it precedes that control's own ``window[0]`` (T094). So
    the era boundary on the gapped series is real, admitted, and 51 days
    before the resumption. With the silence the published window must
    still open on 2026-08-15 -- the day the series resumes -- because the
    baseline holds no reading before it.

    *If the composition took the era's first day rather than the later of
    the two* (``baseline = (boundary.first_day, baseline[1])``), the
    gapped series would publish ``(2026-06-25, 2026-08-31)``: an interval
    claiming 51 days the series does not contain, beside an ``n`` of 17
    and a ``reset_on`` of 2026-08-15 *inside* it. Only the reported
    interval moves under that rule -- ``baseline_readings``, the exclusion
    lists and ``series`` are all fixed before line 617, and ``n``,
    ``band`` and ``verdict`` are identical either way -- which is why this
    pin asserts ``baseline_window[0]`` against the first day the baseline
    actually holds rather than trusting any of them to notice.
    """
    gapped = build(era_then_gap(with_gap=True), target=ERA_THEN_GAP_D)
    control = build(era_then_gap(with_gap=False), target=ERA_THEN_GAP_D)

    # The gapped series first, so that a composition taking the era's first
    # day alone is reported here rather than by the control's D-66 floor,
    # which the same edit also removes.
    assert (gapped.reset_reason, gapped.reset_on) == ("coverage_gap", ERA_THEN_GAP_RESUMPTION)
    assert gapped.baseline_window == (ERA_THEN_GAP_RESUMPTION, ago(7, ERA_THEN_GAP_D))

    # The control: the era boundary exists, is admitted, and is 2026-06-25.
    assert (control.reset_reason, control.reset_on) == ("tier_change", ERA_THEN_GAP_ERA_FIRST_DAY)
    assert control.baseline_window == (ago(66, ERA_THEN_GAP_D), ago(7, ERA_THEN_GAP_D))
    assert control.reset_on < control.baseline_window[0]
    assert gapped.tier == control.tier == STRAP

    # The window is not wider than the readings it claims to be taken from:
    # the first baseline reading *is* window[0], on a series whose era
    # boundary would have opened it 51 days earlier.
    assert gapped.baseline[0].date == gapped.baseline_window[0]
    assert len(gapped.baseline) == 17

    # The strap days the silence swallowed are the gap's, listed under it:
    # inside the un-clipped window, after the era boundary, so no era clip
    # could have removed them.
    reasons = excluded_reasons(gapped)
    pre_silence = {f"era-{day}" for day in ERA_THEN_GAP_PRE_SILENCE}
    assert {reasons[session_id] for session_id in pre_silence} == {"before_reset: coverage_gap"}
    assert pre_silence.isdisjoint({r.session_id for r in gapped.series})

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
    ``coverage_gap``'s can never precede ``window[0]`` *by outliving its
    own clip* (the resumption is the clip's first day while the gap is
    reported at all), ``tier_change``'s can (the era's true first day,
    ``R``, against a window clipped at ``D-66``). Since T107 a
    ``coverage_gap``'s can precede ``window[0]`` for the other reason --
    an era boundary clipping later than the resumption -- which is not
    this series: here the two clips coincide on ``R``, which is why the
    ``>=`` assertions below still hold and why this pin could not see
    G-C6-5. The re-attribution is an accepted cost, named in F005's
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


# ---------------------------------------------------------------------------
# T112 (review cycle 7, G-C7-2): report-liveness is rule 4's three
# conditions, and clause (a) can lapse on its own
#
# T109 published "liveness is rule 4's three conditions together, not any one
# of them alone" in both contract copies and through ``app.openapi()``, and
# its whole test footprint was string containment: one literal swapped in
# ``RESET_REASON_CLAIMS``, two appended to ``RESET_REASON_WITHDRAWN``,
# docstrings rewritten. Nothing behavioural. If ``build_series`` regressed so
# that a lapsed clause (a) still reported ``tier_change``, every assertion in
# ``test_the_two_copies_of_the_reset_reason_contract_publish_the_same_claims``
# stayed green -- the claim was a substring of prose asserted to appear in two
# copies of that prose. This is the series T109 reproduced in-process and then
# discarded, landed as a pin that the implementation can break.
# ---------------------------------------------------------------------------

#: T109's reproduction, stated as the fixture's own constants rather than
#: relative to the suite-wide ``D``: the switch day, the target 66 days later,
#: and the two strap runs the pin and its control differ by.
R_SWITCH = date(2026, 5, 1)
D_SWITCH = R_SWITCH + timedelta(days=66)
CLAUSE_A_LAPSED_DAYS = 11
CLAUSE_A_HELD_DAYS = MIN_BASELINE_READINGS = hrv_trend.MIN_BASELINE_READINGS


def clause_a_series(strap_days: int) -> list[dict]:
    """A daily ``health_snapshot`` for 200 days up to ``R-1``, then a daily
    ``chest_strap_raw`` on ``R`` for ``strap_days`` days and never again."""
    return readings(
        SNAPSHOT, span(R_SWITCH - timedelta(days=200), R_SWITCH - timedelta(days=1)), 40.0, "snap"
    ) + readings(STRAP, span(R_SWITCH, R_SWITCH + timedelta(days=strap_days - 1)), 40.0, "strap")


def test_clause_a_lapsing_nulls_the_report_while_clause_b_and_the_week_half_still_hold() -> None:
    """The published claim, attacked at the one condition its prose was
    written to be about. At ``D = R+66`` the strap holds **11** of the 14
    distinct days clause (a) needs in ``[D-66, D-7]``, so (a) has lapsed --
    while clause (b) demonstrably holds (the snapshot holds all **60** days
    of ``[D-126, D-67]``, so the previous window is sustained by a tier other
    than the resolved one) and the week half holds too. ``reset_reason`` must
    be ``None`` anyway.

    All three are asserted, not just the null. A pin that asserted only the
    null could not tell "(a) lapsed" from "nothing happened here", which is
    the branch-beside-the-bug shape this project keeps escaping through
    (``review-catches-what-tests-cannot``). The control is the same series
    with three more strap days and nothing else changed: (a) then holds and
    ``tier_change`` **is** reported, on the same ``reset_on``, the same
    window and the same judged week -- so clause (a)'s day count is the only
    thing that moved, and the week half is shown to hold by the report
    itself rather than by an assertion about an empty set.

    Authorship (``contract-tables-need-an-independent-oracle``): the series,
    the day counts and the expected null come from the task text and
    ``research/00`` §5.4's three conditions, not from reading
    ``build_series``. The two clause-(b) facts below are computed from the
    **fixture's own days** rather than from the result's ``series``, which is
    already tier-filtered.

    Perturbation (T112, recorded in the Delivered note): deleting
    ``tier_change_reset``'s clause (a) gate -- the
    ``_tier_counts(baseline_readings).get(tier, 0) < MIN_BASELINE_READINGS``
    early return -- makes an 11-day strap report ``tier_change on R`` and
    turns this test red; restored, it is green again.
    """
    judged = hrv_trend.judged_window(D_SWITCH)
    previous = hrv_trend.previous_window(D_SWITCH)

    lapsed = hrv_trend.build_series(clause_a_series(CLAUSE_A_LAPSED_DAYS), AUCKLAND, D_SWITCH)
    held = hrv_trend.build_series(clause_a_series(CLAUSE_A_HELD_DAYS), AUCKLAND, D_SWITCH)

    # Clause (b) holds: the previous window is sustained by the snapshot,
    # which is not the resolved tier. Counted over the fixture's own days.
    previous_snapshot_days = [
        day for day in span(R_SWITCH - timedelta(days=200), R_SWITCH - timedelta(days=1))
        if previous[0] <= day <= previous[1]
    ]
    sustained = hrv_trend.sustained_tier(
        hrv_trend._tier_counts(parsed_reading(day, 6, SNAPSHOT) for day in previous_snapshot_days)
    )
    assert len(previous_snapshot_days) == 60
    assert sustained == SNAPSHOT
    assert lapsed.tier == STRAP and sustained != lapsed.tier, "(b) holds: the old tier sustains"

    # The week half holds: no reading of either tier falls in [D-6, D], so
    # no era boundary can have a stray day there -- and the control, whose
    # judged week is identical, is reported, which is the week half holding.
    assert not [day for day in span(*judged) if day <= R_SWITCH + timedelta(days=CLAUSE_A_HELD_DAYS)]
    assert hrv_trend._isolated((), judged)
    assert held.reset_reason == "tier_change" and held.reset_on == R_SWITCH

    # Clause (a) has lapsed -- and that alone nulls the report.
    assert len(lapsed.baseline) == CLAUSE_A_LAPSED_DAYS < MIN_BASELINE_READINGS
    assert lapsed.reset_reason is None
    assert lapsed.reset_on is None

    # Nothing else separates the two runs.
    unclipped = (D_SWITCH - timedelta(days=66), D_SWITCH - timedelta(days=7))
    assert lapsed.baseline_window == held.baseline_window == unclipped
    assert lapsed.tier == held.tier == STRAP
    assert len(held.baseline) == CLAUSE_A_HELD_DAYS


def test_the_reset_constants_are_the_construction_references() -> None:
    assert hrv_trend.GAP_RESET_DAYS == 21
    assert hrv_trend.REASON_COVERAGE_GAP == "coverage_gap"
    assert hrv_trend.REASON_TIER_CHANGE == "tier_change"
    assert hrv_trend.previous_window(D) == (ago(126), ago(67))
