"""The resting-HRV trend (F005, spec §3.7): series construction.

This module turns stored ``sessions`` rows into **a clean one-reading-per-
local-day series for one source tier** -- the substrate the SWC band and the
verdict (T084) and the reset rules (T092) are computed over. It ends there.

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


def baseline_window(target_date: date) -> tuple[date, date]:
    """The closed local-date interval ``[D-66, D-7]``."""
    last = target_date - timedelta(days=WINDOW_DAYS)
    return last - timedelta(days=BASELINE_DAYS - 1), last


def judged_window(target_date: date) -> tuple[date, date]:
    """The closed local-date interval ``[D-6, D]``."""
    return target_date - timedelta(days=WINDOW_DAYS - 1), target_date


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
    """
    baseline = baseline_window(target_date)
    judged = judged_window(target_date)
    baseline_first = baseline[0]

    readings: list[Reading] = []
    excluded: list[Exclusion] = []
    for row in rows:
        session_id = row["session_id"]
        day, instant = local_day(session_id, row["start_time"], zone)
        tier = row["hrv_source_tier"]
        value = row["resting_rmssd_ms"]

        if not baseline_first <= day <= target_date:
            excluded.append(Exclusion(day, session_id, REASON_OUTSIDE_WINDOWS))
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

    tier = resolve_baseline_tier(Counter(r.tier for r in _within(readings, baseline)))
    if tier is None:
        tier = resolve_baseline_tier(Counter(r.tier for r in _within(readings, judged)))

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
    )
