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
2. **Resolve the baseline tier**: the highest-fidelity tier with at least
   ``MIN_BASELINE_READINGS`` readings in the baseline window, else the tier
   with the **most** readings there -- not the highest tier present, so one
   borrowed chest-strap capture cannot demote a 45-reading Health Snapshot
   baseline to ``n=1`` (§3.7.3: "never merged into the same band").
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
#: The baseline tier is the highest-fidelity tier carrying at least this many
#: readings in the baseline window. T084 also reads it as "established".
MIN_BASELINE_READINGS = 14
#: A silence of **more than** this many consecutive local days with no entry
#: in the post-exclusion series re-establishes the baseline (T092;
#: construction reference "Constants": survives a taper, a holiday or a
#: two-week illness; catches an era break). 21 does not reset; 22 does.
GAP_RESET_DAYS = 21

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
#: A reading inside ``[D-66, D]`` that predates a reset (T092): it
#: contributed to neither the baseline nor the window, and ``research/00``
#: §1.6 wants it listed rather than silently dropped. Parameterised with the
#: reset reason: ``before_reset: coverage_gap``.
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
    #: T092. The local day the current baseline era began, when a reset was
    #: detected, and why: ``REASON_COVERAGE_GAP`` or ``REASON_TIER_CHANGE``.
    #: ``baseline_window`` is then clipped to ``[reset_on, D-7]``. Both
    #: ``None`` when nothing reset. Defaulted so a series can be built
    #: without naming them.
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


def _is_usable_value(value: Any) -> bool:
    """``ln`` is defined on exactly the strictly positive finite reals.
    ``inf`` and ``nan`` both clear a ``<= 0`` test (IDEA-034), so the
    finiteness check is explicit."""
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _is_reading(row: Mapping[str, Any]) -> bool:
    """``build_series``'s exclusion chain as one predicate, for a row whose
    listed reason is ``outside_windows`` but whose reading the previous
    window (T092) still needs. Equivalent to the chain: a pre-amendment row
    has a null value, which ``_is_usable_value`` rejects; a null or unknown
    tier is not in the fidelity order."""
    return row["hrv_source_tier"] in _FIDELITY_RANK and _is_usable_value(row["resting_rmssd_ms"])


def _tier_counts(readings: Iterable[Reading]) -> Counter[str]:
    """Readings per tier -- the input ``resolve_baseline_tier`` takes."""
    return Counter(r.tier for r in readings)


def resolve_baseline_tier(counts: Mapping[str, int]) -> str | None:
    """The baseline tier from per-tier reading counts over one window.

    The highest-fidelity tier with at least ``MIN_BASELINE_READINGS``; failing
    that, the tier with the most readings, ties to the higher fidelity.
    ``None`` when there are no readings at all.

    "Most readings", not "highest present": an athlete with 45 Health Snapshot
    readings who borrows a chest strap once keeps the established snapshot
    baseline, and the strap capture is corroboration (§3.7.3).
    """
    for tier in TIER_FIDELITY:
        if counts.get(tier, 0) >= MIN_BASELINE_READINGS:
            return tier
    present = [tier for tier in TIER_FIDELITY if counts.get(tier, 0) > 0]
    if not present:
        return None
    return max(present, key=lambda tier: (counts[tier], -_FIDELITY_RANK[tier]))


def build_series(rows: Iterable[Mapping[str, Any]], zone: ZoneInfo, target_date: date) -> HrvSeries:
    """Exclude, resolve the tier, filter, collapse -- in that order.

    ``rows`` are ``sessions`` rows (or dicts) carrying ``session_id``,
    ``start_time``, ``resting_rmssd_ms`` and ``hrv_source_tier``; any other
    key is ignored. Rows may span more than ``[D-66, D]`` -- the route reads
    a padded UTC range once and T091 calls this per local day -- and the ones
    outside are excluded as ``outside_windows`` rather than pre-filtered, so
    the response can still list them.

    **Tier fallback when the baseline window is empty.** The tier rule is
    stated over the baseline window; when that window holds no reading of
    any tier (a new athlete's first week, a request before any history) the
    same rule is applied to the judged window instead, so a day with a
    reading still carries it in the series. No band can be built from an
    empty baseline, so the verdict for such a day is T084's ``unavailable``
    whichever tier is chosen; the fallback only decides which reading the
    contract's ``points[].ln_rmssd`` shows.

    **Resets (T092)** are detected between the exclusion step and the tier
    step, because a coverage gap clips the baseline window *before* the
    tier is resolved on it -- the fresh baseline is begun from the
    resumption on whatever tier sustains it there. The sustained-tier-change
    rule runs after the series is built and compares the resolved tier with
    the one that sustained the previous window ``[D-126, D-67]``, so
    ``rows`` must span ``[D-126, D]`` for it to be able to fire; a narrower
    read leaves the previous window empty, which reads as "thin" and never
    as a change. See ``coverage_gap_reset`` and ``tier_change_reset``.
    """
    baseline = baseline_window(target_date)
    judged = judged_window(target_date)
    baseline_first = baseline[0]
    previous = previous_window(target_date)

    readings: list[Reading] = []
    previous_readings: list[Reading] = []
    excluded: list[Exclusion] = []
    for row in rows:
        session_id = row["session_id"]
        day, instant = local_day(session_id, row["start_time"], zone)
        tier = row["hrv_source_tier"]
        value = row["resting_rmssd_ms"]

        if not baseline_first <= day <= target_date:
            excluded.append(Exclusion(day, session_id, REASON_OUTSIDE_WINDOWS))
            if previous[0] <= day <= previous[1] and _is_reading(row):
                previous_readings.append(Reading(day, session_id, tier, float(value), instant))
        elif is_pre_amendment_window(row):
            excluded.append(Exclusion(day, session_id, REASON_PRE_AMENDMENT_WINDOW))
        elif tier is None:
            excluded.append(Exclusion(day, session_id, REASON_NULL_TIER))
        elif tier not in _FIDELITY_RANK:
            excluded.append(Exclusion(day, session_id, f"{REASON_UNKNOWN_TIER}: {tier}"))
        elif not _is_usable_value(value):
            excluded.append(Exclusion(day, session_id, f"{REASON_UNUSABLE_VALUE}: {value!r}"))
        else:
            readings.append(Reading(day, session_id, tier, float(value), instant))

    # Deterministic before anything is chosen by position: by instant, then
    # by id for two devices sharing an instant.
    readings.sort(key=lambda r: (r.start_time, r.session_id))

    reset_on = coverage_gap_reset(readings, previous_readings)
    reset_reason = REASON_COVERAGE_GAP if reset_on is not None else None
    if reset_on is not None:
        baseline = (reset_on, baseline[1])
        readings, excluded = _exclude_before_reset(readings, excluded, reset_on, REASON_COVERAGE_GAP)

    tier = resolve_baseline_tier(_tier_counts(_within(readings, baseline)))
    if tier is None:
        tier = resolve_baseline_tier(_tier_counts(_within(readings, judged)))

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
    excluded.sort(key=lambda e: (e.date, e.session_id))

    if reset_on is None:
        reset_on = tier_change_reset(previous_readings, tier, _within(readings, baseline))
        if reset_on is not None:
            reset_reason = REASON_TIER_CHANGE
            baseline = (reset_on, baseline[1])

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
    - the mean strictly below ``band.lo`` on an **established** baseline
      (``>= MIN_BASELINE_READINGS``) -> ``hrv_suppressed``, with ``below_by``;
    - the mean inside **or above** the band -> ``hrv_normal``;
    - the mean below the band on a baseline that is *not* established ->
      ``hrv_unavailable``. §3.7.3 says the suppression is *withheld* until
      the baseline is adequately established; it does not say the week reads
      normal. ``hrv_normal`` would tell Section 6 that readiness is intact on
      the strength of the very reading that says otherwise -- up-regulating
      on weak evidence, which ``research/00`` §1.7 forbids -- so the honest
      verdict is that there is none, and the response carries ``baseline_n``
      and ``established`` to say why.

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
        if window_mean < band.lo:
            if established:
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


def coverage_gap_reset(readings: Iterable[Reading], previous_readings: Iterable[Reading]) -> date | None:
    """The local day the baseline is re-established on after a coverage gap,
    or ``None``.

    Scans the distinct local days of ``readings`` (the post-exclusion series
    of **every** tier, inside ``[D-66, D]``) backwards from the latest and
    returns the first day that follows a silence of more than
    ``GAP_RESET_DAYS`` local days -- the resumption. Measured on ``date``
    arithmetic, never on UTC deltas: a silence straddling a DST change is
    still the same number of local days.

    A gap is bounded by a reading on both sides. Two consequences the spec
    text does not state and this function decides:

    * **An open gap** (the last reading is more than 21 days old and nothing
      has resumed) is not yet a reset: the reset lands on the first reading
      *after* a gap, and there is none. The judged week is empty, so the
      verdict is ``unavailable`` regardless.
    * **The leading stretch** of ``[D-66, D]`` before the first reading is a
      gap only when measured from the last reading before the window --
      ``previous_readings``, the post-exclusion readings of ``[D-126,
      D-67]`` -- and that silence exceeds 21 days. With no known earlier
      reading it is the start of history, not a break between two eras:
      nothing precedes it that could contribute across it, and reporting a
      new athlete's first capture as a ``coverage_gap`` would name an event
      that did not happen.
    """
    days = sorted({r.date for r in readings})
    if not days:
        return None
    for i in range(len(days) - 1, 0, -1):
        if _silence_between(days[i - 1], days[i]) > GAP_RESET_DAYS:
            return days[i]
    last_before = max((r.date for r in previous_readings), default=None)
    if last_before is not None and _silence_between(last_before, days[0]) > GAP_RESET_DAYS:
        return days[0]
    return None


def _silence_between(earlier: date, later: date) -> int:
    """The number of whole local days strictly between two reading days."""
    return (later - earlier).days - 1


def _exclude_before_reset(
    readings: list[Reading], excluded: list[Exclusion], reset_on: date, reason: str
) -> tuple[list[Reading], list[Exclusion]]:
    """Move every reading dated before ``reset_on`` from the series into the
    exclusions, named ``before_reset: <reason>``, so ``readings`` and
    ``excluded`` stay disjoint and exhaustive over the rows in ``[D-66, D]``."""
    kept = [r for r in readings if r.date >= reset_on]
    dropped = [
        Exclusion(r.date, r.session_id, f"{REASON_BEFORE_RESET}: {reason}")
        for r in readings
        if r.date < reset_on
    ]
    return kept, excluded + dropped


def tier_change_reset(
    previous_readings: Iterable[Reading], tier: str | None, baseline_readings: Iterable[Reading]
) -> date | None:
    """The local day a fresh baseline begins on after a sustained tier
    change, or ``None``.

    T083's tier rule is resolved twice -- on the previous window
    (``previous_readings``, ``[D-126, D-67]``) and on the current baseline
    window (``baseline_readings``, all tiers, already clipped by any
    coverage gap) -- and a change is asserted only when the two tiers
    differ **and both windows sustain their tier** with at least
    ``MIN_BASELINE_READINGS``. The reset lands on the first reading of the
    new tier inside the baseline window.

    **A tier resolution that differs only because the previous window is
    thin is not a change.** Five snapshot readings in ``[D-126, D-67]``
    resolve that window to the snapshot by "most readings", but nothing
    sustained a baseline there; a strap baseline now is the athlete's first
    established one, not a change from anything. Symmetrically a thin
    current window is not yet a change: F005 fires the reset "when
    chest_strap_raw readings become dense enough to sustain a baseline",
    and until then the previous tier still holds the baseline (its readings
    are in the window, the new tier's are off-tier) so no suppression can
    be read off the new tier's values. A single off-tier capture therefore
    never resets (§3.7.3: corroboration, "never merged into the same band").

    A coverage gap takes precedence: ``build_series`` only asks this rule
    when no gap reset was found, because the gap's resumption day is where
    the fresh baseline begins and the tier was already resolved on that era.
    """
    if tier is None:
        return None
    previous_counts = _tier_counts(previous_readings)
    previous_tier = resolve_baseline_tier(previous_counts)
    if previous_tier is None or previous_counts[previous_tier] < MIN_BASELINE_READINGS:
        return None
    if previous_tier == tier:
        return None
    on_tier = [r for r in baseline_readings if r.tier == tier]
    if len(on_tier) < MIN_BASELINE_READINGS:
        return None
    return min(r.date for r in on_tier)
