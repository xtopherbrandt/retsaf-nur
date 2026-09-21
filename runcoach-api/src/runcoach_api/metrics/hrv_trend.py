"""The resting-HRV trend (F005, spec §3.7): the series, the band and verdict, and the resets.

This module turns stored ``sessions`` rows into **one clean one-reading-per-
local-day dataset per source tier present** (``build_series``, T083; N
datasets since F006/T151), judges one dataset's target date against the SWC
band built over its baseline (``judge``, T084), and clips or re-establishes
a baseline after a coverage gap or a sustained tier change
(``coverage_gap_reset`` / ``tier_change_reset``, T092). The three sections
follow in that order. The window constants and every exclusion and
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
2. **Clip the coverage gap**, globally: the silence of the series as a
   whole, every tier together (``coverage_gap_reset``, AC16), rebinds the
   post-exclusion readings and the first day of every dataset's window
   alike, before any tier is looked at.
3. **Partition** the readings by tier -- one ``HrvDataset`` per tier
   present (F006, ``research/00`` §5.4 amended 2026-09-18; T151). A morning
   carrying two tiers' captures feeds both datasets.
4. **Collapse** to one reading per local day **within each dataset**: the
   earliest capture of the day on that tier; every later one is
   ``same_day_later_capture``. Every count anywhere is in distinct local
   days, the unit ``judge`` reports as ``baseline_n`` and
   ``readings_in_window`` (T095; ``_tier_counts`` is the one place it is
   taken). Then each dataset's own era clip (``tier_change_reset``, asked
   with that tier), its band, ``n``, ``established`` and ``withheld``.

Doing (4) before (3) -- collapsing across tiers, then partitioning --
silently drops any day whose earliest capture is on another tier even when
a usable reading of this tier existed, and ``readings_in_window`` decides
``hrv_unavailable`` -- so the order is outcome-determining, not cosmetic.

**Which dataset is judged is decided by ``select_dataset``, after the
series is built** (F006, T155; ``research/00`` §5.4 amended 2026-09-18
(ii)). F005 resolved *the* baseline tier inside ``build_series``
(``resolve_baseline_tier``: rule 1's candidacy in distinct local days with
T117's recency gate, rule 2's week coverage, rule 3's last-used candidate;
T093/T094/T095/T117). F006 selects among the datasets instead: the
highest-fidelity **judgeable** one -- established and holding a judged
week -- skipped past when its latest baseline-window reading is more than
``RECENCY_TOLERANCE_DAYS`` behind any **established** dataset's (T164; the
reference population is wider than the candidates). ``selected_view``
hands ``judge`` and the route the selected dataset on the F005 series shape
(``SingleDatasetView``) until T159 renders the datasets themselves;
``resolve_baseline_tier`` is no longer on the verdict's path.

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
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
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
#: from ``D-7``. The constant is not published in ``thresholds``
#: ([[IDEA-070]], 2026-09-15).
#:
#: Why 28, and not any other value in the measured green band
#: ``[18, 44]``, measured 2026-09-15 and **scoped to the 394 tests the five
#: HRV suites held on that date less the tolerance pin that existed then**
#: (``BAND_CORPUS_WHEN_MEASURED``). The scope is half of
#: what the bracket means: the tolerance's own pins assert this value or
#: its measured consequences and are red at every other N by construction,
#: so over the *whole* of the five suites the green band is ``{28}`` and
#: the bracket is a statement about the rest of them. The band was
#: published without that scope until T121 measured it.
#:
#: **The corpus is named by a relation, not by a number** (review cycle 8,
#: ``acfebae``). It is the five suites' collected tests less the pins named in
#: ``BAND_CORPUS_EXCLUDES``, and both the live collection and that
#: subtraction are asserted by
#: ``test_hrv_trend_endpoint.test_the_scoped_suite_count_the_band_was_measured_over_is_pinned_not_published``
#: -- read the count from ``SCOPED_SUITE_COLLECTED`` there, never from a
#: literal here. T121 wrote the literal ("the five suites collect 395")
#: into this comment and three normative documents to give the band its
#: missing scope, and the next commit of T121's own fix batch added a test
#: to one of those five suites and made all four wrong, with nothing in
#: the tree able to notice. That relation is the *live* corpus, and it is
#: not what the bracket is a claim about: the bracket was run over the 394
#: members it had on 2026-09-15 (``BAND_CORPUS_WHEN_MEASURED``), and the
#: assertion reports how far the relation has grown past that rather than
#: re-scoping the bracket to whatever it holds today.
#:
#: The two ends are real and are what the band means: N=17 reds
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
#:   stays green. T121's replacement said "what actually reddened at 21
#:   was the hardcoded 27 row" -- a **singular**, and also wrong (review
#:   cycle 8, ``acfebae``). Measured over the walk's five rows at tolerances
#:   19..30: at 21 the ``gap`` 22, 27 **and** 28 rows all redden. What
#:   makes the ``GAP_RESET_DAYS + 1`` row the pin is not that it is the
#:   only red row at 21 but that it is the only one whose red *onset* is
#:   there -- green at 22, red at 21. Each admitted row's onset sits one
#:   step below its own ``gap``: 20, 21, 26 and 27 for the ``gap`` 21, 22,
#:   27 and 28 rows, so at 21 the 27 and 28 rows are already red and the
#:   21 row is not yet. That relation is asserted, not just stated, by
#:   ``test_the_seam_row_is_the_only_one_whose_red_onset_is_at_gap_reset_days``.
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
#:
#: **Since F006 (T155) this same constant, through the same
#: ``_recency_struck``, is the selection gate** (``select_dataset``; AC6/
#: AC7): a judgeable dataset whose latest baseline-window reading falls
#: more than this many days behind any **established** dataset's is skipped
#: (T164, 2026-09-20: the reference population was the judgeable datasets
#: from T155 until then, and narrowing it nearly doubled ``hrv_normal`` on
#: an entirely pre-layoff band against shipped F005 -- 1,896 -> 3,705 of
#: 307,500 -- because a stopped or weekless carrier left the reference set;
#: shipped F005's own rule-1 reference was every established tier). The
#: constant is inherited, not re-justified: the ``[18, 44]`` band above
#: was measured against the *fused* band, and its upper end no longer
#: binds because a stale trial is not judgeable -- do not cite it as if it
#: transferred (reference §10; T161/T162 re-measure it).
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
#: There is no "off the baseline tier" reason any more (F006, T152; AC3,
#: AC15): a reading of another tier is in that tier's own dataset, not in
#: this list. The partition is per dataset -- every stored row in
#: ``[D-66, D]`` is in exactly one dataset's ``series`` or in ``excluded``.
REASON_SAME_DAY_LATER_CAPTURE = "same_day_later_capture"
#: A reading inside ``[D-66, D]`` that predates the day the current
#: baseline era began (T092): it contributed to neither the baseline nor
#: the window, and ``research/00`` §1.6 wants it listed rather than
#: silently dropped. Parameterised with the reason the era began:
#: ``before_reset: coverage_gap`` for a resumption after a silence, and
#: ``before_reset: tier_change`` for the readings of a dataset's tier that
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
class HrvDataset:
    """One source tier's dataset for one target date (F006, T151;
    ``research/00`` §5.4 as amended 2026-09-18, spec §3.7.3's per-tier
    dataset model).

    ``series`` is one reading per local day **on this tier**, in date order,
    over the era-clipped ``[first, D]``; ``baseline`` and ``window`` are its
    two disjoint slices, ``[first, D-7]`` and ``[D-6, D]``. ``band``, ``n``
    and ``established`` are the band over ``baseline`` (``build_band``), its
    count in distinct local days, and ``n >= MIN_BASELINE_READINGS`` -- the
    same three values ``judge`` computes from the same two slices, carried
    here so every dataset reports them whether or not it is the one judged
    (AC1, AC2). No reading of another tier is in any of them: the anti-mixing
    rule is honoured by construction, not by a filter.

    ``judge`` reads ``tier``, ``baseline``, ``window`` and ``withheld`` of
    whatever it is handed, so a dataset can be judged on its own;
    ``select_dataset`` (T155) decides which one the route hands it.
    """

    #: The tier every reading here carries. ``None`` on no dataset
    #: ``build_series`` constructs -- it builds one only for a tier that is
    #: present; the ``tier is None`` shape ``judge`` names as the structural
    #: ``no_tier_sustains_a_trend`` cause is the empty ``SingleDatasetView``
    #: ``selected_view`` manufactures when the series holds no dataset.
    tier: str | None
    #: ``[max(D-66, <the gap resumption>, <this tier's era first day>,
    #: <this tier's last internal-hole resumption>), D-7]`` (T092/T098/T107
    #: composed; the hole term since T153, F006 AC17): the gap term is
    #: global and identical on every dataset; the era and hole terms are
    #: this dataset's own. The hole term is **unreported** -- it never sets
    #: ``reset_on`` / ``reset_reason`` (``_internal_hole_resumption``).
    baseline_window: tuple[date, date]
    series: tuple[Reading, ...]
    baseline: tuple[Reading, ...]
    window: tuple[Reading, ...]
    band: Band | None
    n: int
    established: bool
    #: T092. The local day the current baseline era began, when a reset is
    #: **reported** for this dataset, and why: ``REASON_COVERAGE_GAP`` (the
    #: global gap, the same on every dataset) or ``REASON_TIER_CHANGE`` (this
    #: dataset's own era boundary, ``tier_change_reset`` asked with its
    #: tier). Both ``None`` when nothing is reported. ``baseline_window`` is
    #: **not** a function of these -- the era clip applies whenever a
    #: boundary exists, reported or not (D4a, decision log 2026-09-13; T098),
    #: and whether or not a gap fired (T107).
    reset_on: date | None = None
    reset_reason: str | None = None
    #: T125/T132 at dataset scope (T158, AC24; ``research/00`` §5.4 (v)),
    #: asked of this dataset as if it were the selected one: the judged week
    #: is not a fair sample of it because a dataset that could not have been
    #: selected -- not judgeable, or skipped by the recency gate -- holds
    #: ``MIN_WINDOW_READINGS`` week days all later than every one of this
    #: dataset's. ``judge`` answers ``hrv_unavailable`` on it. See
    #: ``verdict_withheld``; computed by ``build_series`` once every dataset
    #: exists, since it reads the others' judgeability.
    #:
    #: **Defaults closed** (T134, moved here with the field). ``band``
    #: (``None``) and ``established`` (``False``) default toward
    #: ``hrv_unavailable``; an unset ``withheld`` must too, or a construction
    #: site that forgets the argument silently manufactures a dataset
    #: eligible for ``hrv_normal`` -- the direction ``research/00`` Section
    #: 1.7 forbids on weak evidence. ``build_series`` always passes the
    #: computed value; the default exists for the call site that does not
    #: yet exist, and ``test_hrv_series_withheld_defaults_closed.py`` pins it.
    withheld: bool = True


@dataclass(frozen=True)
class HrvSeries:
    """The series for one target date, bucketed in one zone: **N per-tier
    datasets over one globally-clipped population** (F006, T151).

    ``readings`` is the post-exclusion set of every tier inside
    ``[D-66, D]``, after the coverage-gap clip -- what T092 measures the gap
    on, because a gap is "no entry in the post-exclusion series", not "no
    stored row", and it is measured over **every tier together** (AC16): one
    tier's silence while another carries the series is not a gap.
    ``datasets`` holds one ``HrvDataset`` per tier present in ``readings``,
    in fidelity order, each built from its own tier's readings alone.
    ``excluded`` is the one shared list of every row inside the windows that
    contributed to nothing, and why: the screens, ``before_reset:
    coverage_gap`` for the readings the global clip removed, and each
    dataset's own ``same_day_later_capture`` and ``before_reset:
    tier_change`` -- every row exactly once (``research/00`` §1.6; the
    per-dataset ``included``/``excluded`` partition is T152's).

    **The two clips are not symmetric in what they rebind, deliberately.**
    The gap clip runs before the partition, so it rebinds ``readings``
    itself and ``baseline_window[0]`` for every dataset alike -- the gap's
    era is an era of *every* tier. The era-boundary clip is asked once per
    dataset and is a statement about **that tier's** era, so it rebinds that
    dataset's ``series`` alone; ``readings`` still contains readings a
    dataset's clip listed ``before_reset: tier_change``, an overlap inside
    this dataclass only. ``main.py`` never reads ``readings``.
    """

    target_date: date
    #: The IANA key the rows were bucketed in, so the response can report it.
    timezone: str
    #: The **global** window, ``[max(D-66, <the gap resumption>), D-7]``;
    #: each dataset's own is this further clipped at its era boundary.
    baseline_window: tuple[date, date]
    judged_window: tuple[date, date]
    readings: tuple[Reading, ...]
    excluded: tuple[Exclusion, ...]
    #: One per tier present in ``readings``, in ``TIER_FIDELITY`` order;
    #: empty when no reading of any tier exists in ``[D-66, D]``.
    datasets: tuple[HrvDataset, ...]
    #: The global coverage-gap resumption (``coverage_gap_reset``), or
    #: ``None``. Every dataset's ``reset_on``/``reset_reason`` report it
    #: when it is set; it is carried here because it is a property of the
    #: series as a whole, not of any one dataset.
    gap_reset_on: date | None = None


@dataclass(frozen=True)
class SingleDatasetView:
    """One dataset of an ``HrvSeries`` flattened onto the shape the F005
    series had, so that ``judge``, the route and every pin written against
    a single resolved tier read exactly what they read before the
    partition. Constructed only by ``selected_view`` (T155; T151's bridge
    built it over the retired resolver), which flattens the dataset
    ``select_dataset`` selects -- or, when nothing is judgeable, the
    presentation fallback (AC9; T156 formalises it) -- and retires with
    T159, which renders the datasets themselves.

    ``tier``, ``baseline_window``, ``series``, ``baseline``, ``window``,
    ``withheld``, ``reset_on`` and ``reset_reason`` are ``selected``'s;
    ``target_date``, ``timezone``, ``judged_window`` and ``readings`` are
    the series'; ``selection`` is the decision the view presents, with the
    judgeable and skipped datasets it can be reproduced from. ``excluded``
    is the series' own list, unchanged (AC15, T152): the other datasets'
    readings are in ``datasets``, each in its own, and are not re-listed
    here as anything. (Shipped F005 listed them ``off_baseline_tier:
    <tier>``, and T151/T155 kept that re-listing on this view as a shim
    until T152 retired the reason.)
    """

    target_date: date
    timezone: str
    baseline_window: tuple[date, date]
    judged_window: tuple[date, date]
    tier: str | None
    readings: tuple[Reading, ...]
    series: tuple[Reading, ...]
    baseline: tuple[Reading, ...]
    window: tuple[Reading, ...]
    excluded: tuple[Exclusion, ...]
    reset_on: date | None = None
    reset_reason: str | None = None
    withheld: bool = True
    #: The dataset this view flattens -- the selected one, or the fallback
    #: -- or ``None`` when the series holds no dataset at all.
    selected: HrvDataset | None = None
    #: The selection this view presents (``select_dataset``); ``None`` only
    #: on a view built without one.
    selection: Selection | None = None
    #: Every dataset of the series, in fidelity order (``HrvSeries.datasets``),
    #: so what the view does not present is still reachable from it: the
    #: readings of a non-selected tier are in its own dataset here, not in
    #: ``excluded`` (T152). T159 renders these.
    datasets: tuple[HrvDataset, ...] = ()
    #: Why ``selected`` is the dataset this view presents (T156): one of
    #: ``PRESENTATIONS`` -- ``PRESENTED_SELECTED`` when ``selection.selected``
    #: is it, else the ``_presentation_fallback`` clause that chose it --
    #: or ``None`` on the empty view. ``selection.selected_reason`` is the
    #: contract's ``selected_reason`` (AC13); this names the fallback clause
    #: beside it, so the null-selection case is legible on the view.
    presented_by: str | None = None


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


def _recency_struck(candidates: Sequence[str], last_read: Mapping[str, date]) -> set[str]:
    """Rule 1's recency gate (T117), as the **set it strikes** rather than the
    set it keeps.

    A candidate whose latest day in ``last_read`` falls more than
    ``RECENCY_TOLERANCE_DAYS`` behind the latest day of any candidate is
    struck. The comparison is between the candidates themselves, so a lone
    candidate is its own reference and is never struck, and an empty candidate
    list strikes nothing.

    **Why this is a function and not four lines inside
    ``resolve_baseline_tier``** (T125, 2026-09-16). ``build_series`` needs the
    same set the gate applied in order to ask T125's question -- *did the gate
    strike the tier the athlete is currently using?* -- and it must be the same
    set, not a second transcription of the rule: the defect T125 closes exists
    precisely because two places measured recency over two different windows.
    The gate itself is unchanged, up to and including which tier it returns on
    every series in the suites (T125 measured tier-identical to shipped on all
    2050 sweep geometries); only the set became nameable from outside.
    """
    if not candidates:
        return set()
    latest = max(last_read.get(tier, date.min) for tier in candidates)
    return {
        tier
        for tier in candidates
        if (latest - last_read.get(tier, date.min)).days > RECENCY_TOLERANCE_DAYS
    }


def is_judgeable(dataset: HrvDataset) -> bool:
    """AC8's precondition of candidacy, stated once: established over the
    dataset's own **post-clip** baseline window (read from the dataset,
    never recounted) **and** at least ``MIN_WINDOW_READINGS`` distinct
    judged-week days. ``select_dataset`` draws its candidates by it and
    ``verdict_withheld`` (T158) draws the set it asks the order clause of by
    its negation, so the two can never disagree about who could have been
    selected."""
    return dataset.established and len(_days(dataset.window)) >= MIN_WINDOW_READINGS


def verdict_withheld(
    dataset: HrvDataset,
    datasets: Iterable[HrvDataset],
    skipped: Collection[str | None],
) -> bool:
    """Whether the judged week is too unrepresentative of the athlete *now*
    for any verdict to be asserted on ``dataset`` -- T125's order clause
    (2026-09-16, form 2; widened T132, form B), **retained at dataset scope**
    (T158, 2026-09-19; F006 AC24; ``research/00`` §5.4 (v)).

    True when another dataset **that could not have been selected** -- one
    that is not judgeable (``is_judgeable``: unestablished, or fewer than
    ``MIN_WINDOW_READINGS`` week days), or one the recency gate ``skipped``
    -- holds at least ``MIN_WINDOW_READINGS`` distinct days inside the
    judged week and **every one of them is later than every judged-week
    day of ``dataset``**. That is the returning-*or-brand-new*-device shape,
    stated without a notion of "device" in the vocabulary: the athlete has
    gone back to (or bought) a source the selected dataset does not read,
    he has recorded a full week's-worth of mornings on it, and the readings
    the verdict would be computed from are all *older* than every one of
    them. The week is then not a fair sample of the dataset being judged,
    so no verdict is asserted and the response says ``hrv_unavailable``.
    Asked of every dataset by ``build_series``, as if that dataset were the
    selected one, so a dataset judged on its own answers the same way the
    route's view of it does.

    **What moved from F005's form, and what did not.** F005 asked the
    clause of ``struck | never_used``: the tiers rule 1's gate struck among
    the *candidates* (``>= MIN_BASELINE_READINGS`` raw baseline-window days)
    and, from T132, the tiers with **zero** baseline-window days and a full
    week. ``struck`` is ``skipped`` here -- the same ``_recency_struck``
    over the same baseline-window ``_last_read``, over the same
    **established** reference population (T164), taken once by
    ``build_series`` for every dataset and once by ``select_dataset`` (T125:
    the set must be *the* set, pinned equal in
    ``test_the_withhold_reads_the_skipped_set_selection_reads_and_is_asked_of_every_dataset``).
    ``never_used`` is widened to **not judgeable**: T132 touched only a tier
    with zero baseline-window presence and left a tier with 1..13 days
    "exactly as shipped" -- not withheld, the outgoing tier's stale week
    promoted ``hrv_normal``. AC24 asks it of every dataset that is not
    judgeable, because a dataset with one baseline reading can no more
    carry a verdict than one with none, and the athlete's suppressed week
    is just as unread either way. That is the **one verdict this
    restatement moves**: a non-judgeable dataset with 1..13 baseline days
    and a full later week now withholds (``hrv_normal ->
    hrv_unavailable``, §1.7's freely tolerated direction; T130's era-10
    row in ``test_hrv_dataset_populations.py`` is the measured instance,
    shipped's single ``FN`` at ``c = 0``). The strict day-order clause is
    unchanged to the character. A judgeable dataset that is **not**
    skipped is never in the set: it could have been selected and lost on
    fidelity rank alone, the selected dataset decides (§5.4 (iii)) and its
    disagreement is reported (AC10) -- withholding on it would be the
    withhold-on-disagreement form the decision log rejected.

    **Why "not judgeable **or skipped**", when AC24 says "not judgeable".**
    T125's own population -- the returning strap, established on a
    pre-layoff era and back for three mornings while the watch's week is
    the stale one -- is judgeable under AC8 and *skipped* under AC6. Read
    literally, AC24's set drops it and the device-return walk's ``r = 3``
    and ``r = 4`` promote ``hrv_normal`` on the watch's pre-return week --
    the exact reproduction T125 closed. The set is therefore "every
    dataset that could not have been selected", which is what
    ``struck | never_used`` always meant; IDEA-083 records the wording gap.

    **Day sets, not counts, and that is why this reads windows and not
    ``n``.** "Entirely pre-return" is a statement about the order of two
    sets of days; counts cannot express it. The rule that could be written
    with counts -- "the other dataset covers the week" -- is satisfied by an
    *abandoned trial* too (T125 form 1, measured: it re-admits the July
    trial and turns ``test_stale_candidacy_...`` red), which is the whole
    difficulty: a device return and an abandoned trial differ in the day
    order, not in the counts. Measured 2026-09-16 by dropping the order
    clause and keeping the count: **5 red** across the five HRV suites. The
    order clause is load-bearing, and it is pinned from both sides:
    deleting ``not series.withheld`` from ``judge`` reds the device-return
    walk's four cases at ``r = 3``, and dropping this pass out of
    ``build_series`` reds AC24's own series
    (``test_a_brand_new_device_on_the_selected_datasets_stale_week_withholds_the_verdict``).

    **This does not re-select.** ``select_dataset`` reads nothing here (the
    withhold is applied to the promoted verdict, not to candidacy);
    ``tier``, ``baseline_window``, the band, the reset and ``excluded`` are
    whatever they were, and the withhold is downstream of all of them and
    changes only the verdict. On T125's 2050-geometry sweep the resolved
    tier and the reported baseline window were identical to shipped on
    every row, and the only verdict change in either direction was
    ``hrv_normal -> hrv_unavailable``, 72 times.

    **The residual, named in F005's Negative Class -- and it is a closed
    form, not a count.** ``MIN_WINDOW_READINGS`` on the returning dataset is
    the guard that keeps a stray cross-device capture from withholding a
    legitimate verdict, and it is *the same constant* that makes the
    athlete's opening mornings back invisible to this rule: while he holds
    fewer than ``MIN_WINDOW_READINGS`` return days in the judged week, the
    verdict there still comes from pre-return readings and still reads
    ``hrv_normal``. **How many such mornings:** ``min(WINDOW_DAYS -
    MIN_WINDOW_READINGS, k3)``, where ``k3`` is the offset at which the
    returning dataset's ``MIN_WINDOW_READINGS``-th distinct local day enters
    the judged week (T145, measured 2026-09-18 over all 64 weekly return
    patterns containing day 0, on this arm and on T132's ``never_used`` arm,
    zero mismatches, stable for layoffs s = 33..48). It is **four** mornings
    whenever the return is captured **sub-daily** (4/wk spread, 3/wk, 2/wk);
    a 4/wk *clustered* return is two, like the daily one, so the axis is the
    spacing of the captures, not their weekly count. Only ``k3`` belongs to
    this rule: the cap ``WINDOW_DAYS - MIN_WINDOW_READINGS`` = 4 is the
    **carrier's** judged-week coverage expiring, and it would end the
    residual whether or not this withhold existed. No form measured at T125
    closes the residual, and loosening the constant to reach it is exactly
    the change that starts producing false withholds, so the behaviour is
    **left unchanged** (user decision 2026-09-18, review cycle 10).

    **And at sub-daily density this withhold decides no verdict at all.** At
    4/wk-spread and 3/wk it flips ``withheld`` true only at ``k = 4..6``,
    where ``readings_in_window`` is already 2, 1 and 0 and
    ``week_too_thin`` precedes it in ``_unavailable_reason``'s fixed order
    -- the verdict would be identical with the withhold deleted. At 2/wk it
    never fires (checked to ``k = 40``). Any new measurement here must vary
    capture density on **both** datasets (AC19), and a walk indexed by
    "days since return" must say whether it means days elapsed or mornings
    captured.

    **The carrier-overlap disarm, pinned as the current fact (T130).** The
    order clause is a fact about the judged week, so one carrier morning
    inside the return -- the athlete puts the strap back on and does not
    take the watch off -- makes "every one later than every" false and the
    withhold never fires (0 of 2050 withheld at any ``c >= 1``; flips 54 /
    72 / 90 at ``c`` = 1 / 2 / >= 3). Nine candidate predicates were
    measured and none survives overlap without an unjustified parameter;
    the user deferred it to this sprint's measurement tasks, and it is
    **not** fixed here --
    ``test_probe_the_carrier_still_recording_through_the_return_disarms_the_withhold``
    pins both rows, and T161/T162 sweep the geometry against shipped F005.

    **T132's own residual, structural rather than a corner case.** For the
    first ``MIN_WINDOW_READINGS`` days, a **legitimate, permanent** device
    switch is the *same shape* as T132's forbidden geometry: an
    un-established dataset holding ``>= MIN_WINDOW_READINGS`` week days,
    every one later than the selected dataset's. Nothing in the two
    datasets' windows separates "a device he will never use again" from "a
    device he bought yesterday and will use forever". This widening
    therefore also silences ``hrv_normal`` on a permanent switch's third and
    fourth mornings at daily or 4/wk-clustered capture (T132/T145/T147,
    pinned in ``test_hrv_trend_band.test_the_return_residual_turns_on_capture_spacing_not_weekly_count``)
    -- the *freely tolerated* direction (``research/00`` §1.7), accepted as
    two days of silence per permanent device switch in exchange for closing
    the forbidden direction on a brand-new device that goes unused again.
    ``tier_change_reset`` and, under F006, the new dataset's own
    establishment are the mechanisms that distinguish the two in general;
    no predicate over a single week's shape can do what a time-accumulating
    mechanism is for.
    """
    newest_judged = max(_days(dataset.window), default=date.min)
    for other in datasets:
        if other is dataset or other.tier == dataset.tier:
            continue
        if is_judgeable(other) and other.tier not in skipped:
            # It could have been selected and lost on fidelity rank alone:
            # the selected dataset decides, and it is reported as disagreeing.
            continue
        days = _days(other.window)
        if len(days) >= MIN_WINDOW_READINGS and min(days) > newest_judged:
            return True
    return False


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
    (rule 4(b)) -- the current window is tested by the dataset's own tier's
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
       candidate is its own reference and is never struck. The constant
       is not published in ``thresholds`` ([[IDEA-070]], 2026-09-15).
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
    # Rule 1's recency condition (T117): relative to the candidates
    # themselves, never to ``D-7``, so a lone candidate is its own reference
    # and is never struck. Factored into ``_recency_struck`` by T125, which
    # needs the struck set itself at the verdict; the gate is unchanged.
    struck = _recency_struck(candidates, last_read)
    candidates = [tier for tier in candidates if tier not in struck]
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
    """Exclude, clip the global gap, partition by tier, collapse within each
    -- in that order -- and return one ``HrvDataset`` per tier present.

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

    **N datasets, one population** (F006, T151). Every tier present in the
    gap-clipped readings gets a dataset built from its own readings alone,
    with its own band, ``n``, ``established`` and ``withheld``; a tier with
    readings only in the judged week gets one with an empty baseline (no
    band, ``hrv_unavailable`` whichever is judged), which is what F005's
    empty-baseline fallback used to decide by choosing a tier. Which dataset
    is judged is ``select_dataset``'s (T155), asked of the returned series.

    **Resets (T092).** The coverage gap is detected between the exclusion
    step and the partition, because it clips the baseline window of *every*
    dataset before any tier is looked at -- it measures the silence of the
    series as a whole, and one tier's silence while another carries the
    series is no gap (AC16). The sustained-tier-change rule runs once per
    dataset after its collapse: it finds the era boundary between the tier
    that sustained the previous window ``[D-126, D-67]`` (``sustained_tier``,
    rule 1 alone) and this dataset's tier, when the latter sustains the
    current window and the readings on the wrong side of that boundary are
    never dense enough to be a baseline of their own (``tier_change_reset``
    / ``_era_boundary``, T094; judged over both windows since sprint-005
    review cycle 3, with T095's density tolerance; per dataset since T151,
    re-derived by T154).

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

    **A third clip, per dataset and unreported** (F006 AC17, T153). After
    the two above, each dataset's own baseline-window days are scanned for
    an internal capture hole of more than ``GAP_RESET_DAYS`` silent local
    days -- one tier's silence while another tier bridged it, which the
    global gap cannot see (AC16) and ``_era_boundary`` cannot either. The
    window is clipped at the last such resumption, the pre-hole readings are
    listed ``before_reset: coverage_gap``, and nothing is reported for it:
    ``reset_on`` / ``reset_reason`` remain the gap's or the era rule's.
    A hole of exactly ``GAP_RESET_DAYS`` silent days is not clipped, as it
    is not a gap (``_internal_hole_resumption``).
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
    # **The unclipped population, kept before the gap rule can rebind
    # ``readings``** (T129). Every reading of every tier inside
    # ``[D-66, D]``, which is what rule 4 counts its *strays* over: the
    # clip decides which readings enter the **band**, and must not also
    # decide which readings ``_era_boundary`` can **see**. Until T129 the
    # same clipped list answered both, so the readings of
    # ``[D-66, gap_reset_on)`` were absent from the stray count and a
    # coverage gap could *create* an era boundary the full capture history
    # refuses -- a later boundary, on a band the athlete's own history does
    # not support (G-C7-3; ``tier_change_reset``).
    unclipped_readings = tuple(readings)
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

    # The population every per-dataset rule reads: every tier, inside the
    # (possibly clipped) baseline window -- and, for the week-coverage and
    # withhold questions, every tier inside the judged week. Computed once,
    # before the partition, so each dataset is asked its questions against
    # the same cross-tier facts (T125: the struck set must be the one set).
    baseline_readings = _within(readings, baseline)
    week_readings = _within(readings, judged)
    baseline_last_read = _last_read(baseline_readings)

    # F006 (T151): one dataset per tier present, in fidelity order. The
    # per-day collapse is **within** a tier (AC4): the earliest capture of
    # the day on that tier is kept and every later one is listed
    # ``same_day_later_capture``. A day carrying two tiers' captures feeds
    # both datasets (AC3); no reading is excluded for being off *the*
    # baseline tier, because no tier is that any more -- the F005 reason
    # is retired (T152), and the partition is per dataset (AC15): a row is
    # in exactly one dataset's ``series`` or in ``excluded``, never both.
    datasets: list[HrvDataset] = []
    for tier in TIER_FIDELITY:
        on_tier = _of_tier(readings, tier)
        if not on_tier:
            continue
        series_by_day: dict[date, Reading] = {}
        for reading in on_tier:
            if reading.date in series_by_day:
                excluded.append(Exclusion(reading.date, reading.session_id, REASON_SAME_DAY_LATER_CAPTURE))
            else:
                series_by_day[reading.date] = reading
        series = tuple(series_by_day[day] for day in sorted(series_by_day))
        dataset_window = baseline

        # The cross-tier era question (T092/T094), asked once per dataset
        # with **this dataset's** tier -- the one call site, inside this
        # loop, receiving no resolved tier because none exists under F006
        # (T151 placed it; T154 pins it). Clauses (a), (b) and (c) are
        # unchanged and are asked over the same cross-tier populations for
        # every dataset: ``previous_readings``, the gap-clipped
        # ``baseline_readings`` for (a), and the one unclipped
        # ``stray_population`` of every tier for (c) (T129, kept global --
        # narrowed to this dataset's own readings, another tier's habit
        # inside this era would vanish from the stray count and T094's
        # refused reset would come back). The answer is this dataset's own
        # ``reset_on`` / ``reset_reason``; the route presents the selected
        # dataset's (``selected_view``).
        boundary = tier_change_reset(
            previous_readings,
            tier,
            baseline_readings,
            week_readings,
            judged,
            stray_population=unclipped_readings,
        )
        dataset_reset_on, dataset_reset_reason = reset_on, reset_reason
        if boundary is not None:
            # D4a (T098), made true of a gapped series by T107 (review cycle
            # 6, G-C6-5): the clip is unconditional -- on the report *and* on
            # the coverage gap. ``research/00`` §5.4 says the now-sustaining
            # tier's pre-boundary readings are *never* in the band; only the
            # *report* was ever the gap's to win.
            #
            # The two clips compose as the **later** first day. Each says
            # the same kind of thing -- these readings are not of this
            # baseline's era -- so the band must contain neither the pre-gap
            # nor the pre-boundary readings, and the admissible set is the
            # intersection. ``baseline[0]`` already carries
            # ``max(D-66, gap_reset_on)``, so this one ``max`` composes all
            # three. The era may have begun before D-66 (T094: ``first_day``
            # is its true first day, not the first inside the window).
            #
            # Nothing is listed twice (``research/00`` §1.6). The gap branch
            # rebinds ``readings`` before the partition, so ``series`` holds
            # only what it kept; this branch excludes out of ``series``. A
            # boundary earlier than the resumption removes nothing here
            # rather than re-excluding what the gap already took.
            dataset_window = (max(boundary.first_day, baseline[0]), baseline[1])
            kept, excluded = _exclude_before_reset(
                list(series), excluded, boundary.first_day, REASON_TIER_CHANGE
            )
            series = tuple(kept)
            # The gap keeps the *report* -- rule 4's precedence is unchanged,
            # and only it was ever precedence over. A gapped series can
            # therefore report ``coverage_gap`` on a window clipped later
            # than the resumption (T107).
            if boundary.reported and gap_reset_on is None:
                dataset_reset_on = boundary.first_day
                dataset_reset_reason = REASON_TIER_CHANGE

        # F006 AC17, T153: the **unreported per-dataset band clip** at an
        # internal capture hole. Neither rule above can see one tier's own
        # silence while another tier bridges it -- the gap is global by
        # design (AC16) and ``_era_boundary`` needs an old-tier reading
        # followed by a new-tier one, so on one dataset it returns ``None``
        # (measured at planning). Scanned over **this dataset's** days
        # inside the window the two clips above left it, so a hole before
        # an era boundary or a resumption is already gone and is not
        # counted twice, and the three clips compose as the latest first
        # day. Counted exactly as ``coverage_gap_reset`` counts (more than
        # ``GAP_RESET_DAYS`` silent days; ``_internal_hole_resumption``).
        # The pre-hole readings leave the series for ``excluded`` as
        # ``before_reset: coverage_gap`` -- the dataset's own coverage gap,
        # an already-published reason -- so the band, ``n`` and
        # ``established`` are the post-hole era's (AC8 counts established
        # post-clip) and every row is still listed once (§1.6). Nothing is
        # **reported**: ``reset_on`` / ``reset_reason`` stay whatever the
        # global gap or ``tier_change_reset`` decided, which can leave
        # ``window[0]`` after ``reset_on`` (a state T107 already allows).
        hole_resumption = _internal_hole_resumption([r.date for r in _within(series, dataset_window)])
        if hole_resumption is not None:
            dataset_window = (hole_resumption, dataset_window[1])
            kept, excluded = _exclude_before_reset(list(series), excluded, hole_resumption, REASON_COVERAGE_GAP)
            series = tuple(kept)

        dataset_baseline = _within(series, dataset_window)
        datasets.append(
            HrvDataset(
                tier=tier,
                baseline_window=dataset_window,
                series=series,
                baseline=dataset_baseline,
                window=_within(series, judged),
                band=build_band(ln_rmssd(reading) for reading in dataset_baseline),
                n=len(dataset_baseline),
                established=len(dataset_baseline) >= MIN_BASELINE_READINGS,
                reset_on=dataset_reset_on,
                reset_reason=dataset_reset_reason,
                # Closed (T134) until the dataset-scope pass below, which
                # needs every dataset's judgeability and so runs after all
                # of them exist.
                withheld=True,
            )
        )

    # T125/T132 at dataset scope (T158, AC24; ``research/00`` §5.4 (v)),
    # asked of every dataset as if it were the selected one: the set the
    # order clause is asked about is every dataset that could not have been
    # selected -- not judgeable, or skipped by the recency gate. The gate is
    # the one ``select_dataset`` reuses, over the same baseline-window
    # ``_last_read``, so ``skipped`` here is *the* set (T125), not a second
    # transcription; the equality is pinned. On the real baseline window
    # only: an empty window has nothing judgeable, nothing skipped and no
    # band anyway, so F005 answered it ``False`` through its fallback and
    # this does the same.
    judgeable = {dataset.tier for dataset in datasets if is_judgeable(dataset)}
    established = [dataset.tier for dataset in datasets if dataset.established]
    skipped = _recency_struck(established, baseline_last_read) & judgeable
    datasets = [
        replace(dataset, withheld=bool(baseline_readings) and verdict_withheld(dataset, datasets, skipped))
        for dataset in datasets
    ]

    # Once, after every clip has had its say: each moves readings into
    # ``excluded`` out of order (``_exclude_before_reset`` appends what it
    # drops), and nothing between here and there reads the order.
    excluded.sort(key=lambda e: (e.date, e.session_id))

    return HrvSeries(
        target_date=target_date,
        timezone=zone.key,
        baseline_window=baseline,
        judged_window=judged,
        readings=tuple(readings),
        excluded=tuple(excluded),
        datasets=tuple(datasets),
        gap_reset_on=gap_reset_on,
    )


#: Why the selected dataset is the one promoted (F006 AC13, T156; T159
#: renders it as ``selected_reason``). Closed: non-null exactly when
#: ``Selection.selected`` is non-null, null with null (T144's shape).
#: ``highest_fidelity_judgeable`` -- no judgeable dataset outranks it;
#: ``higher_fidelity_skipped_stale`` -- one did, and the recency gate (AC6)
#: skipped it.
SELECTED_HIGHEST_FIDELITY = "highest_fidelity_judgeable"
SELECTED_HIGHER_FIDELITY_STALE = "higher_fidelity_skipped_stale"
SELECTED_REASONS: tuple[str, ...] = (SELECTED_HIGHEST_FIDELITY, SELECTED_HIGHER_FIDELITY_STALE)

#: How the dataset a ``SingleDatasetView`` presents came to be presented
#: (T156; ``SingleDatasetView.presented_by``): the selection, or one of the
#: three clauses of ``_presentation_fallback``, in the order they are asked.
PRESENTED_SELECTED = "selected"
FALLBACK_ESTABLISHED_READ_LAST = "fallback_established_read_last"
FALLBACK_DENSEST_BASELINE = "fallback_densest_baseline"
FALLBACK_DENSEST_WEEK = "fallback_densest_week"
PRESENTATIONS: tuple[str, ...] = (
    PRESENTED_SELECTED,
    FALLBACK_ESTABLISHED_READ_LAST,
    FALLBACK_DENSEST_BASELINE,
    FALLBACK_DENSEST_WEEK,
)


@dataclass(frozen=True)
class Selection:
    """Which dataset is promoted into ``baseline``/``band``/``hrv_status``
    for one target date, and the facts it was decided on (F006, T155;
    ``research/00`` §5.4 amended 2026-09-18 (ii); AC5-AC8).

    ``judgeable`` are the tiers of the candidate datasets, in fidelity
    order; ``skipped`` the subset the recency gate struck, in the same
    order; ``last_read`` the latest baseline-window local day of **every**
    tier present (``_last_read`` over the baseline-window slice, AC6's
    normative scope), whether judgeable or not; ``reference`` the latest of
    those over the **established** tiers -- the gate's reference
    population, wider than its candidates since T164 -- and ``None`` only
    when no dataset is established. ``selected`` is the first judgeable
    tier's dataset not skipped, or ``None``; with an established but not
    judgeable dataset holding the reference, every candidate can be
    skipped and ``selected`` is ``None`` with ``judgeable`` non-empty. Everything a witness needs to print the slice the
    decision compared is here (``describe``), so a pin can say which
    datasets were candidates, which were skipped and by how many days.

    ``band_readings`` is every dataset read against its **own** band
    (``read_against_band``, T157), in fidelity order, judgeable or not;
    ``disagreed_with`` the tiers among them on the other side of their band
    from ``selected`` (``disagreed_with``; AC10/AC11). Empty when nothing is
    selected: a disagreement is with a verdict, and the presentation
    fallback confers none (IDEA-082, settled by T156: the reading that
    keeps the field's name honest and the fallback verdict-free).
    ``selected_reason`` is AC13's closed enum, derived from ``skipped``.
    """

    selected: HrvDataset | None
    judgeable: tuple[str, ...]
    skipped: tuple[str, ...]
    last_read: Mapping[str, date]
    reference: date | None
    band_readings: tuple[BandReading, ...] = ()
    disagreed_with: tuple[str, ...] = ()

    @property
    def selected_reason(self) -> str | None:
        """Why ``selected`` is the one (AC13; one of ``SELECTED_REASONS``),
        ``None`` exactly when nothing is selected. Derived from the facts
        this selection already carries rather than stored beside them, so
        it cannot disagree with ``skipped``: a skipped tier of higher
        fidelity than the selected one is the only way a candidate other
        than the first by rank came to be selected."""
        if self.selected is None or self.selected.tier is None:
            return None
        rank = _FIDELITY_RANK[self.selected.tier]
        if any(_FIDELITY_RANK[tier] < rank for tier in self.skipped):
            return SELECTED_HIGHER_FIDELITY_STALE
        return SELECTED_HIGHEST_FIDELITY

    def disagreement(self) -> str:
        """One line per dataset: ``n``, judged-week days, ``band.lo``, the
        week mean and which side it reads -- the slice ``disagreed_with``
        compared, for a witness to print."""
        chosen = None if self.selected is None else self.selected.tier
        rows = " ".join(r.describe() for r in self.band_readings)
        return f"selected={chosen} disagreed_with={list(self.disagreed_with)} against_band=[{rows}]"

    def gap(self, tier: str) -> int | None:
        """How many days ``tier``'s latest baseline-window reading falls
        behind the reference; ``None`` for a tier that is not judgeable."""
        if self.reference is None or tier not in self.judgeable:
            return None
        return (self.reference - self.last_read[tier]).days

    def describe(self) -> str:
        """One line naming the candidates, the skipped ones and the gaps."""
        parts = []
        for tier in TIER_FIDELITY:
            if tier not in self.last_read:
                continue
            gap = f"(-{self.gap(tier)})" if tier in self.judgeable else ""
            parts.append(f"{tier}:{self.last_read[tier]}{gap}{'!' if tier in self.skipped else ''}")
        chosen = None if self.selected is None else self.selected.tier
        return (
            f"selected={chosen} judgeable={list(self.judgeable)} skipped={list(self.skipped)} "
            f"reference={self.reference} last_read=[{' '.join(parts)}]"
        )


def select_dataset(series: HrvSeries) -> Selection:
    """The dataset the verdict is taken from: the highest-fidelity
    **judgeable** dataset, skipped past on baseline-window staleness (F006,
    T155; ``research/00`` §5.4 amended 2026-09-18 (ii); spec §3.7.4).

    1. **Candidates are the judgeable datasets** (AC8): ``established`` --
       at least ``MIN_BASELINE_READINGS`` distinct local days in the
       dataset's own **post-clip** baseline window, read from the dataset,
       never recounted over the shared readings -- **and** at least
       ``MIN_WINDOW_READINGS`` distinct judged-week days. F005's rule 2
       ("covers the week") and the count half of its rule 1 are this one
       condition, stated once.
    2. **The highest fidelity rank wins** (AC5; ``_FIDELITY_RANK``, §3.7.1's
       ratified hierarchy preserved: chest-strap raw RR over the numeric
       tiers). The numeric confidence weight §3.7.1 defines is reported per
       dataset and **never** consulted here (reference §3, the two senses of
       quality split), so no recency-against-quality exchange rate exists.
    3. **A candidate is skipped** (AC6/AC7) when its latest reading **within
       the baseline window** ``[D-66, D-7]`` falls more than
       ``RECENCY_TOLERANCE_DAYS`` behind the latest baseline-window reading
       of any **established** dataset -- strictly greater than. The gate is
       F005's ``_recency_struck`` reused verbatim, over ``_last_read`` of the
       baseline-window slice, which is exactly the scope AC6 makes normative
       (task technical notes: a reuse, not a new computation; no window is
       computed here). The reference maximum is taken **once,
       simultaneously**, over every **established** dataset -- the ones that
       are not judgeable and the ones about to be skipped alike -- while the
       *candidates* struck from it stay the judgeable datasets, so a lone
       established dataset is its own reference and the dataset holding the
       maximum can never be skipped.

       **Why the population is the established datasets** (T164, 2026-09-20;
       ``research/00`` 5.4 amended first, then spec 3.7.3/3.7.4, then AC6/
       AC7). T155 took the reference over the judgeable datasets alone.
       Shipped F005's rule 1 took its equivalent over every **established**
       tier, so a carrier that had stopped -- or whose judged week was too
       thin to be judgeable -- still struck a returning dataset whose band is
       entirely pre-layoff. T162 measured the narrowing over 307,500 rectangle
       rows and 24,000 walk rows, on both modules and under both overlap
       variants: ``hrv_normal`` on an entirely pre-layoff band rose **1,896 ->
       3,705** (x1.95) and **96 -> 254** (x2.65), worse at every ``c``, on 82
       of 150 cells -- 1.7's forbidden direction on the population AC6 exists
       to close, reopened at AC7 (IDEA-080). Widening the population restores
       F005's rate. The cost, knowingly re-imported: a **lone judgeable**
       dataset is no longer automatically its own reference -- an established
       but weekless dataset read later strikes it -- so every candidate can be
       skipped at once and the AC9 fallback presents one verdict-free.

    **Why the window is normative** (reference §9, the defect a first draft
    got wrong). A strap established on ``D-66..D-36``, silent to ``D-5``
    while the snapshot carried the series, and back on ``D-4/D-2/D-0`` is
    established and judgeable and the highest fidelity; its *latest*
    reading is ``D-0``, gap 0, and an unqualified gate selects it and judges
    the athlete against a band every reading of which is 36 to 66 days old
    and entirely pre-layoff -- ``hrv_normal`` on a stale band, §1.7's
    forbidden direction, on the exact mechanism T125 closed. Its latest
    reading *in the window* is ``D-36``, 29 behind the snapshot's ``D-7``,
    and it is skipped. Per-tier baselining removed every clip that checked
    the *baseline's* recency; this gate is what puts the question back.

    Selection runs per local day (the route calls this per judged day, AC14)
    and reads nothing from yesterday: it is path-independent by
    construction, which is what the deferred hysteresis (AC23) would give up.
    ``withheld`` is not consulted here -- it is ``judge``'s, at dataset scope
    (T158). Two datasets of one tier cannot come out of ``build_series``
    (the dataset key is the tier, reference §1) and are refused rather than
    resolved by position.
    """
    seen: set[str] = set()
    for dataset in series.datasets:
        if dataset.tier is None or dataset.tier in seen:
            raise ValueError(
                f"two datasets carry the tier {dataset.tier!r}: the dataset key is the tier, so this "
                "series was not built by build_series and there is no rank to select on"
            )
        seen.add(dataset.tier)

    by_rank = sorted(series.datasets, key=lambda d: _FIDELITY_RANK[d.tier])
    judgeable = [d for d in by_rank if is_judgeable(d)]
    last_read = _last_read(_within(series.readings, series.baseline_window))
    candidates = [d.tier for d in judgeable]
    # T164: the reference population is every **established** dataset, the
    # candidates struck from it are still the judgeable ones. One call, so
    # the maximum is still taken once and simultaneously (AC7).
    established = [d.tier for d in by_rank if d.established]
    struck = _recency_struck(established, last_read)
    skipped = {tier for tier in candidates if tier in struck}
    reference = max((last_read[tier] for tier in established if tier in last_read), default=None)
    selected = next((d for d in judgeable if d.tier not in skipped), None)
    against_band = tuple(read_against_band(d) for d in by_rank)
    return Selection(
        selected=selected,
        judgeable=tuple(candidates),
        skipped=tuple(tier for tier in candidates if tier in skipped),
        last_read=last_read,
        reference=reference,
        band_readings=against_band,
        disagreed_with=disagreed_with(selected, against_band),
    )


@dataclass(frozen=True)
class BandReading:
    """One dataset read against its **own** band, whether or not it is
    judgeable (F006, T157; AC10). ``band`` and ``week_mean`` are exactly
    ``judge``'s ``band`` and ``ln_rmssd_7d_mean`` for this dataset --
    ``build_band`` over its baseline (``None`` under two readings) and the
    ``fmean`` of ``ln rMSSD`` over its judged week (``None`` on an empty
    week). ``below`` is ``week_mean < band.lo``, **strictly less**, the
    comparison ``judge`` makes for ``hrv_suppressed``; ``None`` when either
    side is missing, so a dataset with one baseline reading, or none in the
    week, cannot disagree and is visible here carrying ``n`` and
    ``week_days`` instead (AC10's second sentence; T159 renders it).
    """

    tier: str
    n: int
    week_days: int
    band: Band | None
    week_mean: float | None
    below: bool | None

    def describe(self) -> str:
        lo = None if self.band is None else f"{self.band.lo:.4f}"
        mean = None if self.week_mean is None else f"{self.week_mean:.4f}"
        side = {True: "below", False: "within", None: "-"}[self.below]
        return f"{self.tier}:n={self.n},week={self.week_days},lo={lo},mean={mean},{side}"


def read_against_band(dataset: HrvDataset) -> BandReading:
    """``dataset`` read against its own band, by asking ``judge`` for the
    band and the week mean it computes -- one arithmetic, not a second
    derivation of it -- and comparing them the way ``judge`` does. The
    verdict ``judge`` returns is discarded here: it is gated on
    judgeability and ``withheld``, and this reading deliberately is not."""
    verdict = judge(dataset)
    below = (
        None
        if verdict.band is None or verdict.ln_rmssd_7d_mean is None
        else verdict.ln_rmssd_7d_mean < verdict.band.lo
    )
    return BandReading(
        tier=dataset.tier or "",
        n=verdict.baseline_n,
        week_days=len(_days(dataset.window)),
        band=verdict.band,
        week_mean=verdict.ln_rmssd_7d_mean,
        below=below,
    )


def disagreed_with(selected: HrvDataset | None, readings: Iterable[BandReading]) -> tuple[str, ...]:
    """The tiers whose reading against their own band is on the **other
    side** from the selected dataset's (F006, T157; AC10/AC11;
    ``research/00`` §5.4 amended 2026-09-18 (iii)), in the order given --
    fidelity order from ``select_dataset``.

    Both directions (AC11): a dataset reading below while the selected one
    reads within, and one reading within while the selected reads below.
    A dataset with no ``below`` -- no band (fewer than two baseline
    readings) or no week mean -- cannot disagree. Judgeability is never
    consulted: a band from two readings, a week of one, a withheld dataset,
    all read and all can be named (AC10, taken literally). Nothing here
    feeds ``judge``: the verdict is the selected dataset's, unchanged.

    ``()`` when nothing is selected. The presentation fallback (AC9)
    presents a dataset but confers no verdict, and a disagreement is with a
    verdict; naming a dissenter against ``hrv_unavailable`` would report a
    contradiction of a claim never made. ``band_readings`` still carries
    every dataset's reading on that day.
    """
    if selected is None:
        return ()
    readings = tuple(readings)
    own = next((r.below for r in readings if r.tier == selected.tier), None)
    if own is None:
        return ()
    return tuple(r.tier for r in readings if r.tier != selected.tier and r.below is not None and r.below != own)


@dataclass(frozen=True)
class Presentation:
    """The dataset ``_presentation_fallback`` presents and the clause that
    chose it (one of the three ``FALLBACK_*`` names)."""

    dataset: HrvDataset
    clause: str


def _presentation_fallback(series: HrvSeries, last_read: Mapping[str, date]) -> Presentation | None:
    """The dataset ``baseline``/``band`` are populated from when **no**
    dataset is judgeable -- F005's rule 3, retained for presentation only
    (F006 AC9; ``research/00`` §5.4 (iii): "the dataset the athlete was
    read on last"; formalised by T156, keeping the clause order T155 built
    provisionally). No verdict is conferred by it and no dissenter is named
    against it: ``judge`` on the presented dataset answers with that
    dataset's own first-firing guard (``week_too_thin`` on an established
    dataset with a thin week, ``baseline_unestablished`` or ``no_band`` on a
    thin one), and ``disagreed_with`` is empty (``Selection``).

    The three clauses, asked in order, each the shape F005's resolver gave
    it (``resolve_baseline_tier`` rule 3 and ``_densest_tier``):

    1. **Among established datasets, the one read last in the baseline
       window** (``last_read``, AC6's normative slice), ties by ``n`` then
       fidelity -- T094's direction: the dataset the athlete is actually
       on, which is what "keeps the tier stable" always meant; densest
       instead handed a switched athlete's thin week back to the device he
       abandoned. This clause is what holds the T138 tier-change silence
       at its measured 18 days (the outgoing, established dataset keeps
       the presentation and says ``week_too_thin``), and what keeps the
       SW+20 stray pin in ``test_hrv_trend_reset.py`` on the snapshot.
    2. **With none established, the densest by ``n``**, ties to fidelity:
       an athlete with 45 snapshot readings who borrows a strap once keeps
       the snapshot presentation, and the strap capture is corroboration.
    3. **With no baseline reading of any tier, the densest in the judged
       week**, ties to fidelity -- ``build_series``'s old empty-baseline
       fallback, which applied rule 3 to the week standing in for both.

    ``None`` exactly when the series holds no dataset, which is the
    structural ``no_tier_sustains_a_trend`` cause (``test_hrv_unavailable_
    causes.py`` reads this annotation for it since T156). A dataset with one
    baseline reading is presented by clause 2 and reports ``no_band`` on a
    real tier; a week-only series is presented by clause 3 and reports the
    same -- neither is the structural case.

    **The cross-dataset ``unavailable_reason`` precedence** (AC9, T156)
    is this clause order composed with ``judge``'s guard order: which
    dataset speaks is decided here (after ``select_dataset``), and which
    of its causes is named is decided by ``judge`` on that dataset alone.
    See ``_unavailable_reason``.
    """
    if not series.datasets:
        return None
    established = [d for d in series.datasets if d.established]
    if established:
        chosen = max(established, key=lambda d: (last_read[d.tier], d.n, -_FIDELITY_RANK[d.tier]))
        return Presentation(chosen, FALLBACK_ESTABLISHED_READ_LAST)
    if any(d.n for d in series.datasets):
        chosen = max(series.datasets, key=lambda d: (d.n, -_FIDELITY_RANK[d.tier]))
        return Presentation(chosen, FALLBACK_DENSEST_BASELINE)
    chosen = max(series.datasets, key=lambda d: (len(_days(d.window)), -_FIDELITY_RANK[d.tier]))
    return Presentation(chosen, FALLBACK_DENSEST_WEEK)


def selected_view(series: HrvSeries) -> SingleDatasetView:
    """The one dataset ``judge`` and the route are handed, on the F005
    series shape: the dataset ``select_dataset`` selects, or the
    presentation fallback when nothing is judgeable, or an empty view with
    ``tier`` ``None`` when the series holds no dataset at all (the
    structural ``no_tier_sustains_a_trend`` cause). Replaces T151's bridge
    over the retired resolver (T155); retires with T159.

    ``excluded`` is the series' own list (AC15, T152). A non-presented
    dataset's readings are in that dataset, carried in ``datasets``, and
    are not re-listed here: the partition of the span's rows into "in some
    dataset" and "excluded, with a reason" is made once by ``build_series``
    and the view presents it as it is.

    **This is where the ``unavailable_reason`` precedence across datasets
    is decided** (F006 AC9, T156). With N datasets, different datasets
    satisfy different causes at once; the reason the response carries is
    the first-firing guard of **the dataset this view presents** -- the
    selected one, else the fallback's, else none -- and never a cause
    satisfied by a dataset the response does not carry the baseline of.
    ``presented_by`` names which of those it was.
    """
    selection = select_dataset(series)
    presented = selection.selected
    presented_by: str | None = PRESENTED_SELECTED
    if presented is None:
        fallback = _presentation_fallback(series, selection.last_read)
        presented = None if fallback is None else fallback.dataset
        presented_by = None if fallback is None else fallback.clause
    excluded = series.excluded

    if presented is None:
        return SingleDatasetView(
            target_date=series.target_date,
            timezone=series.timezone,
            baseline_window=series.baseline_window,
            judged_window=series.judged_window,
            tier=None,
            readings=series.readings,
            series=(),
            baseline=(),
            window=(),
            excluded=excluded,
            reset_on=series.gap_reset_on,
            reset_reason=REASON_COVERAGE_GAP if series.gap_reset_on is not None else None,
            withheld=False,
            selected=None,
            selection=selection,
            datasets=series.datasets,
            presented_by=None,
        )
    return SingleDatasetView(
        target_date=series.target_date,
        timezone=series.timezone,
        baseline_window=presented.baseline_window,
        judged_window=series.judged_window,
        tier=presented.tier,
        readings=series.readings,
        series=presented.series,
        baseline=presented.baseline,
        window=presented.window,
        excluded=excluded,
        reset_on=presented.reset_on,
        reset_reason=presented.reset_reason,
        withheld=presented.withheld,
        selected=presented,
        selection=selection,
        datasets=series.datasets,
        presented_by=presented_by,
    )


# ---------------------------------------------------------------------------
# The SWC band, the thin-data guards and the verdict (T084)
#
# Everything in this section reads **one dataset** -- an ``HrvDataset``, or
# the route's ``SingleDatasetView`` of the selected one (T155) -- and nothing
# else, so it composes with T092's reset clipping of ``baseline`` without
# knowing about it, and never sees the other datasets of the series.
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

#: Why ``hrv_unavailable`` was emitted (T137, review cycle 9, closing F005's
#: Negative Class row "the verdict still cannot say *why* it is unavailable",
#: open since cycle 4). Six causes in all -- T128's AST oracle
#: (``test_hrv_unavailable_causes.py``) derives the same six from ``judge``,
#: ``main.py`` and ``resolve_baseline_tier`` independently of this module, and
#: is the authority if the two ever disagree. The first four are ``judge``'s
#: own guards, evaluated in the fixed order stated in its docstring; the last
#: two exist outside the pure rule -- one at the route
#: (``main._withhold_future``), one structural (``resolve_baseline_tier``
#: answering "no tier at all"). ``HrvVerdict.unavailable_reason`` is exactly
#: one of these, or ``None`` whenever ``verdict`` is not ``hrv_unavailable``.
REASON_NO_TIER = "no_tier_sustains_a_trend"
REASON_NO_BAND = "no_band"
REASON_WEEK_TOO_THIN = "week_too_thin"
REASON_WEEK_NOT_REPRESENTATIVE = "week_not_representative"
REASON_BASELINE_UNESTABLISHED = "baseline_unestablished"
#: Decided at the route, not here (``main._withhold_future``); listed so the
#: full six-member set lives in one place.
REASON_DAY_NOT_HAPPENED = "day_not_happened"

#: The six, in the precedence ``judge`` applies plus the route's own (T137).
UNAVAILABLE_REASONS: tuple[str, ...] = (
    REASON_NO_TIER,
    REASON_NO_BAND,
    REASON_WEEK_TOO_THIN,
    REASON_WEEK_NOT_REPRESENTATIVE,
    REASON_BASELINE_UNESTABLISHED,
    REASON_DAY_NOT_HAPPENED,
)


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
    ``unavailable_reason`` (T137) is the first of ``judge``'s own guards to
    fire, or ``None`` whenever ``verdict`` is not ``hrv_unavailable``; the
    route may override it to ``REASON_DAY_NOT_HAPPENED`` (``_withhold_future``)
    regardless of what this function decided, since a day that has not
    happened is the reason no verdict is asserted about it whatever its
    computed fields would otherwise have said.
    """

    verdict: str
    ln_rmssd_7d_mean: float | None
    below_by: float | None
    band: Band | None
    baseline_n: int
    established: bool
    readings_in_window: int
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        """Refuse a verdict whose two published fields disagree (T144).

        ``HrvSeries.withheld`` (T134) closes the same hole by **defaulting**
        toward ``hrv_unavailable``, and the note there asks any field on this
        path to do likewise. This one cannot, and the difference is in the
        type rather than in the reasoning: ``withheld`` is a ``bool`` with a
        safe conservative value -- ``True`` says *nothing*, the direction
        ``research/00`` Section 1.7 tolerates freely. ``unavailable_reason``
        has no such value. Each of its six members names a **specific** cause,
        so a sentinel default would have to assert one, and publishing a cause
        that did not fire is not silence on weak evidence, it is a second
        false claim in the field that exists to stop the first; while a
        sentinel *outside* the six is unpublishable (``schemas.py`` and
        ``contracts/openapi.yaml`` both refuse a seventh value) and so would
        need catching here anyway.

        So the invariant itself is made structural, in the same direction and
        in both: the biconditional those two documents already state as
        ``null whenever it is not``. That closes what a default of any value
        could not -- ``_withhold_future`` and any future ``replace`` can flip
        ``verdict`` while leaving a stale reason behind, and a defaulted field
        is not consulted on a ``replace``.

        Unreached by shipped code and moves no verdict: ``judge`` computes the
        reason from exactly the guards that left ``verdict`` at
        ``VERDICT_UNAVAILABLE`` (``_unavailable_reason`` returns ``None`` on
        precisely the condition under which ``judge`` asserts a verdict), and
        ``main._withhold_future`` sets both fields together. ``ValueError``
        rather than ``assert`` so that ``python -O`` does not strip it.
        """
        withheld = self.verdict == VERDICT_UNAVAILABLE
        if withheld != (self.unavailable_reason is not None):
            raise ValueError(
                f"verdict={self.verdict!r} and unavailable_reason="
                f"{self.unavailable_reason!r} disagree: unavailable_reason is "
                f"set exactly when verdict is {VERDICT_UNAVAILABLE!r}"
            )


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


def _unavailable_reason(
    series: HrvDataset | SingleDatasetView,
    band: Band | None,
    window_mean: float | None,
    readings_in_window: int,
    established: bool,
) -> str | None:
    """Which of ``judge``'s own four guards is the first to fire, in the
    fixed order ``judge`` evaluates them (its docstring's order): no band,
    then a week too thin to mean anything, then a week withheld as
    unrepresentative (T125/T132), then an unestablished baseline (T116).
    ``None`` once none of them fire -- the verdict is asserted, not withheld.

    A separate function, not a rewrite of ``judge``'s own ``if``, **on
    purpose**: T128's oracle (``test_hrv_unavailable_causes.py``) parses
    ``judge``'s source for the guards that leave the verdict at
    ``hrv_unavailable``, and this function's own conditionals must not be
    mistaken for a second, competing set of them. It is asked with exactly
    the values ``judge`` itself computed, so the two can never disagree about
    *which* guard fired -- only this function additionally names it.

    The ``no band`` guard is one cause in ``judge`` and two in the enum: T128
    names the **structural** case -- no dataset at all, the empty view
    ``selected_view`` builds when the series holds none (F005: ``resolve_
    baseline_tier`` answering "no tier at all") -- separately from a
    selected dataset whose baseline is merely too thin, because
    ``series.tier is None`` implies ``band is None`` (the empty view has an
    empty ``series.baseline``) but not the converse, and the two are told
    apart here rather than folding the structural case silently into
    ``no_band``.

    **The precedence across datasets** (F006 AC9; ``research/00`` §5.4
    (iii); T156). This function reads one dataset, and with N datasets the
    question "which dataset's cause is named?" is answered *before* it is
    asked, by ``selected_view``: (1) which dataset speaks -- the selected
    dataset (``select_dataset``), else the presentation fallback
    (``_presentation_fallback``: established read last, ties ``n`` then
    fidelity; else densest by ``n``; else densest in the week), else no
    dataset; (2) that dataset's own guard order, above. Two consequences
    are the whole point. The reason is always true of the dataset whose
    ``baseline``/``band``/``established`` the response carries -- an
    illness week on an established strap beside an unestablished snapshot
    that covered the week says ``week_too_thin`` on ``n`` 60, not the
    snapshot's ``baseline_unestablished`` on a baseline the reader cannot
    see. And ``no_tier_sustains_a_trend`` fires only when the series holds
    no dataset at all: a null *selection* on an ordinary illness or holiday
    week presents the dataset the athlete used last and names its cause.
    Pinned in ``test_hrv_unavailable_reason.py`` (T156 section).
    """
    if band is None:
        return REASON_NO_TIER if series.tier is None else REASON_NO_BAND
    if window_mean is None or readings_in_window < MIN_WINDOW_READINGS:
        return REASON_WEEK_TOO_THIN
    if series.withheld:
        return REASON_WEEK_NOT_REPRESENTATIVE
    if not established:
        return REASON_BASELINE_UNESTABLISHED
    return None


def judge(series: HrvDataset | SingleDatasetView) -> HrvVerdict:
    """The verdict for one dataset from its two disjoint slices -- the
    dataset ``select_dataset`` selected, as the route hands it (T155), or
    any ``HrvDataset`` on its own. The parameter keeps its F005 name
    because ``test_hrv_unavailable_causes.py`` reads this function's guards
    out of the source by name.

    The band is built over ``series.baseline`` (``[D-66, D-7]``) and the
    week's mean over ``series.window`` (``[D-6, D]``); because the slices do
    not overlap, a suppressed week cannot lower its own band and self-clear
    (§3.7.4: a single good morning does not clear an accumulated
    suppression). In order:

    - no band (fewer than two baseline readings) -> ``hrv_unavailable``;
    - fewer than ``MIN_WINDOW_READINGS`` in the week -> ``hrv_unavailable``
      (two bad mornings are not a trend, however bad);
    - a week that is not a fair sample of ``series.tier`` -- ``series.withheld``
      (T125) -> ``hrv_unavailable``, however many readings it holds;
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
    after a **coverage-gap** reset, which collapses the baseline
    deliberately: the athlete then traverses 20 unestablished days --
    ``R+0 .. R+19``, pinned by
    ``test_the_establishment_delay_after_a_reset_is_twenty_days``. Twelve of
    them changed verdict at T116:
    ``R+8 .. R+19``, previously all of them reading ``hrv_normal``
    unless the week fell below the band. The first eight already read
    ``hrv_unavailable``, because a band needs two readings (T126).

    **It is not reachable after a tier change, and this docstring said it
    was until 2026-09-19** (T163, correcting what [[T138]] measured false
    and could not touch: T138 carried ``behaviour_change: false``, whose
    scope rule forbade any edit under ``runcoach-api/src/``, so the claim
    was corrected at all three *document* sites and survived here). A
    clean, gapless, permanent source-tier change collapses **nothing**:
    the outgoing tier keeps its full 60-day baseline, ``established``
    stays **true** throughout and ``n`` merely decays 60 -> 47, so the
    switch traverses **zero** unestablished days and never reaches this
    gate at all. Its own quiet is **18** days, ``R+2 .. R+19``
    (``MIN_BASELINE_READINGS + WINDOW_DAYS - MIN_WINDOW_READINGS``), and
    it is a different mechanism entirely -- week coverage on the tier the
    athlete has stopped using, which is the ``week_too_thin`` guard above,
    with ``reset_reason`` null on every one of the 18.

    **The pin cited above cannot speak for the tier change.**
    ``test_the_establishment_delay_after_a_reset_is_twenty_days`` is a
    coverage-gap walk -- it asserts ``reset_reason == ["coverage_gap"] *
    21`` across its own geometry -- and is structurally incapable of
    producing the tier-change shape, which is why one figure stood for two
    mechanisms for as long as it did. The tier change is pinned
    separately, on a walked clean switch, by
    ``test_the_tier_change_silence_is_eighteen_days_and_names_no_reset_on_any_of_them``
    in ``test_hrv_trend_reset.py``, and both figures are stated beside
    each other in ``research/00`` 5.4 and spec 3.7.3 (pinned by
    ``test_the_tier_change_silence_is_stated_beside_the_coverage_gap_figure``).

    Pinned by ``test_a_thin_baseline_inside_the_band_is_unavailable_not_normal``,
    ``test_a_thin_baseline_above_the_band_is_unavailable_too`` and
    ``test_the_establishment_gate_flips_normal_at_exactly_fourteen_readings``
    in ``test_hrv_trend_band.py``, and by the contract table there.

    **The withhold is the same argument as the establishment gate, one window
    over** (T125, 2026-09-16; ``research/00`` §5.4, spec §3.7.3/§3.7.4). T116
    withheld both verdicts when the *baseline* is too thin to support either.
    T125 withholds both when the *week* is not the athlete's: rule 1's recency
    gate struck the tier he is currently recording on, so the mean is computed
    from the surviving tier's last few days before he came back, and its
    ``hrv_normal`` direction is §1.7's forbidden one -- readiness is intact, on
    a week the athlete did not live. The condition is computed in
    ``build_series`` (``verdict_withheld``, at dataset scope since T158: the
    set asked is every dataset that could not have been selected), not here,
    because it is a statement about the order of two datasets' judged-week
    days and this function sees one dataset's. The measured cost, priced in
    F005's Negative Class: 72 of
    2050 swept return geometries move ``hrv_normal -> hrv_unavailable``, which
    is the athlete's third and fourth mornings back on top of the fifth to
    seventh, already silent -- five of his first seven. No verdict moves in the
    other direction, and the tier, band and window are unchanged on every row.

    The band itself is asserted whenever it can be built, established or
    not, and whether or not the week has readings: it is a property of the
    baseline, and the contract's ``points[]`` draws it on days with no
    reading (T091). That is true of a withheld week too -- the band is the
    baseline's, and the baseline is not what is in doubt.

    **``unavailable_reason`` (T137).** The four guards above are evaluated in
    this fixed order, and the response now carries which one fired --
    ``_unavailable_reason`` reads exactly the same four values this function
    computed, so it can never disagree with what actually happened here. Two
    more causes exist outside this pure function: the structural one -- no
    dataset at all, ``selected_view``'s empty view (F005: ``resolve_
    baseline_tier`` answering "no tier at all") -- is folded into the ``no
    band`` guard's report (``series.tier is None`` implies ``band is None``,
    so the two share a guard here and are told apart by name only),
    and the day-not-happened one is the route's (``main._withhold_future``),
    which overrides whatever this function decided.

    **Across datasets (F006, T156)** the guard order above is the second
    half of the precedence, applied to the one dataset ``selected_view``
    presents; the first half -- which dataset that is -- is decided there,
    and the verdict promoted is this function's answer on the selected
    dataset **unchanged** (AC11): nothing another dataset reads, in either
    direction, enters here. When nothing is selected the presented dataset
    is the AC9 fallback, this function still answers on it alone, and no
    verdict is conferred because a dataset that is not judgeable cannot
    pass the guards.
    """
    band = build_band(ln_rmssd(reading) for reading in series.baseline)
    baseline_n = len(series.baseline)
    established = baseline_n >= MIN_BASELINE_READINGS
    readings_in_window = len(series.window)
    window_mean = statistics.fmean(ln_rmssd(r) for r in series.window) if readings_in_window else None

    verdict = VERDICT_UNAVAILABLE
    below_by = None
    if (
        band is not None
        and window_mean is not None
        and readings_in_window >= MIN_WINDOW_READINGS
        and not series.withheld
    ):
        if established:
            if window_mean < band.lo:
                verdict = VERDICT_SUPPRESSED
                below_by = band.lo - window_mean
            else:
                verdict = VERDICT_NORMAL

    reason = _unavailable_reason(series, band, window_mean, readings_in_window, established)

    return HrvVerdict(
        verdict=verdict,
        ln_rmssd_7d_mean=window_mean,
        below_by=below_by,
        band=band,
        baseline_n=baseline_n,
        established=established,
        readings_in_window=readings_in_window,
        unavailable_reason=reason,
    )


# ---------------------------------------------------------------------------
# Baseline re-establishment: the coverage gap and the sustained tier change
# (T092)
#
# ``build_series`` calls into this section at two points: the gap rule before
# the partition (it clips the baseline window of every dataset alike), the
# tier-change rule once per dataset after its collapse. Both read readings, never
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


def _internal_hole_resumption(days: Sequence[date]) -> date | None:
    """The day one dataset's baseline resumed on after the **last** internal
    capture hole of more than ``GAP_RESET_DAYS`` silent local days, or
    ``None`` when its readings hold no such hole (F006 AC17, T153).

    ``days`` are the sorted distinct local days of **one dataset's** readings
    inside its own (gap- and era-clipped) baseline window -- never the
    judged week's, and never another tier's. A hole is counted exactly as
    ``coverage_gap_reset`` counts a gap: ``_silence_between`` (the days
    strictly between two readings) **greater than** ``GAP_RESET_DAYS``, so
    22 silent days clip and 21 do not -- one constant, one meaning, and the
    per-dataset clip can never call a break the global rule would not. The
    scan runs backwards from the latest day, as the gap's does, so of two
    holes the later resumption wins (the younger era, the cautious reading).

    This is the loop inside ``coverage_gap_reset`` restated over one
    dataset, deliberately not shared with it: the global rule also reads
    the readings *before* the window and the store's earliest reading, and
    stays global (AC16); this one reads nothing outside the dataset. A
    leading silence -- the window's first day to the dataset's first
    reading -- is not a hole: nothing precedes it that the band could mix.
    ``build_series`` moves ``baseline_window[0]`` to the day returned and
    reports nothing for it (no ``reset_on``, no ``reset_reason``).
    """
    for i in range(len(days) - 1, 0, -1):
        if _silence_between(days[i - 1], days[i]) > GAP_RESET_DAYS:
            return days[i]
    return None


def _exclude_before_reset(
    readings: list[Reading], excluded: list[Exclusion], reset_on: date, reason: str
) -> tuple[list[Reading], list[Exclusion]]:
    """Move every reading dated before ``reset_on`` out of the series into
    the exclusions, named ``before_reset: <reason>``, so what is kept and
    what is excluded stay disjoint and exhaustive over the rows in
    ``[D-66, D]``.

    Both resets call it (T098), and since T107 both can call it on one
    request: the gap rule on the readings of every tier, before the
    partition, and each dataset's era-boundary clip on that dataset's
    collapsed one-per-day series, after it -- another tier's rows are in
    another dataset (T151) and a re-taken morning is already listed
    ``same_day_later_capture``, so neither is listed twice. The two
    populations cannot overlap either,
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
    """Where the new tier's era begins (``tier_change_reset``'s ``tier``,
    each dataset's own since F006), and whether rule 4 reports it.

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

    #: ``B_start``'s local day: the new tier's first reading after the
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
    * that boundary's ``B_start`` day is where ``new``'s era
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
    tier: str,
    baseline_readings: Iterable[Reading],
    week_readings: Iterable[Reading],
    judged: tuple[date, date],
    stray_population: Iterable[Reading] | None = None,
) -> EraBoundary | None:
    """The era boundary between the previous window's tier and ``tier`` --
    the local day a fresh baseline of ``tier`` begins on, and whether it is
    *reported* -- or ``None`` when there is none.

    **Asked once per dataset, with that dataset's tier** (F006 AC17, T154;
    T151 placed the call). Under F005 ``build_series`` resolved one tier and
    asked this question about it, so ``tier`` was ``str | None`` -- ``None``
    when the resolver found no tier at all. Under F006 there is no resolved
    tier: every tier present has a dataset, and ``build_series``'s one call
    site, inside its per-dataset loop, hands each dataset's own tier here
    (``test_hrv_tier_change_per_dataset.py`` pins the call site, and the
    answer three-valued against a mutant that hands the selected tier to
    every dataset). The clauses below are unchanged; what each one now means
    is "this dataset" where it used to mean "the resolved tier". For the
    dataset that *is* ``previous_tier``, (b) short-circuits and nothing is
    clipped or reported -- AC6, not this rule, defends the reference §9
    series. The reported reset is therefore **per dataset**: the route
    presents the *selected* dataset's own (``selected_view``), and a
    non-selected dataset's report is carried in ``datasets``, never
    promoted. Every dataset is asked over the **same** cross-tier
    populations -- ``previous_readings``, the gap-clipped
    ``baseline_readings`` for clause (a), and one ``stray_population`` of
    every tier's unclipped readings for clause (c) -- so the question is per
    dataset and the facts it is asked against are not.

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

    (a) this dataset's tier ``tier`` **sustains** the current baseline
        window -- read on at least ``MIN_BASELINE_READINGS`` distinct local
        days in ``baseline_readings`` (all tiers, already clipped by any
        coverage gap), rule 1's candidacy;
    (b) it differs from the tier the previous window ``[D-126, D-67]``
        sustains (``sustained_tier`` on ``previous_readings``: highest
        fidelity with at least ``MIN_BASELINE_READINGS`` days, rule 1
        alone);
    (c) the two eras **do not interleave**, judged over both windows and
        the judged week together (``previous_readings``,
        ``baseline_readings`` and ``week_readings``, the last so that the
        tolerance's week half has readings to count; sprint-005 review
        cycle 3, M1 -- T094 judged it on the current window alone): with
        ``A`` the previous tier and ``B`` this dataset's ``tier``, there is an
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
        new tier's first is simultaneous, not a stray, so the tie is
        interleaved and reports nothing. Judged on the current window
        alone, (c) was vacuously true for a trial that had aged wholly
        into the previous window and a phantom ``tier_change`` was
        reported for an athlete who never switched (the review's series
        B); judged exactly, one capture of either tier on either side of
        a genuine switch made the eras interleave and silenced the reset
        for the whole era (review cycle 3, G9, G12, IDEA-065).

    The reset lands on the era's **true first day**: ``tier``'s
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
    earlier -- a ``tier`` that never reaches or falls back below
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

    *Clause (a)* took, under F005, the tier ``build_series`` had already
    resolved, so the recency gate had been applied before this function was
    called and a tier struck for staleness never arrived here as ``tier``.
    Under F006 (T154) the gate is ``select_dataset``'s and runs *after*
    this: every dataset is asked, stale or not, and its answer is its own
    report; the gate then decides only whose report the route presents. A
    skipped dataset's report is still the true account of the band it
    clipped, so restating the gate in (a) would be a second copy of one rule
    that also blanked a report ``datasets`` is meant to carry.

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

    **What that clip does to clause (c): nothing, since T129** (2026-09-16,
    ``research/00`` §5.4, resolving G-C7-3 **by change**). The clip decides
    which readings enter the **band**; it does not decide which readings
    ``_era_boundary`` may **see** when it counts strays. So this rule takes
    a ``stray_population`` -- every reading of every tier inside
    ``[D-66, D]``, gap-clipped or not, which ``build_series`` keeps before
    the gap branch rebinds ``readings`` -- and clause (c) is asked over it
    together with ``previous_readings``. Clause (a) is **unchanged** and
    still reads the gap-clipped ``baseline_readings``, because (a) asks
    whether the resumption era sustains a baseline of its own.

    *What the change removed, kept here because the pins point at it.*
    Until T129 one clipped population answered both, so the readings of
    ``[D-66, gap_reset_on)`` were absent from the stray count too. New-tier
    readings hidden there would have been strays of every *late* ``A_end``
    -- they lie between the old era's first day and that boundary's
    ``B_start`` -- so hiding them shrank the stray term for late boundaries
    and could admit, or promote over an earlier candidate, a boundary the
    full capture history refuses or dates earlier. That ``first_day`` could
    then fall **after** the resumption, and the composed clip removed
    post-resumption readings of the baseline tier from the band as
    ``before_reset: tier_change``. T107 created that reachability (before
    it the gap cancelled this branch outright); T118 accepted and named it
    on 2026-09-15 on a **direction** -- 0 flips to ``hrv_normal`` in 40,000
    trials -- and T123 falsified that direction on 2026-09-16 against the
    post-T116 rule: of 26,360 well-formed randomized histories, 9,230 moved
    the boundary, 4,466 of those held ``baseline_n >= 14``, 702 were
    flip-reachable and **5 flipped** ``hrv_suppressed`` to ``hrv_normal``
    on an identical week mean with the baseline established on both sides
    -- up-regulation on weak evidence, which ``research/00`` §1.7 forbids.
    At the fix the flip class is **0 of 26,360**. Pinned at both levels by
    ``test_the_gap_clip_moves_the_era_boundary_later_than_the_full_history_finds``
    (one history, the boundary dated 2026-07-03 on the full population and
    2026-08-14 on the gap-clipped one -- the *population dependence* itself,
    which is still true of this rule and is what the caller now controls)
    and, end to end, by
    ``test_the_unclipped_stray_count_refuses_the_gap_created_era_boundary``
    and ``test_the_gap_created_era_boundary_keeps_on_tier_days_at_the_resumption``.
    """
    baseline_readings = tuple(baseline_readings)
    previous_readings = tuple(previous_readings)
    if _tier_counts(baseline_readings).get(tier, 0) < MIN_BASELINE_READINGS:
        return None
    previous_tier = sustained_tier(_tier_counts(previous_readings))
    if previous_tier is None or previous_tier == tier:
        return None
    # Clause (c)'s population is the **unclipped** one (T129): the strays are
    # counted over every reading the capture history holds in
    # ``[D-66, D]``, gap-clipped or not, plus the previous window. Callers
    # that hand no ``stray_population`` are asking the rule about exactly the
    # readings they passed, which is what the population-dependence pin does.
    inside = tuple(stray_population) if stray_population is not None else (*baseline_readings, *week_readings)
    everything = (*previous_readings, *inside)
    return _era_boundary(_of_tier(everything, previous_tier), _of_tier(everything, tier), judged)
