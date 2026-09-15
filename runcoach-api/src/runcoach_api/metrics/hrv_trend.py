"""The resting-HRV trend (F005, spec §3.7): the series, the band and verdict, and the resets.

This module turns stored ``sessions`` rows into **a clean one-reading-per-
local-day series for one source tier** (``build_series``, T083), judges the
target date against the SWC band built over that series (``judge``, T084),
and re-establishes the baseline after a coverage gap or a sustained tier
change (``coverage_gap_reset`` / ``tier_change_reset``, T092). The three
sections follow in that order. The window constants and every exclusion and
reset reason are declared together at the top, because ``build_series``
reads them all; the band's own constants sit with the band.

**Pure, by design.** ``build_series`` takes rows, a ``ZoneInfo`` and a target
date and returns a dataclass; it imports neither ``config`` nor ``db`` nor
``fastapi``. ``pipeline.py`` states the reason this pattern exists for
``hrv_classification`` and it holds here: the route resolves the zone and
reads the rows, and the suites drive the computation directly with hand-built
dicts and no database. Rows are read **by key** (``session_id``,
``start_time``, ``resting_rmssd_ms``, ``hrv_source_tier``), so a
``sqlite3.Row`` from ``db.read_hrv_rows`` and a plain dict are interchangeable.

**The order is normative** (construction reference, "Series construction"):

1. **Exclude** rows that are not readings this feature may trend -- rows
   outside ``[D-66, D]``, the pre-amendment window
   (``hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL``, the
   in-Python companion of ``db.PRE_AMENDMENT_WINDOW_PREDICATE``), null-tier
   rows (every ordinary session, and every capture F004's quality gates
   failed), a tier string the enum does not name, and a value ``ln`` cannot
   take.
2. **Resolve the baseline tier**: among the tiers read on at least
   ``MIN_BASELINE_READINGS`` **distinct local days** in the baseline window
   **and** read within ``RECENCY_TOLERANCE_DAYS`` of the most recent
   baseline-window day of any such tier (the *candidates*; the recency
   condition is T117's, stated in full at ``RECENCY_TOLERANCE_DAYS`` and at
   ``resolve_baseline_tier``, and the comparison is between candidates, so a
   lone candidate is its own reference and is never struck), the
   highest-fidelity one that **also covers the judged week** with at least
   ``MIN_WINDOW_READINGS`` distinct local days;
   when no candidate covers the week, the candidate **the athlete used
   last** -- the one whose latest reading in the baseline window is most
   recent, ties by count then fidelity (T094); when there is no candidate
   at all, the tier read on the **most** days in the baseline window, ties
   to fidelity. Every count in rules 1-3 is in distinct local days, the
   unit ``judge`` reports as ``baseline_n`` and ``readings_in_window``
   (T095; before it rules 1-3 counted captures, so one re-taken morning
   handed a week to a tier that could not judge it) --
   not the highest tier present, so one borrowed chest-strap capture cannot
   demote a 45-reading Health Snapshot baseline to ``n=1`` (§3.7.3: "never
   merged into the same band"), a two-week strap trial abandoned two months
   ago cannot blank the snapshot's verdict (T093; F005 "Negative Class"),
   and a thin week cannot hand a switched athlete's baseline back to the
   device they abandoned (T094).
3. **Filter** to that tier.
4. **Collapse** to one reading per local day: the earliest capture of the
   day, within the tier.

Doing (4) before (3) silently drops any day whose highest-fidelity capture is
off the baseline tier even when a usable on-tier reading existed, and
``readings_in_window`` decides ``hrv_unavailable`` -- so the order is
outcome-determining, not cosmetic.

**Days are the athlete's local days.** ``start_time`` is stored as an aware
UTC ISO string (``mapping.py``, ``+00:00``). Each row is bucketed with
``datetime.fromisoformat(...).astimezone(zone).date()`` *per row*: the offset
is not a constant across a DST transition, so a single precomputed offset is
wrong, and slicing ``start_time[:10]`` is the UTC day -- the defect
``athlete_timezone`` exists to prevent. A **naive** ``start_time`` is a defect
of the writer, not an input to guess at: ``astimezone`` on a naive value
silently assumes the machine's zone, so it raises instead.

**This module does not re-gate.** A capture whose beat stream failed F004's
artefact-survival gate carries no tier and no value; it is excluded as
``null_tier`` and its retained-beat fraction is never read here -- ingestion
discharged that gate, and a re-applied one would describe a row that cannot
exist in this series.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

#: The baseline is the 60 local days ending a week before the target
#: (construction reference "Constants"; the Plews/Altini lineage the register
#: cites). Closed interval ``[D-66, D-7]``; the judged week is ``[D-6, D]``.
#: The two are disjoint so a suppressed week cannot lower its own band.
BASELINE_DAYS = 60
#: The judged window, in local days.
WINDOW_DAYS = 7
#: A tier needs readings on at least this many **distinct local days** in
#: the baseline window to be a *candidate* for the baseline
#: (``resolve_baseline_tier``: the candidate must also cover the judged
#: week, else the candidate read last takes it; with no candidate, the
#: densest tier). Days, not captures (T095): ``judge``'s ``established``
#: counts the collapsed one-per-day series, and the same number must mean
#: the same thing wherever the response echoes it. Since T117 candidacy
#: also carries a recency condition (``RECENCY_TOLERANCE_DAYS``), so a
#: trial abandoned more than four weeks before the tier the athlete is
#: actually on was last read is no longer a candidate -- until T117 it
#: stayed one until it aged out of the window, which is the defect
#: IDEA-064 named and F005's Negative Class priced as "stale candidacy".
#: ``tier_change_reset`` reads this constant as "sustains" and as the
#: tolerance's candidacy threshold -- **in neither place with the recency
#: condition attached**, see that function -- and T084 as "established".
MIN_BASELINE_READINGS = 14
#: A silence of **more than** this many consecutive local days with no entry
#: in the post-exclusion series re-establishes the baseline (T092;
#: construction reference "Constants": survives a taper, a holiday or a
#: two-week illness; catches an era break). 21 does not reset; 22 does.
GAP_RESET_DAYS = 21
#: A candidate for the baseline must also have been read **recently**,
#: relative to the other candidates: a tier whose latest local day in the
#: baseline window falls more than this many days behind the latest such
#: day of any candidate is struck from the candidate set before rule 2 is
#: asked (``resolve_baseline_tier``; T117, 2026-09-15, closing IDEA-064).
#: The comparison is between candidates, not against an absolute offset
#: from ``D-7``, so the response's ``thresholds`` block gains no key.
#:
#: Why 28, and not any other value in the measured green band
#: ``[18, 44]``, re-measured 2026-09-15 and **scoped to the 394 tests of
#: the five HRV suites that predate T117's own tolerance pin** (T121: the
#: five suites collect 395, and the 395th is
#: ``test_rule_1s_recency_admits_a_candidate_up_to_the_tolerance_and_strikes_it_past_it``,
#: which asserts ``RECENCY_TOLERANCE_DAYS == 28`` and hardcodes its gap
#: rows -- so it is red at every N != 28 by construction, and over all 395
#: the green band is ``{28}``. The band is a statement about the rest of
#: the suite, and it was published without that scope until T121 measured
#: it). The two ends are real and are what the band means: N=17 reds
#: ``test_a_clean_ended_strap_trial_reads_as_a_switch_until_the_snapshot_covers_a_week_again``
#: -- a legitimately resuming snapshot is struck -- and N=45 reds
#: ``test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band``
#: -- the July trial is re-admitted:
#:
#: * **28 = 4 x ``WINDOW_DAYS``** -- four judged weeks. Stated in the
#:   rule's own unit, it says a tier read at least once in any four
#:   consecutive judged weeks is never struck for staleness.
#: * **28 > ``GAP_RESET_DAYS`` (21)**, which is the load-bearing half.
#:   The two mechanisms answer different questions and must not overlap:
#:   ``coverage_gap_reset`` measures the silence of the series as a whole
#:   (every tier together) and calls more than 21 days a *break*; this
#:   measures one tier's silence *while another tier kept capturing*. A
#:   tolerance of 21 or less would let relative staleness strike a tier
#:   for a silence shorter than the shortest silence this feature is
#:   willing to call a break -- two rules disagreeing about the same
#:   number of days.
#:
#:   **The two rules count in different units, and the seam is stated
#:   here in one** (measured and corrected 2026-09-15, T121). This gate
#:   compares two ``last_read`` **days**; ``_silence_between`` is
#:   ``(later - earlier).days - 1``. So a day difference of ``g`` is a
#:   silence of ``g - 1`` whole days, staleness strikes at
#:   ``g > RECENCY_TOLERANCE_DAYS`` -- that is, at a silence of
#:   ``RECENCY_TOLERANCE_DAYS`` days or more -- and a break is a silence
#:   of **more than** ``GAP_RESET_DAYS`` days. The two therefore stop
#:   disagreeing exactly at ``RECENCY_TOLERANCE_DAYS >= 22``, which is
#:   ``> GAP_RESET_DAYS``: the strict inequality above is the right one.
#:   It is pinned by
#:   ``test_rule_1s_recency_admits_a_candidate_up_to_the_tolerance_and_strikes_it_past_it``,
#:   by its **``gap == GAP_RESET_DAYS + 1``** row -- a silence of exactly
#:   ``GAP_RESET_DAYS`` days, admitted at a tolerance of 22 and struck at
#:   21. Until T121 this comment named that suite's ``GAP_RESET_DAYS``
#:   row instead, which does not pin the seam at all: at a tolerance of 21
#:   a day difference of 21 is still ``<=`` the tolerance, so that row
#:   stays green, and what actually reddened at 21 was the hardcoded 27
#:   row the prose treated as ordinary.
#:
#: Which of the two fires first is not a race but a partition, and it is
#: decided by *where the readings are*, not by 28 against 21. When the
#: whole series goes silent for more than 21 days, the gap reset clips
#: ``[D-66, gap_reset_on)`` out of the window before any tier is counted,
#: so the pre-gap era never reaches candidacy and this rule is never
#: consulted. When one tier goes silent while another carries the series,
#: there is no gap to report and this rule is the only one that acts. The
#: gap reset therefore always fires first *where it fires at all* -- and
#: the exception, the case where neither applies, is a lone tier with no
#: rival: it is its own most recent candidate, so its gap is zero and it
#: is never struck however old it is (that is the coverage gap's
#: population, not this one).
RECENCY_TOLERANCE_DAYS = 28

#: The tier enum (``models.Session.hrv_source_tier``), highest fidelity first.
#: The order is the authority's (``research/00`` §3.3 and its register row:
#: chest-strap raw RR, then Health Snapshot, then Health API overnight) and
#: §2.4.5's own numbered list. ``health_api_overnight`` is in the enum but is
#: never written by the classifier; it is ranked all the same.
TIER_FIDELITY: tuple[str, ...] = ("chest_strap_raw", "health_snapshot", "health_api_overnight")
_FIDELITY_RANK = {tier: rank for rank, tier in enumerate(TIER_FIDELITY)}

# Exclusion reasons. Each stored row inside ``[D-66, D]`` that is not in the
# series is listed with exactly one of these, so a verdict is reproducible
# from what the response reports (``research/00`` §1.6). The parameterised
# ones carry their argument after ``": "``.
REASON_OUTSIDE_WINDOWS = "outside_windows"
REASON_PRE_AMENDMENT_WINDOW = "pre_amendment_window"
REASON_NULL_TIER = "null_tier"
REASON_UNKNOWN_TIER = "unknown_tier"
REASON_UNUSABLE_VALUE = "unusable_value"
REASON_OFF_BASELINE_TIER = "off_baseline_tier"
REASON_SAME_DAY_LATER_CAPTURE = "same_day_later_capture"
#: A reading inside ``[D-66, D]`` that predates the day the current
#: baseline era began (T092): it contributed to neither the baseline nor
#: the window, and ``research/00`` §1.6 wants it listed rather than
#: silently dropped. Parameterised with the reason the era began:
#: ``before_reset: coverage_gap`` for a resumption after a silence, and
#: ``before_reset: tier_change`` for the readings of the resolved tier that
#: predate its era boundary -- **whether or not the ``tier_change`` is
#: reported**, because the clip is a property of the capture history and
#: the report is a statement about what the athlete is told (D4a,
#: decision log 2026-09-13; T098, which made this member reachable).
REASON_BEFORE_RESET = "before_reset"

#: The two reset reasons (``HrvSeries.reset_reason``, T092). There is no
#: timezone-change reset (decision log, 2026-09-09).
REASON_COVERAGE_GAP = "coverage_gap"
REASON_TIER_CHANGE = "tier_change"


@dataclass(frozen=True)
class Reading:
    """One stored row that is a reading: bucketed, tiered, and ``ln``-safe."""

    date: date
    session_id: str
    tier: str
    rmssd_ms: float
    #: The aware UTC instant, kept for the within-day ordering and for the
    #: response; never the naive value.
    start_time: datetime


@dataclass(frozen=True)
class Exclusion:
    """One stored row that contributed to nothing, and why."""

    date: date
    session_id: str
    reason: str


@dataclass(frozen=True)
class HrvSeries:
    """The series for one target date, bucketed in one zone.

    ``series`` is the deliverable: one reading per local day, on ``tier``,
    in date order, over ``[D-66, D]``. ``baseline`` and ``window`` are its
    two disjoint slices. ``readings`` is the post-exclusion, pre-filter set
    of every tier -- what T092 measures the coverage gap on, because a gap
    is "no entry in the post-exclusion series", not "no stored row".

    **The two clips are not symmetric in what they rebind, deliberately.**
    The gap clip runs before the tier is resolved, so it rebinds
    ``readings`` itself -- the gap's era is an era of *every* tier. The
    era-boundary clip runs after the collapse and is a statement about the
    **resolved tier's** era, so it rebinds ``series`` alone; rebinding
    ``readings`` there would drop on-tier pre-boundary readings from the
    population the gap rule is defined over while leaving the off-tier
    ones, which is a different set from either. The cost is the standing
    advisory that ``readings`` still contains readings that ``excluded``
    lists ``before_reset: tier_change`` -- an overlap inside this dataclass
    only, unreachable from ``main.py``, which reads ``series``,
    ``baseline``, ``window`` and ``excluded`` and never ``readings``. T107
    left it exactly as it was rather than widen it: the era clip's
    population is unchanged (``series``), and every list the response is
    built from stays disjoint and exhaustive.
    """

    target_date: date
    #: The IANA key the rows were bucketed in, so the response can report it.
    timezone: str
    baseline_window: tuple[date, date]
    judged_window: tuple[date, date]
    #: ``None`` only when no reading of any tier exists in ``[D-66, D]``.
    tier: str | None
    readings: tuple[Reading, ...]
    series: tuple[Reading, ...]
    baseline: tuple[Reading, ...]
    window: tuple[Reading, ...]
    excluded: tuple[Exclusion, ...]
    #: T092. The local day the current baseline era began, when a reset is
    #: **reported**, and why: ``REASON_COVERAGE_GAP`` or
    #: ``REASON_TIER_CHANGE``. Both ``None`` when nothing is reported.
    #: Defaulted so a series can be built without naming them.
    #: ``baseline_window`` is **not** a function of these: it is clipped to
    #: ``[max(D-66, <a gap resumption>, <the era's first day>), D-7]`` --
    #: the era boundary's half applies whenever a boundary exists, whether
    #: or not rule 4 reports it (D4a, decision log 2026-09-13; T098) **and
    #: whether or not a coverage gap has already fired** (T107, review
    #: cycle 6 G-C6-5: until then any gap cancelled the era clip outright).
    #: A tier-change era's first day may precede the window (T094). So a
    #: ``null`` ``reset_reason`` on a clipped window is a reachable state,
    #: and it is the athlete being told nothing about a baseline that is
    #: nonetheless era-correct; so, since T107, is a ``coverage_gap``
    #: whose ``reset_on`` precedes ``baseline_window[0]`` because the era
    #: boundary clipped later than the resumption.
    reset_on: date | None = None
    reset_reason: str | None = None


def baseline_window(target_date: date) -> tuple[date, date]:
    """The closed local-date interval ``[D-66, D-7]``."""
    last = target_date - timedelta(days=WINDOW_DAYS)
    return last - timedelta(days=BASELINE_DAYS - 1), last


def judged_window(target_date: date) -> tuple[date, date]:
    """The closed local-date interval ``[D-6, D]``."""
    return target_date - timedelta(days=WINDOW_DAYS - 1), target_date


def previous_window(target_date: date) -> tuple[date, date]:
    """The closed local-date interval ``[D-126, D-67]``: the 60-day baseline
    window immediately before ``baseline_window(target_date)``. The
    sustained-tier-change rule (T092) resolves the tier here and compares."""
    last = target_date - timedelta(days=WINDOW_DAYS + BASELINE_DAYS)
    return last - timedelta(days=BASELINE_DAYS - 1), last


def is_pre_amendment_window(row: Mapping[str, Any]) -> bool:
    """The in-Python companion of ``db.PRE_AMENDMENT_WINDOW_PREDICATE``:
    ``hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL``.

    A row F004 recognised as a capture before the 2026-09-06 amendment, whose
    reading was never backfilled into the resolved column. A stored row, not
    a reading -- ``ln`` must never be evaluated on it. The test suite checks
    this predicate against the SQL one by running both over the same rows.
    """
    return row["hrv_source_tier"] is not None and row["resting_rmssd_ms"] is None


def local_day(session_id: str, start_time: str, zone: ZoneInfo) -> tuple[date, datetime]:
    """Bucket one stored ``start_time`` into ``zone``; returns the local date
    and the parsed aware instant.

    ``fromisoformat`` (3.11+) accepts ``+00:00``, ``Z`` and a naive string
    alike, so the suffix is never string-matched. A naive value would be
    bucketed by ``astimezone`` into the *machine's* zone with no error, which
    is the one outcome worse than a crash for a readiness verdict; the writer
    never produces one, so it is raised as a defect naming the row.
    """
    instant = datetime.fromisoformat(start_time)
    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
        raise ValueError(
            f"session {session_id!r} has a naive start_time {start_time!r}; the writer stores aware UTC, "
            "and a naive value cannot be bucketed into a local day without guessing its zone"
        )
    return instant.astimezone(zone).date(), instant


def _within(readings: Iterable[Reading], window: tuple[date, date]) -> tuple[Reading, ...]:
    """The readings whose local day falls inside the closed interval ``window``."""
    first, last = window
    return tuple(r for r in readings if first <= r.date <= last)


def _days(readings: Iterable[Reading]) -> set[date]:
    """The distinct local days ``readings`` fall on -- the unit every count is
    taken in (T095)."""
    return {r.date for r in readings}


def _is_usable_value(value: Any) -> bool:
    """``ln`` is defined on exactly the strictly positive finite reals.
    ``inf`` and ``nan`` both clear a ``<= 0`` test (IDEA-034), so the
    finiteness check is explicit."""
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _exclusion_reason(row: Mapping[str, Any]) -> str | None:
    """The exclusion chain for one stored row: the reason it is not a
    reading, or ``None`` when it is one. The screens run in the normative
    order (module docstring, step 1) and each row gets exactly the first that
    fires, so a verdict is reproducible from the listed reasons.

    The one implementation for both populations ``build_series`` screens:
    the rows inside ``[D-66, D]``, which are listed with this reason, and the
    rows of the previous window ``[D-126, D-67]``, which are listed as
    ``outside_windows`` but whose *readings* the tier-change and gap rules
    (T092) still need. A second predicate for the latter would be a second
    copy of this chain, and the next screen added here would then split the
    two populations the tier-change rule compares (sprint-005 review, S1).
    """
    tier = row["hrv_source_tier"]
    value = row["resting_rmssd_ms"]
    if is_pre_amendment_window(row):
        return REASON_PRE_AMENDMENT_WINDOW
    if tier is None:
        return REASON_NULL_TIER
    if tier not in _FIDELITY_RANK:
        return f"{REASON_UNKNOWN_TIER}: {tier}"
    if not _is_usable_value(value):
        return f"{REASON_UNUSABLE_VALUE}: {value!r}"
    return None


def _tier_counts(readings: Iterable[Reading]) -> Counter[str]:
    """Distinct local days per tier -- the input ``resolve_baseline_tier``
    and ``sustained_tier`` take. **Days, not captures** (T095; decision log
    2026-09-12): two captures on one morning are one day, exactly as the
    same-day collapse and ``judge`` count them, so a re-taken morning can
    neither cover a week nor make a tier a candidate."""
    return Counter(tier for tier, _ in {(r.tier, r.date) for r in readings})


def _of_tier(readings: Iterable[Reading], tier: str) -> tuple[Reading, ...]:
    """The readings captured on ``tier`` -- one era's readings, in a window."""
    return tuple(r for r in readings if r.tier == tier)


def _last_read(readings: Iterable[Reading]) -> dict[str, date]:
    """The latest local day each tier was read on.

    Read by both of the module's recency rules, which are not the same
    rule: rule 1's **admission gate** (``RECENCY_TOLERANCE_DAYS``) and
    rule 3's **tie-break** among the candidates that survive it."""
    last: dict[str, date] = {}
    for r in readings:
        if r.tier not in last or r.date > last[r.tier]:
            last[r.tier] = r.date
    return last


def sustained_tier(counts: Mapping[str, int]) -> str | None:
    """The highest-fidelity tier with at least ``MIN_BASELINE_READINGS`` in
    ``counts``, or ``None`` when no tier sustains a baseline there.

    Rule 1 of the tier rule on its own, and only its *count* half.
    ``resolve_baseline_tier`` narrows this **twice** since T117: first to the
    tiers read recently enough relative to the other candidates
    (``RECENCY_TOLERANCE_DAYS``, rule 1's admission gate), then to those that
    also cover the judged week. So this function is not the candidate set --
    it is the candidate set before either narrowing, which is why
    ``resolve_baseline_tier`` does not call it. ``tier_change_reset``
    reads it unnarrowed on the previous window ``[D-126, D-67]`` only
    (rule 4(b)) -- the current window is tested by the resolved tier's own
    count there (rule 4(a)) -- because a change of baseline is a change
    from the tier that *sustained* the previous window, not from the tier
    a week chose (sprint-005 review cycle 3, M2: an earlier wording said
    "on both windows", the mechanism T094 retracted).
    """
    for tier in TIER_FIDELITY:
        if counts.get(tier, 0) >= MIN_BASELINE_READINGS:
            return tier
    return None


def _densest_tier(counts: Mapping[str, int]) -> str | None:
    """The tier with the most readings, ties to the higher fidelity; ``None``
    when there are none."""
    present = [tier for tier in TIER_FIDELITY if counts.get(tier, 0) > 0]
    if not present:
        return None
    return max(present, key=lambda tier: (counts[tier], -_FIDELITY_RANK[tier]))


def resolve_baseline_tier(
    baseline_counts: Mapping[str, int],
    week_counts: Mapping[str, int],
    last_read: Mapping[str, date] | None = None,
) -> str | None:
    """The baseline tier from per-tier reading counts over the baseline
    window and over the judged week, and the day each tier was last read
    in the baseline window (T093, decision log 2026-09-10; rule 3 amended
    by T094, decision log 2026-09-11).

    1. The *candidates* are the tiers with at least ``MIN_BASELINE_READINGS``
       in ``baseline_counts`` **and read recently enough**: a tier whose
       latest day in ``last_read`` falls more than ``RECENCY_TOLERANCE_DAYS``
       behind the latest day of any candidate is struck (T117, 2026-09-15,
       closing IDEA-064). The comparison is between candidates, so a lone
       candidate is its own reference and is never struck, and the
       response's ``thresholds`` block gains no key.
    2. If any candidate holds at least ``MIN_WINDOW_READINGS`` in
       ``week_counts``, the baseline tier is the highest-fidelity such
       candidate.
    3. Otherwise -- no candidate covers the week: the athlete is ill, on
       holiday, or the only week-covering tier is thin -- **the candidate
       the athlete used last**: the one whose latest reading in the
       baseline window (``last_read``) is most recent, ties by count in the
       window, then by fidelity. When there is no candidate at all, the
       tier with the most days in ``baseline_counts``, ties to the higher
       fidelity (``_densest_tier``). Either way a week with no readings of
       any tier reads ``hrv_unavailable`` and **begins no reset** -- rule
       4 reads nothing inside the judged week that an empty week could
       change -- while a reset already in force persists through it
       unchanged (T095, review cycle 3 G11: "keeps the tier stable with no
       reset" was true only of beginning one). ``None`` when the baseline
       window holds no readings at all.

    Why the week is consulted: 14 readings in 60 days is 1.6 a week, and a
    judged week needs 3, so a tier can sustain a baseline by count while
    never sustaining a week -- a strap trial abandoned two months ago, or a
    strap worn two days a week, would otherwise own the baseline and blank
    every verdict on it as ``hrv_unavailable`` while the daily snapshot's
    readings sit off-tier. Why recency and not density among candidates
    (T094): "densest" handed a switched athlete's first thin week back to
    the device they abandoned three weeks earlier (and withdrew the reset
    with it), and asserted a reset on an empty week that both neighbouring
    weeks withdrew; the candidate read last is the tier the athlete is
    actually on, which is what "keeps the tier stable" always meant. "Most
    readings", not "highest present", when nothing is a candidate: an
    athlete with 45 Health Snapshot readings who borrows a chest strap once
    keeps the established snapshot baseline, and the strap capture is
    corroboration (§3.7.3). The accepted costs, named in F005's Negative
    Class: a strap worn two or three days a week takes and loses the
    baseline whenever its count in the sliding judged week crosses 3 --
    the oscillation, which the recency condition deliberately does **not**
    touch, because both tiers are in current use. "Stale candidacy" -- an
    abandoned trial still in the window plus three strap days this week,
    judged on the trial's band -- was the second such cost and is **closed**
    by T117 rather than accepted; it is priced in F005's Negative Class as
    resolved by change. Every count is in distinct local days
    (T095; IDEA-047 closed) -- ``_tier_counts`` is the one place the unit
    is taken.

    **Two recency notions now live in this function, and they are not the
    same rule.** Rule 3's recency is a **tie-break among admitted
    candidates**: it chooses between tiers that are all still eligible, so
    removing a tier from its consideration only changes which of several
    valid baselines is taken. Rule 1's recency is an **admission gate**: it
    removes a tier from the set entirely, and the baseline then goes
    somewhere else. The distinction is not decorative -- it is why the
    parameter-free form of this condition could not be built. A strict
    comparison (``tier.last_read > other.last_read``) was implemented and
    measured on 2026-09-15 and turned **5 pinned tests red in both error
    directions**, because a daily lower-fidelity tier is *always* read at
    least as recently as a 2-3-day-a-week higher-fidelity one: on the
    oscillation series (a documented, ``@must``-required accepted cost) the
    strap's latest is 2026-08-28 against the snapshot's 2026-09-01, a gap
    of **4 days**; on the stale July trial the gap is **45**. Any strict
    day-comparison rejects both alike, voiding rule 2's fidelity precedence
    instead of qualifying it, and it strips a legitimately resuming
    snapshot on ``D+3`` as well. The two populations differ only in *how*
    stale, which is a magnitude, and a magnitude is a constant -- hence
    ``RECENCY_TOLERANCE_DAYS``. Do not re-propose the parameter-free form.

    ``last_read`` may be omitted only where there is no baseline window to
    be recent in -- ``build_series``'s empty-baseline fallback, which
    applies the rule to the judged week standing in for both; candidates
    there tie on recency and fall to count, then fidelity, and all default
    to ``date.min``, so rule 1's gate sees every gap as zero and strikes
    nothing.
    """
    candidates = [tier for tier in TIER_FIDELITY if baseline_counts.get(tier, 0) >= MIN_BASELINE_READINGS]
    last_read = last_read or {}
    if candidates:
        # Rule 1's recency condition (T117): relative to the candidates
        # themselves, never to ``D-7``. ``latest`` is the most recent day
        # any candidate was read in the baseline window, so a lone
        # candidate is always its own ``latest`` and is never struck.
        latest = max(last_read.get(tier, date.min) for tier in candidates)
        candidates = [
            tier
            for tier in candidates
            if (latest - last_read.get(tier, date.min)).days <= RECENCY_TOLERANCE_DAYS
        ]
    for tier in candidates:
        if week_counts.get(tier, 0) >= MIN_WINDOW_READINGS:
            return tier
    if not candidates:
        return _densest_tier(baseline_counts)
    return max(
        candidates,
        key=lambda tier: (last_read.get(tier, date.min), baseline_counts[tier], -_FIDELITY_RANK[tier]),
    )


def build_series(
    rows: Iterable[Mapping[str, Any]],
    zone: ZoneInfo,
    target_date: date,
    earliest_start_time: str | None = None,
) -> HrvSeries:
    """Exclude, resolve the tier, filter, collapse -- in that order.

    ``rows`` are ``sessions`` rows (or dicts) carrying ``session_id``,
    ``start_time``, ``resting_rmssd_ms`` and ``hrv_source_tier``; any other
    key is ignored. Rows may span more than ``[D-66, D]`` -- the route reads
    a padded UTC range once and T091 calls this per local day -- and the ones
    outside are excluded as ``outside_windows`` rather than pre-filtered, so
    the response can still list them.

    ``earliest_start_time`` is the stored ``start_time`` of the **earliest
    reading the store holds** (``db.earliest_hrv_reading``), or ``None``
    when the caller knows of none (T096, review cycle 3 G13). The route
    reads rows back to ``D-126`` only and does not widen that read; this
    one instant is what lets ``coverage_gap_reset`` tell a layoff longer
    than the read -- a reading precedes the window, none was among the
    rows -- from a new athlete, for whom no reading precedes the window at
    all. It is bucketed into ``zone`` like every row, and a naive value is
    refused for the same reason a naive row is. Omitting it reads as "no
    earlier reading is known", which is correct for a caller whose rows
    are the whole history and wrong for one whose rows are a window of it.

    **Tier fallback when the baseline window is empty.** The tier rule is
    stated over the baseline window (and the judged week it must cover);
    when the baseline window holds no reading of any tier (a new athlete's
    first week, a request before any history) the same rule is applied to
    the judged window standing in for both, so a day with a reading still
    carries it in the series. No band can be built from an
    empty baseline, so the verdict for such a day is T084's ``unavailable``
    whichever tier is chosen; the fallback only decides which reading the
    contract's ``points[].ln_rmssd`` shows.

    **Resets (T092)** are detected between the exclusion step and the tier
    step, because a coverage gap clips the baseline window *before* the
    tier is resolved on it -- the fresh baseline is begun from the
    resumption on whatever tier sustains it there. The sustained-tier-change
    rule runs after the series is built: it finds the era boundary between
    the tier that sustained the previous window ``[D-126, D-67]``
    (``sustained_tier``, rule 1 alone) and the resolved tier, when the
    latter sustains the current window and the readings on the wrong side
    of that boundary are never dense enough to be a baseline of their own
    (``tier_change_reset`` / ``_era_boundary``, T094; judged over both
    windows since sprint-005 review cycle 3, with T095's density
    tolerance).

    **The clip and the report are two consequences of that boundary, not
    one** (D4a, decision log 2026-09-13; T098). The baseline window is
    clipped to ``[max(D-66, <the era's first day>), D-7]``, and the
    readings the clip removes leave the series for ``excluded`` as
    ``before_reset: tier_change``, **whenever a boundary exists**;
    ``reset_on`` / ``reset_reason`` are reported only when rule 4(c)'s week
    half also holds (``_isolated``: fewer than 3 stray days inside
    ``[D-6, D]``). Coupled, as they were until T098, the week half -- judged
    on the **sliding** judged week -- reached the band, and one capture of
    the other tier, contributing nothing to the week mean, un-clipped a
    finished device era back into it (review cycle 4, G-C4-1).

    **A coverage gap cancels neither** (T107, review cycle 6, G-C6-5).
    The gap's precedence is over the *report* alone: rule 4 is asked on
    every request, and the two clips compose as the later of their first
    days -- ``[max(D-66, <the resumption>, <the era's first day>), D-7]``
    -- because neither pre-gap nor pre-boundary readings may be in the
    band (``research/00`` §5.4). Until T107 this branch sat behind ``if
    reset_on is None``, so any gap in ``[D-66, D]`` meant no boundary was
    computed and nothing was clipped at all. ``rows``
    must span ``[D-126, D]`` for the rule to be able to
    fire, since a narrower read leaves the previous window empty, which
    reads as "thin" and never as a change. See ``coverage_gap_reset`` and
    ``tier_change_reset``.
    """
    baseline = baseline_window(target_date)
    judged = judged_window(target_date)
    baseline_first = baseline[0]
    previous = previous_window(target_date)

    readings: list[Reading] = []
    # Every reading before the baseline window: the previous window's, which
    # the tier-change rule reads, and any older one the caller's rows reach,
    # which only the gap rule reads (as the latest reading before the window).
    earlier_readings: list[Reading] = []
    excluded: list[Exclusion] = []
    for row in rows:
        session_id = row["session_id"]
        day, instant = local_day(session_id, row["start_time"], zone)
        tier = row["hrv_source_tier"]
        value = row["resting_rmssd_ms"]

        reason = _exclusion_reason(row)

        if not baseline_first <= day <= target_date:
            excluded.append(Exclusion(day, session_id, REASON_OUTSIDE_WINDOWS))
            if day < baseline_first and reason is None:
                earlier_readings.append(Reading(day, session_id, tier, float(value), instant))
        elif reason is not None:
            excluded.append(Exclusion(day, session_id, reason))
        else:
            readings.append(Reading(day, session_id, tier, float(value), instant))

    # Deterministic before anything is chosen by position: by instant, then
    # by id for two devices sharing an instant.
    readings.sort(key=lambda r: (r.start_time, r.session_id))
    previous_readings = _within(earlier_readings, previous)
    earliest_day = None
    if earliest_start_time is not None:
        earliest_day, _ = local_day("<earliest_start_time>", earliest_start_time, zone)

    gap_reset_on = coverage_gap_reset(readings, earlier_readings, earliest_day)
    reset_on = gap_reset_on
    reset_reason = REASON_COVERAGE_GAP if gap_reset_on is not None else None
    if gap_reset_on is not None:
        baseline = (gap_reset_on, baseline[1])
        readings, excluded = _exclude_before_reset(
            readings, excluded, gap_reset_on, REASON_COVERAGE_GAP
        )

    # The population both the tier rule and the tier-change rule read: every
    # tier, inside the (possibly clipped) baseline window -- and, for the
    # tier rule's week-coverage test, every tier inside the judged week.
    baseline_readings = _within(readings, baseline)
    week_readings = _within(readings, judged)
    week_counts = _tier_counts(week_readings)
    tier = resolve_baseline_tier(_tier_counts(baseline_readings), week_counts, _last_read(baseline_readings))
    if tier is None:
        tier = resolve_baseline_tier(week_counts, week_counts)

    series_by_day: dict[date, Reading] = {}
    for reading in readings:
        if reading.tier != tier:
            excluded.append(
                Exclusion(reading.date, reading.session_id, f"{REASON_OFF_BASELINE_TIER}: {reading.tier}")
            )
        elif reading.date in series_by_day:
            excluded.append(Exclusion(reading.date, reading.session_id, REASON_SAME_DAY_LATER_CAPTURE))
        else:
            series_by_day[reading.date] = reading

    series = tuple(series_by_day[day] for day in sorted(series_by_day))

    boundary = tier_change_reset(previous_readings, tier, baseline_readings, week_readings, judged)
    if boundary is not None:
        # D4a (T098), made true of a gapped series by T107 (review cycle 6,
        # G-C6-5): the clip is unconditional -- on the report *and* on the
        # coverage gap. Until T107 this branch sat behind ``if reset_on is
        # None``, so any gap in ``[D-66, D]`` meant rule 4 was never asked
        # and **nothing was clipped**, which drew an abandoned device era
        # back into the band and read a suppressed week as normal, with no
        # threshold to cross. ``research/00`` §5.4 says the now-sustaining
        # tier's pre-boundary readings are *never* in the band; only the
        # *report* was ever the gap's to win.
        #
        # The two clips compose as the **later** first day. Each says the
        # same kind of thing -- these readings are not of this baseline's
        # era -- so the band must contain neither the pre-gap nor the
        # pre-boundary readings, and the admissible set is the
        # intersection. ``baseline[0]`` already carries
        # ``max(D-66, gap_reset_on)``, so this one ``max`` composes all
        # three. The era may have begun before D-66 (T094: ``first_day`` is
        # its true first day, not the first inside the window); the window
        # reported is the schema's
        # ``[max(D-66, gap_reset_on, boundary.first_day), D-7]``, whether or
        # not ``reset_on`` is reported.
        #
        # Nothing is listed twice (``research/00`` §1.6). The gap branch
        # rebinds ``readings`` before the collapse, so ``series`` holds only
        # what it kept; this branch excludes out of ``series``. The two
        # populations are therefore disjoint by construction, and a boundary
        # earlier than the resumption removes nothing here rather than
        # re-excluding what the gap already took.
        baseline = (max(boundary.first_day, baseline[0]), baseline[1])
        kept, excluded = _exclude_before_reset(
            list(series), excluded, boundary.first_day, REASON_TIER_CHANGE
        )
        series = tuple(kept)
        # The gap keeps the *report* -- rule 4's precedence is unchanged, and
        # only it was ever precedence over. A gapped series can therefore
        # report ``coverage_gap`` on a window clipped later than the
        # resumption, which is the one claim T107 had to qualify: a
        # ``coverage_gap``'s ``reset_on`` no longer never precedes
        # ``baseline_window[0]``.
        if boundary.reported and gap_reset_on is None:
            reset_on = boundary.first_day
            reset_reason = REASON_TIER_CHANGE

    # Once, after both reset branches have had their say: each moves
    # readings into ``excluded`` out of order (``_exclude_before_reset``
    # appends what it drops), and nothing between here and there reads the
    # order.
    excluded.sort(key=lambda e: (e.date, e.session_id))

    return HrvSeries(
        target_date=target_date,
        timezone=zone.key,
        baseline_window=baseline,
        judged_window=judged,
        tier=tier,
        readings=tuple(readings),
        series=series,
        baseline=_within(series, baseline),
        window=_within(series, judged),
        excluded=tuple(excluded),
        reset_on=reset_on,
        reset_reason=reset_reason,
    )


# ---------------------------------------------------------------------------
# The SWC band, the thin-data guards and the verdict (T084)
#
# Everything in this section reads an ``HrvSeries`` and nothing else, so it
# composes with T092's reset clipping of ``baseline`` without knowing about it.
# ---------------------------------------------------------------------------

#: The register's shipped smallest-worthwhile-change width: the band is the
#: baseline mean +/- ``SWC_FACTOR * SD(ln rMSSD)`` (``research/00`` register
#: row, as clarified by F005's amendment).
SWC_FACTOR = 0.5
#: The smallest half-width the band may have. A metronomic athlete would
#: otherwise get a razor-thin band and be punished for consistency. **0.01,
#: not 0.05**: an ordinary athlete's ``0.5 * SD(ln)`` is about 0.05, so a
#: floor there would *be* the band for everyone and the computed half-width
#: would never be exercised -- a discriminator with no reachable negative
#: case. At 0.01 the floor fires only for a genuinely degenerate series
#: (daily readings within about +/-2%), and both branches are testable.
BAND_FLOOR = 0.01
#: §3.7.4's trends-not-single-readings rule, made testable: fewer readings
#: than this in the judged week is ``hrv_unavailable``, whatever they say.
MIN_WINDOW_READINGS = 3

VERDICT_NORMAL = "hrv_normal"
VERDICT_SUPPRESSED = "hrv_suppressed"
VERDICT_UNAVAILABLE = "hrv_unavailable"


@dataclass(frozen=True)
class Band:
    """The SWC band in log space: ``mean +/- half_width`` over the baseline.

    ``mean`` is the contract's ``points[].baseline``; ``lo``/``hi`` are its
    ``swc_low``/``swc_high``. ``floored`` says whether ``half_width`` is the
    computed ``SWC_FACTOR * SD`` or ``BAND_FLOOR`` -- the response carries it
    so a reader can see which branch fired.
    """

    mean: float
    half_width: float
    lo: float
    hi: float
    floored: bool


@dataclass(frozen=True)
class HrvVerdict:
    """The verdict for one target date, with everything that produced it.

    ``ln_rmssd_7d_mean`` is the mean of the judged window's readings, or
    ``None`` when the window is empty; it is reported whenever there is one
    so the verdict is reproducible by hand (``research/00`` §1.6), even when
    ``readings_in_window`` is below the minimum and the verdict is
    unavailable. ``below_by`` is ``band.lo - mean`` when suppressed, else
    ``None``. ``band`` is ``None`` when the baseline holds fewer than two
    readings. ``established`` is ``baseline_n >= MIN_BASELINE_READINGS``.
    """

    verdict: str
    ln_rmssd_7d_mean: float | None
    below_by: float | None
    band: Band | None
    baseline_n: int
    established: bool
    readings_in_window: int


def ln_rmssd(reading: Reading) -> float:
    """``ln`` of the reading, **unguarded on purpose**. ``build_series``
    guarantees every ``Reading`` carries a strictly positive finite value
    (``_is_usable_value``); a second, silent guard here would mask a T083
    regression, so a non-positive value reaching this function raises
    (``math.log`` -> ``ValueError``) and the defect surfaces."""
    return math.log(reading.rmssd_ms)


def build_band(ln_values: Iterable[float]) -> Band | None:
    """The SWC band over a baseline of ln-rMSSD values, or ``None`` when
    there are fewer than two of them.

    ``half_width = max(SWC_FACTOR * SD(ln), BAND_FLOOR)``, with **sample SD**
    (``statistics.stdev``, ``n - 1``; F005 decision log, 2026-09-09): the
    baseline is a sample of the athlete's dispersion, not the population of
    it. The dispersion is of the *log* series, not a coefficient of
    variation of it -- ``CV = SD / mean`` is a ratio to the origin, and a
    log scale's origin is arbitrary, so ``CV(ln)`` flips sign when the same
    readings are expressed in seconds instead of milliseconds and the band
    inverts; ``SD(ln)`` is unit-invariant and numerically indistinguishable
    from the literature's ``0.5 * CV`` of the raw series.

    ``stdev`` raises ``StatisticsError`` below two readings, which *is* the
    "no band under two readings" rule surfacing structurally; it is caught
    here, at the one place the band is built, and nothing else guards it.
    Never ``pstdev``: it returns ``0.0`` for a single reading and would
    manufacture a floored band from nothing.

    **The seed script (T086) must state its expected band with the same
    estimator** -- sample SD -- or the demo probe compares two different
    bands.
    """
    values = list(ln_values)
    try:
        dispersion = statistics.stdev(values)
    except statistics.StatisticsError:
        return None
    computed = SWC_FACTOR * dispersion
    floored = computed < BAND_FLOOR
    half_width = BAND_FLOOR if floored else computed
    mean = statistics.fmean(values)
    return Band(mean=mean, half_width=half_width, lo=mean - half_width, hi=mean + half_width, floored=floored)


def judge(series: HrvSeries) -> HrvVerdict:
    """The verdict for ``series.target_date`` from its two disjoint slices.

    The band is built over ``series.baseline`` (``[D-66, D-7]``) and the
    week's mean over ``series.window`` (``[D-6, D]``); because the slices do
    not overlap, a suppressed week cannot lower its own band and self-clear
    (§3.7.4: a single good morning does not clear an accumulated
    suppression). In order:

    - no band (fewer than two baseline readings) -> ``hrv_unavailable``;
    - fewer than ``MIN_WINDOW_READINGS`` in the week -> ``hrv_unavailable``
      (two bad mornings are not a trend, however bad);
    - on a baseline that is *not* established (``< MIN_BASELINE_READINGS``)
      -> ``hrv_unavailable``, wherever the mean sits;
    - otherwise, on an **established** baseline: the mean strictly below
      ``band.lo`` -> ``hrv_suppressed``, with ``below_by``; the mean inside
      **or above** the band -> ``hrv_normal``.

    **The establishment gate is symmetric, and that is a 2026-09-15 change**
    (T116, [[IDEA-062]]). §3.7.3 says the suppression is *withheld* until the
    baseline is adequately established; it does not say the week reads
    normal, and until T116 it did. Either verdict on a 2-to-13-reading
    baseline tells Section 6 something on evidence the same response reports
    unestablished, and the ``hrv_normal`` direction is the forbidden one:
    it says readiness is intact, so a planned hard session stands on weak
    evidence -- up-regulating on weak evidence, which ``research/00`` §1.7
    forbids. So the honest verdict is that there is none, and the response
    carries ``baseline_n`` and ``established`` to say why. This is reachable
    after **every** reset this feature performs: a coverage gap or a tier
    change collapses the baseline deliberately, and the athlete then
    traverses ~12 unestablished days, previously all of them reading
    ``hrv_normal`` unless the week fell below the band.

    Pinned by ``test_a_thin_baseline_inside_the_band_is_unavailable_not_normal``,
    ``test_a_thin_baseline_above_the_band_is_unavailable_too`` and
    ``test_the_establishment_gate_flips_normal_at_exactly_fourteen_readings``
    in ``test_hrv_trend_band.py``, and by the contract table there.

    The band itself is asserted whenever it can be built, established or
    not, and whether or not the week has readings: it is a property of the
    baseline, and the contract's ``points[]`` draws it on days with no
    reading (T091).
    """
    band = build_band(ln_rmssd(reading) for reading in series.baseline)
    baseline_n = len(series.baseline)
    established = baseline_n >= MIN_BASELINE_READINGS
    readings_in_window = len(series.window)
    window_mean = statistics.fmean(ln_rmssd(r) for r in series.window) if readings_in_window else None

    verdict = VERDICT_UNAVAILABLE
    below_by = None
    if band is not None and window_mean is not None and readings_in_window >= MIN_WINDOW_READINGS:
        if established:
            if window_mean < band.lo:
                verdict = VERDICT_SUPPRESSED
                below_by = band.lo - window_mean
            else:
                verdict = VERDICT_NORMAL

    return HrvVerdict(
        verdict=verdict,
        ln_rmssd_7d_mean=window_mean,
        below_by=below_by,
        band=band,
        baseline_n=baseline_n,
        established=established,
        readings_in_window=readings_in_window,
    )


# ---------------------------------------------------------------------------
# Baseline re-establishment: the coverage gap and the sustained tier change
# (T092)
#
# ``build_series`` calls into this section at two points: the gap rule before
# the tier is resolved (it clips the baseline window the tier is resolved on),
# the tier-change rule after the series is built. Both read readings, never
# stored rows, so a gap spanned only by excluded rows still counts as a gap.
# ---------------------------------------------------------------------------


def coverage_gap_reset(
    readings: Iterable[Reading],
    earlier_readings: Iterable[Reading],
    earliest_day: date | None = None,
) -> date | None:
    """The local day the baseline is re-established on after a coverage gap,
    or ``None``.

    Scans the distinct local days of ``readings`` (the post-exclusion series
    of **every** tier, inside ``[D-66, D]``) backwards from the latest and
    returns the first day that follows a silence of more than
    ``GAP_RESET_DAYS`` local days -- the resumption. Measured on ``date``
    arithmetic, never on UTC deltas: a silence straddling a DST change is
    still the same number of local days.

    A gap is bounded by a reading on both sides. Three consequences the spec
    text does not state and this function decides:

    * **An open gap** (the last reading is more than 21 days old and nothing
      has resumed) is not yet a reset: the reset lands on the first reading
      *after* a gap, and there is none. The judged week is empty, so the
      verdict is ``unavailable`` regardless.
    * **The leading stretch** of ``[D-66, D]`` before the first reading is a
      gap when the silence from the **latest reading known to precede the
      window** exceeds 21 days. That reading is the latest of
      ``earlier_readings`` -- the post-exclusion readings of every tier
      before ``D-66`` among the rows: the previous window's and any older
      -- or, when no reading before the window was among the rows at all
      but the store's earliest reading (``earliest_day``, T096) precedes
      it, that instant: the latest pre-window reading then lies before
      every row read, so the silence is at least the whole read-back.
      Measuring from the earliest reading is safe in both directions: it
      says "no gap" only when every earlier reading is within 21 days,
      which is right, and "gap" only when the true silence is at least the
      read-back, which is right for a caller whose rows span ``[D-126, D]``
      (``build_series``'s stated contract). With **no known earlier
      reading** it is the start of history, not a break between two eras:
      nothing precedes it that could contribute across it, and reporting a
      new athlete's first capture as a ``coverage_gap`` would name an event
      that did not happen. Until T096 this branch read the previous window
      alone, so "no earlier reading was *read*" was mistaken for "no
      earlier reading *exists*" and a layoff longer than about two months
      could not report the gap (review cycle 3, G13).
    * **The report has a lifetime** (review cycle 3, G14): a ``coverage_gap``
      is reported only while the resumption lies inside ``[D-66, D]`` --
      for 67 days from the resumption -- because the scan runs over that
      window and the leading-stretch test measures to its first reading.
      Once the resumption has aged past ``D-66`` the same era carries no gap
      reset, and if the resumption was also a device switch the era is
      reported as ``tier_change`` on the same ``reset_on`` from that day
      until rule 4(b) fails (``tier_change_reset``), then as nothing -- one
      era, one ``reset_on``, three reports and no event between them, the
      accepted cost named in F005's Negative Class. A ``coverage_gap``'s
      ``reset_on`` precedes ``baseline_window[0]``
      only when an era boundary clipped the window *later* than the
      resumption -- never because the gap report has outlived its own
      clip, as a ``tier_change``'s ``reset_on`` can (the era's true first
      day against a window clipped at ``D-66``). Before T107 (review cycle
      6, G-C6-5) it could not precede it at all, because a gap cancelled
      the era clip outright; the composed clip is
      ``max(D-66, resumption, era first day)`` and the gap keeps only the
      *report*.
    """
    days = sorted(_days(readings))
    if not days:
        return None
    for i in range(len(days) - 1, 0, -1):
        if _silence_between(days[i - 1], days[i]) > GAP_RESET_DAYS:
            return days[i]
    known_before = {r.date for r in earlier_readings}
    if earliest_day is not None and earliest_day < days[0]:
        known_before.add(earliest_day)
    last_before = max(known_before, default=None)
    if last_before is not None and _silence_between(last_before, days[0]) > GAP_RESET_DAYS:
        return days[0]
    return None


def _silence_between(earlier: date, later: date) -> int:
    """The number of whole local days strictly between two reading days."""
    return (later - earlier).days - 1


def _exclude_before_reset(
    readings: list[Reading], excluded: list[Exclusion], reset_on: date, reason: str
) -> tuple[list[Reading], list[Exclusion]]:
    """Move every reading dated before ``reset_on`` out of the series into
    the exclusions, named ``before_reset: <reason>``, so what is kept and
    what is excluded stay disjoint and exhaustive over the rows in
    ``[D-66, D]``.

    Both resets call it (T098), and since T107 both can call it on one
    request: the gap rule on the pre-filter readings of every tier, before
    the baseline tier is resolved on the clipped window, and the
    era-boundary clip on the collapsed one-per-day series, after it --
    off-tier rows are already listed ``off_baseline_tier`` there and must
    not be listed twice. The two populations cannot overlap either,
    because the gap branch **rebinds** ``readings`` and the series is built
    from what it kept, so a reading the gap excluded is not there for the
    era clip to exclude again (``research/00`` §1.6's "exactly one list"). Until T098 the tier-change branch narrowed
    ``baseline`` without moving anything, so the readings it dropped were
    in neither list and the contract's ``before_reset: tier_change`` was
    unreachable (review cycle 4, G-C4-3)."""
    kept = [r for r in readings if r.date >= reset_on]
    dropped = [
        Exclusion(r.date, r.session_id, f"{REASON_BEFORE_RESET}: {reason}")
        for r in readings
        if r.date < reset_on
    ]
    return kept, excluded + dropped


def _isolated(readings: Iterable[Reading], judged: tuple[date, date]) -> bool:
    """Rule 4(c)'s **density tolerance** (T095; decision log 2026-09-12):
    a set of readings is *isolated* -- corroboration, not an era -- when
    it would neither be a candidate nor cover the judged week: fewer than
    ``MIN_BASELINE_READINGS`` distinct local days in all, **and** fewer
    than ``MIN_WINDOW_READINGS`` distinct local days inside ``judged``
    (``[D-6, D]``). Either density means the tier was in use, which is
    what "the era continued" means -- the same two thresholds under which
    rule 1 makes a tier a candidate and rule 2 lets it take the week.

    **Two halves, two consequences, two statements** (T098's D4a;
    corrected here by T104, review cycle 5 G-C5-6 -- this docstring used
    to claim the predicate was stated in this one place, which T098's
    split had already made false). The **candidacy half** alone -- fewer than
    ``MIN_BASELINE_READINGS`` stray days -- decides whether an era
    boundary *exists* at all, and hence whether ``build_series`` clips the
    baseline. It is stated in ``_era_boundary``, at the gate that admits a
    boundary, because the same count also orders the candidates there. The
    **week half** decides whether an admitted boundary is *reported*,
    **and -- as the first ordering term in ``_era_boundary``'s selection
    key -- which of several admitted boundaries is taken, hence where the
    clip lands**. (Corrected again by T104, iteration 2: this paragraph
    said the week half decided the report *only*, which contradicted
    ``_era_boundary``'s own docstring in this same file and is denied by
    ``test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of``,
    where the week-clear boundary wins over one with fewer stray days and
    ``first_day`` moves with it.) The conjunction returned here is
    therefore asked of a boundary whose candidacy half already holds: its
    answer orders the candidates, and the winner's becomes
    ``EraBoundary.reported``. Both sites count with ``_days`` and compare
    against ``MIN_BASELINE_READINGS``, so no threshold has drifted -- but
    the comparison is written twice: change one and read the other.

    The week half is judged on the sliding week, as rule 2 is, so it
    carries rule 2's own edge: three old-tier captures in one week after a
    switch are use on the days they sit in ``[D-6, D]`` and corroboration
    once the week has slid past them (F005 Negative Class, the
    tolerance's cost).
    """
    readings = tuple(readings)
    return (
        len(_days(readings)) < MIN_BASELINE_READINGS
        and len(_days(_within(readings, judged))) < MIN_WINDOW_READINGS
    )


@dataclass(frozen=True)
class EraBoundary:
    """Where the resolved tier's era begins, and whether rule 4 reports it.

    D4a (decision log 2026-09-13; T098): **clip always, report
    conditionally**. ``first_day`` is the era's true first day -- the day
    ``baseline`` is clipped at, whether or not anything is reported --
    and ``reported`` says whether rule 4(c)'s week half also holds, which
    is the only thing that decides what the athlete is *told*. The two
    were one branch until T098, so the tolerance's week half, judged on
    the **sliding** judged week, reached the band: a capture on a tier
    that is not the baseline tier, contributing nothing to the week mean,
    withdrew the reset, un-clipped ``baseline`` back to ``[D-66, D-7]``
    and dragged a seven-week-old device era back into it -- ``hrv_normal``
    on a week that reads suppressed against the era-correct baseline,
    flipping with no new data (review cycle 4, G-C4-1).
    """

    #: ``B_start``'s local day: the resolved tier's first reading after the
    #: old era's last. ``build_series`` clips ``baseline`` to
    #: ``[max(D-66, first_day), D-7]`` on it unconditionally -- on the
    #: report (T098) and, since T107, on a coverage gap too, composing with
    #: the gap's own clip as the later of the two first days.
    first_day: date
    #: Rule 4(c)'s week half on this boundary's strays: ``reset_on`` /
    #: ``reset_reason`` are reported only when it holds.
    #:
    #: **It is assigned the full conjunction**, ``_isolated``, not the week
    #: half on its own. The two coincide only because ``_era_boundary``'s
    #: own gate has already required the candidacy half of the very same
    #: strays, so the conjunction can only turn on the week half there.
    #: Loosen or widen that gate and this field silently stops meaning
    #: what it is documented to mean (T104, review cycle 5 G-C5-6).
    reported: bool


def _era_boundary(
    old: Iterable[Reading], new: Iterable[Reading], judged: tuple[date, date]
) -> EraBoundary | None:
    """Clause (c) with the tolerance: where ``new``'s era begins, and
    whether the boundary is one rule 4 reports -- or ``None`` when the two
    eras interleave and no era of ``new`` began at all.

    A *boundary* is an old-tier reading ``A_end`` and the first new-tier
    reading after it, ``B_start``, with no old-tier reading in between. The
    readings on the wrong side of it -- every old-tier reading after
    ``B_start``, and every new-tier reading from the old era's **first
    local day** up to ``A_end`` -- are the *strays*. **One predicate with
    two halves decides two consequences** (D4a, decision log 2026-09-13),
    and since T098 the two halves are stated in two places: the candidacy
    half at the gate below, the conjunction in ``_isolated`` (T104, review
    cycle 5 G-C5-6):

    * a boundary is an **era boundary** when its strays, together, are
      fewer than ``MIN_BASELINE_READINGS`` distinct local days -- never
      dense enough to be a baseline of their own, so the old era ended
      before the new one began. Of several, the one whose strays are also
      absent from the judged week (``_isolated``, the tolerance's week
      half) first, then the one with the fewest stray days -- the switch
      that explains the most readings -- ties to the later one, the
      younger baseline being the cautious reading (``research/00`` §1.7);
    * that boundary's ``B_start`` day is where the resolved tier's era
      begins, **always**: ``build_series`` clips ``baseline`` there
      whether or not anything is reported, because the era boundary is a
      property of the athlete's capture history;
    * it is **reported** as a ``tier_change`` only when its strays are
      ``_isolated`` -- also fewer than ``MIN_WINDOW_READINGS`` distinct
      days inside ``[D-6, D]``. That half says whether the other device
      was in use *this week*, which is a statement about what the athlete
      is told, not about where the era began, and it is judged on the
      sliding week (rule 2's own edge).

    Ordering the candidates by the week half first is what keeps every
    reported reset exactly where it was before T098: an isolated boundary
    is always a candidate (``_isolated`` implies fewer than 14 stray days),
    so whenever one exists it is still chosen, by fewest stray days and
    the same tie-break, and ``reset_on`` does not move.

    Strays are counted from the old era's first *day*, not its first
    instant: the day is the unit every count is taken in (T095), and a
    new-tier capture earlier that same morning is a day the new tier was
    in use inside the old era -- read by the instant, a daily device's
    reading on the morning a trial began fell outside the span, and on the
    one day the trial held exactly 14 in the previous window the 13 inside
    were "isolated" and a phantom reset was asserted (M1's series,
    2026-08-12). New-tier readings before that day belong to the era the
    old tier replaced and are neither strays nor a start (``first_day`` is
    the first reading *after* the old era). Two readings at the very same
    instant are simultaneous, not strays: a new-tier capture at the
    instant of ``A_end`` means the old tier did not predate the new one
    (the switch-day tie, interleaved, unchanged from review cycle 3), and
    an old-tier capture at the instant of ``B_start`` is judged at its own
    turn as ``A_end``, where the same tie refuses it.
    """
    old = sorted(old, key=lambda r: r.start_time)
    new = sorted(new, key=lambda r: r.start_time)
    new_instants = [r.start_time for r in new]
    from_old_first_day = bisect_left([r.date for r in new], old[0].date)
    boundaries: list[tuple[bool, int, datetime, date]] = []
    for i, a_end in enumerate(old):
        j = bisect_right(new_instants, a_end.start_time)  # the first new-tier reading after A_end
        if j == len(new):
            continue
        if i + 1 < len(old) and old[i + 1].start_time <= new_instants[j]:
            continue  # the old tier read again before the new era's first: judged at that reading instead
        if j > from_old_first_day and new_instants[j - 1] == a_end.start_time:
            continue  # simultaneous: the old tier does not predate the new one
        strays = (*old[i + 1 :], *new[from_old_first_day:j])
        stray_days = len(_days(strays))
        # The candidacy half of rule 4(c), restated here because the count
        # also orders the candidates below; ``_isolated`` holds the
        # conjunction and answers only the reporting half once this holds.
        if stray_days < MIN_BASELINE_READINGS:
            boundaries.append((_isolated(strays, judged), stray_days, a_end.start_time, new[j].date))
    if not boundaries:
        return None
    reported, _, _, first_day = max(
        boundaries, key=lambda boundary: (boundary[0], -boundary[1], boundary[2])
    )
    return EraBoundary(first_day=first_day, reported=reported)


def tier_change_reset(
    previous_readings: Iterable[Reading],
    tier: str | None,
    baseline_readings: Iterable[Reading],
    week_readings: Iterable[Reading],
    judged: tuple[date, date],
) -> EraBoundary | None:
    """The era boundary between the previous window's tier and the resolved
    one -- the local day a fresh baseline begins on, and whether it is
    *reported* -- or ``None`` when there is none.

    **The clip and the report are two consequences of one boundary** (D4a,
    decision log 2026-09-13; T098). ``build_series`` clips ``baseline`` to
    ``[max(D-66, gap_reset_on, first_day), D-7]`` whenever this returns a
    boundary -- since T107 whether or not a coverage gap fired, the two
    clips composing as the later of their first days -- and reports
    ``reset_reason`` / ``reset_on`` only when the boundary is ``reported``
    *and* no gap outranked it. Clauses (a) and (b) below, and the existence
    of an era boundary, gate both; the two differ in exactly one thing, the week half
    of clause (c)'s tolerance (``_era_boundary``). Before T098 they were one
    branch, so a capture of the *other* tier in the judged week -- which
    contributes nothing to the week mean -- withdrew the reset and with it
    the clip, and a finished device era re-entered the band (G-C4-1).

    A boundary is found, and ``tier_change`` is *reported*, when and only
    when (rule 4, T094; decision log 2026-09-11, tolerance 2026-09-12):

    (a) the resolved baseline tier ``tier`` **sustains** the current
        baseline window -- read on at least ``MIN_BASELINE_READINGS``
        distinct local days in ``baseline_readings`` (all tiers, already
        clipped by any coverage gap), rule 1's candidacy;
    (b) it differs from the tier the previous window ``[D-126, D-67]``
        sustains (``sustained_tier`` on ``previous_readings``: highest
        fidelity with at least ``MIN_BASELINE_READINGS`` days, rule 1
        alone);
    (c) the two eras **do not interleave**, judged over both windows and
        the judged week together (``previous_readings``,
        ``baseline_readings`` and ``week_readings``, the last so that the
        tolerance's week half has readings to count; sprint-005 review
        cycle 3, M1 -- T094 judged it on the current window alone): with
        ``A`` the previous tier and ``B`` the resolved tier, there is an
        era boundary -- ``A``'s last era reading and ``B``'s first after it
        -- such that the readings on its wrong side, of either tier
        together, are **isolated** (``_isolated``: fewer than
        ``MIN_BASELINE_READINGS`` distinct days in all and fewer than
        ``MIN_WINDOW_READINGS`` inside the judged week), so the old
        era ended before the new one began and what lies across the
        boundary is corroboration, not use (``_era_boundary``). Its two
        halves are read apart (T098): the candidacy half -- fewer than
        ``MIN_BASELINE_READINGS`` stray days in all -- is what makes the
        boundary an era boundary and clips the baseline; the week half is
        what makes it ``reported``. Compared
        on the captures' instants, as the same-day collapse orders them,
        so two devices worn on the switch morning are ordered by which was
        worn first; a previous-tier capture at the very instant of the
        resolved tier's first is simultaneous, not a stray, so the tie is
        interleaved and reports nothing. Judged on the current window
        alone, (c) was vacuously true for a trial that had aged wholly
        into the previous window and a phantom ``tier_change`` was
        reported for an athlete who never switched (the review's series
        B); judged exactly, one capture of either tier on either side of
        a genuine switch made the eras interleave and silenced the reset
        for the whole era (review cycle 3, G9, G12, IDEA-065).

    The reset lands on the era's **true first day**: the resolved tier's
    first reading after the era boundary -- so it does not slide one day
    per day once the era start ages past ``D-66`` (T094, G8), and an older
    era of the same tier in the previous window (a 40-day strap trial
    between two snapshot eras) is not mistaken for this one. It can
    therefore precede the clipped window's first day; ``build_series``
    reports ``[max(D-66, gap_reset_on, reset_on), D-7]`` -- the gap term
    being why a reported ``coverage_gap``'s ``reset_on`` can precede
    ``window[0]`` too (T107), in the other direction. **The report is live
    only while (a), (b) and the week half all hold** -- any one of the
    three can lapse on its own and this returns nothing on that day
    (T109; T111 corrected this paragraph, which read the (b) route as the
    whole lifetime). The (b) route is the one with a closed form -- the
    construction reference's own words, restored here by T113 (gap
    G-C7-7), in place of the universal T111 had added to them about which
    of the three conditions lapses last. No such universal holds: which
    condition ends a given report is a property of the series, and the
    justification clause is dropped rather than re-stated (T114, gap
    G-C7-13 -- it had claimed (a) and the week half never lapse on a
    forward switch, two sentences before this paragraph says either of
    them may have ended the report earlier).
    ``sustained_tier`` is the
    highest-fidelity tier with ``MIN_BASELINE_READINGS`` days in
    ``[D-126, D-67]``, so the day (b) lapses is direction-dependent
    (review cycle 3, S1): for a forward switch (snapshot to strap) it is
    the day the strap reaches 14 there, ``S+80`` for a daily device; for
    the reverse one (strap to snapshot) the old strap keeps that window by
    fidelity until it drops below 14 there, ``T+114``, a month after the
    snapshot reached 14 (``T+81``). Those dates bound the report; they do
    not promise it, because (a) or the week half may have ended it
    earlier -- a resolved tier that never reaches or falls back below
    ``MIN_BASELINE_READINGS`` in the baseline window, or a few days of the
    other device inside ``[D-6, D]``. Either way the old tier holds at
    least 14 days in the previous window while (b) holds, so there is
    never a reset whose first day is unknown, and an era boundary always
    has a ``B`` reading after it by construction.

    What each clause refuses to call a change. (a): a thin new tier is not
    yet "dense enough to sustain a baseline" (F005), and a single off-tier
    capture is corroboration (§3.7.3); a tier the judged week chose while
    another sustains the window is not a change of baseline either. (b):
    a resolution that differs only because the previous window is thin
    (five snapshot readings there, a strap baseline now) is the athlete's
    first established baseline, not a change from anything; a tier that
    sustains both windows is unchanged, however the week was judged; and
    a stale trial of the *new* tier that still holds 14 days in the
    previous window sustains it by fidelity, so the switch's reset waits
    until that trial ages below 14 there (T095, G12: 16 days for a 14-day
    trial 90 days before the switch, rather than the 28 the exact clause
    (c) then added) -- a stale candidate, IDEA-064's shape, not an
    interleaving. (c), the clause T094 added: a 2/3-day strap habit begun
    nine weeks ago sustains the current window by count while the
    previous window is all snapshot -- the sustained tier changed -- but
    the snapshot readings run daily through the strap's, so no era ended
    and no reset is reported (before T094 it fired on every strap week
    and vanished on the next); a stale trial, in either window, is
    interleaved the same way. The reverse transition -- an owning strap
    abandoned for a daily snapshot at ``T`` -- has the strap's readings
    all before ``T+1``, so it resets on the day the snapshot first owns
    the baseline (``T+21``) with ``reset_on = T+1``, not a month later
    when the strap drops below 14. The tolerance's own error direction,
    named in F005's Negative Class: a habit of the other tier dense enough
    to be a candidate, or to cover a week, inside the era is use, so the
    eras interleave and no reset is reported -- G6's intended behaviour,
    and the accepted cost of tolerating the isolated capture.

    A coverage gap takes precedence **over the report, and over that
    alone** (T107, review cycle 6 G-C6-5). ``build_series`` asks this rule
    on every request, gap or no gap, and composes the two clips as the
    later of their first days; when a gap has fired, ``reset_reason`` stays
    ``coverage_gap`` and this boundary's ``reported`` is not consulted.
    Until T107 the rule was asked only ``if reset_on is None``, so any gap
    in ``[D-66, D]`` meant no boundary was computed and **nothing was
    clipped** -- an abandoned device era re-entered the band and a
    suppressed week read ``hrv_normal``, with no threshold to cross. Note
    that ``baseline_readings`` reaching this rule is already gap-clipped,
    so clause (a) is judged on the resumption era; the boundary itself is
    found over both windows and may precede the resumption, in which case
    the gap's clip is the later one and this one removes nothing.

    **Rule 1's recency condition does not reach either clause here, and
    that is a decision, not an omission** (T117, 2026-09-15, the question
    left open by IDEA-064). ``RECENCY_TOLERANCE_DAYS`` strikes a stale
    candidate from ``resolve_baseline_tier``'s candidate set; neither
    clause (a) nor clause (b) applies it, and both still read
    ``MIN_BASELINE_READINGS`` alone.

    *Clause (a)* takes the tier ``build_series`` already resolved, so the
    recency gate has been applied before this function is called -- a tier
    struck for staleness never arrives here as ``tier`` at all. Restating
    the gate in (a) would be a second copy of one rule.

    *Clause (b)* is the substantive half, and the answer is **no**. The two
    clauses ask different questions. Rule 1 asks which tier may **build
    today's band**, where staleness is a defect because the band would
    describe an athlete who no longer exists. Clause (b) asks only which
    tier **sustained the previous window** ``[D-126, D-67]`` -- a
    historical fact about a window that is by construction at least 67 days
    old, used to detect that something changed. No band is built from it
    and no verdict rests on it; "the tier the athlete used to be on" is
    precisely the thing a staleness gate would forget, and forgetting it
    withdraws the report of the change it exists to announce. Measured, not
    asserted: applying the same relative gate to ``sustained_tier`` here on
    2026-09-15 turned **two** pinned tests red across the reset and series
    suites --
    ``test_the_reverse_transition_resets_the_day_the_snapshot_first_owns_the_baseline``
    (the abandoned strap is stripped from ``[D-126, D-67]`` once the daily
    snapshot there outruns it by 28 days, so the reverse transition's
    report ends long before ``T+114``, the day the strap actually drops
    below 14) and
    ``test_one_resumption_era_is_reported_coverage_gap_then_tier_change_then_nothing``
    (the pre-layoff tier is stripped, and the middle of the three reports
    goes missing). Those two are the assertions that go red if this
    decision is ever reversed without being re-argued.

    What the recency condition therefore does **not** close: G12, the stale
    trial of the *new* tier that still holds 14 days in the previous window
    and delays a genuine switch's reset. That refusal is clause (b)'s, and
    it survives the gate for a reason worth recording -- when the gate was
    applied experimentally, ``test_an_older_trial_of_the_new_tier_does_not_
    delay_a_genuine_switchs_reset`` stayed **green**, because the trial's
    14 days are also strays under clause (c)'s candidacy half, so the
    boundary is refused there instead. G12's row in F005's Negative Class
    stands unchanged.

    **What that clip does to clause (c)** -- the question this docstring
    was silent on until T118 (review cycle 7, G-C7-3), having answered it
    for (a) alone. ``baseline_readings`` and ``week_readings`` are both
    derived from the gap-rebound series, so the readings of
    ``[D-66, gap_reset_on)`` are absent from ``_era_boundary``'s **stray
    count** too, not only from (a)'s candidacy count. New-tier readings
    hidden there would have been strays of every *late* ``A_end`` -- they
    lie between the old era's first day and that boundary's ``B_start`` --
    so hiding them shrinks the stray term for late boundaries and can
    admit, or promote over an earlier candidate, a boundary the full
    capture history refuses or dates earlier. That boundary's
    ``first_day`` can then fall **after** the resumption, and the
    composed clip removes post-resumption readings of the baseline tier
    from the band as ``before_reset: tier_change``. T107 created this
    reachability: before it the gap cancelled this branch outright.
    **Accepted and named, not fixed** (user decision 2026-09-15); the
    measured reachability, and why its error direction waits on T116's
    change to the thin-baseline verdict rule, are priced in F005's
    Negative Class. Pinned at both levels by
    ``test_the_gap_clip_moves_the_era_boundary_later_than_the_full_history_finds``
    (one history, the boundary dated 2026-07-03 on the full population and
    2026-08-14 on the gap-clipped one) and
    ``test_the_gap_created_boundary_clips_on_tier_days_at_the_resumption``.
    """
    if tier is None:
        return None
    baseline_readings = tuple(baseline_readings)
    previous_readings = tuple(previous_readings)
    if _tier_counts(baseline_readings).get(tier, 0) < MIN_BASELINE_READINGS:
        return None
    previous_tier = sustained_tier(_tier_counts(previous_readings))
    if previous_tier is None or previous_tier == tier:
        return None
    everything = (*previous_readings, *baseline_readings, *week_readings)
    return _era_boundary(_of_tier(everything, previous_tier), _of_tier(everything, tier), judged)
