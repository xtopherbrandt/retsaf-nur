"""T092 -- ``metrics/hrv_trend.py``: baseline re-establishment (F005).

The two reset triggers that survive the decision log: a **coverage gap** (more
than ``GAP_RESET_DAYS`` = 21 consecutive local days with no entry in the
post-exclusion series, the reset landing on the first reading after it) and
a **sustained tier change** (the tier that sustains the baseline over
``[D-66, D-7]`` differs from the tier that sustained it over the previous
60-day window ``[D-126, D-67]``, and that tier also owns the baseline -- it
is the resolved tier, T093). There is no timezone-change reset.

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
# (F005 "Negative Class"; decision log 2026-09-10). The reset rule compares
# the previous window's tier with the tier the current baseline window
# *sustains* (>= 14, highest fidelity), never with a tier the judged week
# chose or a fallback the empty week forced -- and a change is reported only
# when the sustained tier both changed and owns the baseline. Each test
# went red against the earlier comparison (T093's Delivered note).
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
    (20 strap, 30 snapshot); nothing captured in ``[D-6, D]``. No candidate
    covers the week, so the densest tier -- the snapshot -- takes the
    baseline for this empty week. The tier the window *sustains* is still
    the strap, the same as before, so nothing changed and nothing resets."""
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
# adversarial rows
# ---------------------------------------------------------------------------


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
    before the previous window opens -- and an established strap baseline
    now. The previous window is the closed interval ``[D-126, D-67]``, and
    a reading before it is read by nothing: neither as the tier that held
    the previous era nor as the reading a leading gap is measured from. So
    this is a first established baseline, not a change from a snapshot era,
    and the 66 empty days before the strap readings are not a gap.

    Perturbation (wave-3 mutation M12): dropping the previous window's lower
    bound lets the twenty sustain a snapshot tier there and bound the
    silence; the existing suite stays green because its one pre-window row
    is a single reading, and this test goes red.
    """
    older = readings(SNAPSHOT, span(ago(146), ago(127)), 40.0, "older")
    current = readings(STRAP, span(ago(60), ago(7), 2), 40.0, "strap")

    result = build(older + current)

    assert len(older) == 20
    assert result.tier == STRAP
    assert result.reset_on is None
    assert result.reset_reason is None
    assert {excluded_reasons(result)[r["session_id"]] for r in older} == {"outside_windows"}


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
