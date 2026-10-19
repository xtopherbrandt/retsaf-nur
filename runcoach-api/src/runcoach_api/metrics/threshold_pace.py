"""Functional threshold pace from tagged races and threshold workouts (F024, F026, F027, spec/04
section 4.3): the qualifier and the filter.

**Pure, by design**, on the pattern of ``metrics/load_chart.py`` and ``metrics/hrv_trend.py``:
``estimate`` takes plain candidate rows and an aware ``as_of`` instant and returns a frozen
dataclass; it imports neither ``config`` nor ``db`` nor ``fastapi``. The route reads the candidate
sessions and their F013 features and hands them over; the suites drive the computation with
hand-built dicts and no database. Rows are read **by key**, so a ``sqlite3.Row``-derived dict and a
plain dict are interchangeable:

- ``session_id``, ``start_time`` (an aware ``datetime`` or an aware ISO 8601 string; a naive value
  is a defect of the writer and raises, as ``hrv_trend.local_day`` does), ``sport``, ``kind``;
- ``timer_time_s`` and the summary's ``duration_s``;
- ``quality_flags``: every flag the session and F013 carry, together, as an iterable of strings
  (``None`` reads as none);
- F013's ``gap_avg_pace_s_per_km``, ``gap_coverage`` and ``gps_degraded_fraction`` (the last two
  are shown, never judged).

**Which rows are read.** The reader decides each row's ``kind``, ``race`` or ``threshold_workout``
(F027 reference section 2: the athlete's tag, or a workout name in the API's
``threshold_workout_names``); the estimator filters and labels on ``kind`` and never reads
``activity_tag``. A row whose ``sport`` is not ``running`` or whose ``kind`` is neither is in
neither ``efforts`` nor ``excluded``. ``admitted_by`` and ``workout_name`` are carried from the row
onto each workout's ``Effort`` or ``Exclusion`` when the reader provides them.

**Which races qualify** (F024 reference section 2), checked in this order; the first that fails is
the reason:

1. ``kind`` is ``race`` (the reader's decision from the tag).
2. The duration ``d`` is ``timer_time_s``, or the summary ``duration_s`` when ``timer_time_s`` is
   ``None``; ``d`` must be a real number (a bool or a string is refused, not coerced), finite, and
   ``1200 <= d <= 3600``. Otherwise ``outside_threshold_band``.
3. The flags hold neither ``distance_regressed`` nor ``smart_recording``, checked in that fixed
   order whatever order the flags arrive in. Otherwise the first of those two present.
4. ``gap_avg_pace_s_per_km`` is a real number, finite and > 0, whose speed ``1000 / pace`` is finite
   (a subnormal pace overflows it). Otherwise ``gap_pace_unavailable``, deliberately not F013's
   flag ``gap_unavailable``, which means coverage below 1 and does not exclude.

Degraded GPS never excludes: the fraction is carried on each effort for the athlete to judge.
Rows whose ``start_time`` is after ``as_of`` are ignored entirely, qualifying or not.

**The filter** (reference section 3, constants of section 1): in log speed ``y = ln(1000 / pace)``,
efforts replayed in ``start_time`` then ``session_id`` order. The first sets ``x = y`` and
``P = R``; each later one, ``dt`` fractional days after the previous, ``P += Q dt``,
``K = P / (P + R)``, ``x += K (y - x)``, ``P = (1 - K) P``; at ``as_of``, ``P += Q dt``. Each
effort carries the gain it entered with (1 for the first). Published: pace ``1000 / exp(x)``, the
80 % interval ``1000 / exp(x +- z sqrt(P))`` (fast end first, symmetric in log speed), ``error =
sqrt(P)``, and the label: ``high`` while ``P <= R``, ``medium`` while ``P <= 4 R``, ``low`` above.
Labels compare P with R and 4 R directly, never ``sqrt(P)`` with 0.05; in doubles the reference's
boundaries land on their stated side with no tolerance (one race and 42 days gives ``P == 4 R``).

``short_races_only`` is true when at least one race is used and every race used lasted under
``SHORT_RACE_S`` (a 20-30 min race reads threshold fast); workouts never set it (F026 reference
section 4). It is a display warning and changes no number. With no qualifying effort the result is
unavailable, ``no_qualifying_effort``, and every number is ``None``.

**Threshold workouts** (F027 reference sections 3 and 4, which amend F026's). A workout enters the
same filter with the same ``R``, at its session ``start_time``, with ``y`` from the GAP pace over
its counted reps' windows. Its row carries what the reader decided from its laps, steps and
records, by key:

- ``laps_read_at`` (null when the laps were never read);
- ``lap_reason``: ``active_laps``' reason (``lap_times_unreadable``, ``no_workout_steps`` or
  ``no_sustained_work``) or null;
- ``block_reason``: ``active_block``'s reason (``gap_pace_unavailable`` or
  ``active_time_below_minimum``) or null, with ``active_time_s`` (``A``, the counted reps' summed
  ``timer_s``) and ``active_blocks``;
- ``quality_flags``, and the block's ``gap_avg_pace_s_per_km``, ``gap_coverage`` and
  ``gps_degraded_fraction`` over the counted windows (read only for a workout past order 5).

Checked in the reference section 4 order, the first that fails being the reason: order 1
``laps_read_at`` set, else ``laps_not_read``; orders 3 to 5, ``lap_reason``; order 7, the race's
flag row above; orders 8 to 11, ``block_reason``, then ``A`` finite and at least
``WORKOUT_MIN_ACTIVE_S`` (else ``active_time_below_minimum``) and the race's GAP row above.
``workout_lap_reason`` decides orders 1 to 5, so the reader applies them before any record read
with the estimator's own rule; order 2 (``laps_unreadable``) and order 6 (the record reasons) are
the reader's, which lists them as unreadable entries. Each ``Effort`` and ``Exclusion`` carries its
``kind``; a workout's ``duration_s`` is ``A`` and its ``active_blocks`` the count of runs of
consecutive counted laps (``None`` for a race).

**The sustained work reps of a threshold workout** (F027 reference section 3, section 4 orders 3
to 5 and 8 to 11), also pure: ``active_laps``, ``rep_paces`` and ``active_block`` read laps,
workout steps and F013's per-segment stream (``session_features.segment_rows``) as mappings, by
key, and are documented where they are defined. ``workout_name_admits`` is the name half of
reference section 2's admission, for the reader.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from statistics import NormalDist
from types import MappingProxyType
from typing import Any

from runcoach_api.metrics import finite as _finite_screen

TYPICAL_ERROR = 0.025
"""sqrt(R): one race's typical error in log speed (2.5 % of speed; user ruling 2026-10-14)."""
R = TYPICAL_ERROR**2
DOUBLING_DAYS = 42
"""The error doubles (P times 4) over this many unobserved days, so Q = 3 R / 42 per day."""
Q_PER_DAY = 3 * R / DOUBLING_DAYS
COVERAGE = 0.80
Z = 1.2815515655446004
"""The 0.9 quantile of the standard normal: the half-width of the 80 % interval in units of sqrt(P).
Kept as the literal spec/04 section 4.3.4 states (``inv_cdf(0.9)`` differs in the last digit); the
assert below refuses to load the module if it ever drifts from ``COVERAGE``."""
assert abs(Z - NormalDist().inv_cdf(0.5 + COVERAGE / 2)) < 1e-12, (Z, COVERAGE)
HIGH_MAX_ERROR = TYPICAL_ERROR
MEDIUM_MAX_ERROR = 2 * TYPICAL_ERROR
SHORT_RACE_S = 1800
BAND_MIN_S = 1200
BAND_MAX_S = 3600
SECONDS_PER_DAY = 86400.0
WORKOUT_MIN_ACTIVE_S = 590
"""A threshold workout's least counted time ``A`` in seconds (F027 reference section 4 order 10:
10 minutes with 10 s of grace for rounding and a lap closed a second early); no maximum."""
WORKOUT_MIN_REP_S = 290
"""A work rep's least length in seconds to be sustained (order 5: 5 minutes with the same grace)."""
WORKOUT_REP_BAND = 1.10
"""A sustained rep counts when ``rep_pace <= WORKOUT_REP_BAND * fastest_pace`` (order 9)."""

PARAMETERS: Mapping[str, float | int] = MappingProxyType(
    {
        "typical_error": TYPICAL_ERROR,
        "doubling_days": DOUBLING_DAYS,
        "coverage": COVERAGE,
        "high_max_error": HIGH_MAX_ERROR,
        "medium_max_error": MEDIUM_MAX_ERROR,
        "short_race_s": SHORT_RACE_S,
        "workout_min_active_s": WORKOUT_MIN_ACTIVE_S,
        "workout_min_rep_s": WORKOUT_MIN_REP_S,
        "workout_rep_band": WORKOUT_REP_BAND,
    }
)
"""The F024 reference section 1 keys and values and F027's three workout bounds, served as the
response's ``parameters``."""

RUNNING = "running"
RACE = "race"
THRESHOLD_WORKOUT = "threshold_workout"
KINDS = (RACE, THRESHOLD_WORKOUT)
"""The ``kind`` values the candidate filter admits, each an effort's ``kind``."""
FLAG_ORDER = ("distance_regressed", "smart_recording")
OUTSIDE_THRESHOLD_BAND = "outside_threshold_band"
GAP_PACE_UNAVAILABLE = "gap_pace_unavailable"
NO_QUALIFYING_EFFORT = "no_qualifying_effort"
LAPS_NOT_READ = "laps_not_read"
ACTIVE_TIME_BELOW_MINIMUM = "active_time_below_minimum"


@dataclass(frozen=True)
class Effort:
    """A qualifying race or threshold workout used by the filter, with the gain ``K`` it entered with.

    For a workout, ``duration_s`` is its counted time ``A``, the GAP pace and the two fractions are
    over its counted windows, and ``active_blocks`` counts runs of consecutive counted laps; a race's
    ``active_blocks`` is ``None``. ``admitted_by`` (``tag``, ``name``, in that order) and
    ``workout_name`` are the reader's admission record for a workout, ``None`` when the row carries
    none and on every race."""

    session_id: str
    start_time: datetime
    duration_s: float
    gap_pace_s_per_km: float
    gap_coverage: Any
    gps_degraded_fraction: Any
    gain: float
    kind: str
    active_blocks: int | None
    admitted_by: tuple[str, ...] | None = None
    workout_name: str | None = None


@dataclass(frozen=True)
class Exclusion:
    """A running race or threshold workout at or before ``as_of`` that did not qualify, and why;
    ``admitted_by`` and ``workout_name`` as on ``Effort``."""

    session_id: str
    start_time: datetime
    reason: str
    kind: str
    admitted_by: tuple[str, ...] | None = None
    workout_name: str | None = None


@dataclass(frozen=True)
class ThresholdPace:
    """The estimate at ``as_of``; every number is ``None`` when ``unavailable`` is set."""

    as_of: datetime
    efforts: tuple[Effort, ...]
    excluded: tuple[Exclusion, ...]
    x: float | None
    variance: float | None
    value: float | None
    interval_low: float | None
    interval_high: float | None
    error: float | None
    confidence: str | None
    short_races_only: bool
    unavailable: str | None
    parameters: Mapping[str, float | int] = field(default_factory=lambda: dict(PARAMETERS))


def _aware(value: Any, what: str) -> datetime:
    instant = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
        raise ValueError(
            f"{what} is naive ({value!r}); an instant without a zone cannot be ordered against as_of"
        )
    return instant


def _real(value: Any) -> float | None:
    """``metrics.finite.finite``: a real, finite number as a float, else ``None``."""
    return _finite_screen.finite(value)


def duration_of(row: Mapping[str, Any]) -> float | None:
    """The duration ``d`` of reference section 2 row 2: ``timer_time_s``, or the summary's
    ``duration_s`` when ``timer_time_s`` is ``None``, through the finite screen."""
    timer = row.get("timer_time_s")
    return _real(row.get("duration_s") if timer is None else timer)


def in_band(duration: float | None) -> bool:
    """Whether ``duration`` lies in the threshold band, ``BAND_MIN_S <= d <= BAND_MAX_S``."""
    return duration is not None and BAND_MIN_S <= duration <= BAND_MAX_S


def _flags(row: Mapping[str, Any]) -> frozenset[str]:
    flags = row.get("quality_flags")
    if flags is None:
        return frozenset()
    if isinstance(flags, str | bytes):
        raise TypeError(
            f"session {row.get('session_id')!r} has quality_flags as one string ({flags!r}); "
            "the caller decodes the stored JSON into a list of flags"
        )
    return frozenset(flags)


def _log_speed(pace: Any) -> float | None:
    """``ln(1000 / pace)`` for a usable GAP pace, else ``None`` (reference section 2 row 4)."""
    number = _real(pace)
    if number is None or number <= 0:
        return None
    speed = 1000 / number
    if not math.isfinite(speed) or speed <= 0:
        return None
    return math.log(speed)


def _pace_at(log_speed: float) -> float:
    """``1000 / exp(log_speed)``, computed in logs so no finite state raises an OverflowError."""
    exponent = math.log(1000) - log_speed
    try:
        return math.exp(exponent)
    except OverflowError:
        return math.inf


def _label(variance: float) -> str:
    if variance <= R:
        return "high"
    if variance <= 4 * R:
        return "medium"
    return "low"


def _days(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / SECONDS_PER_DAY


def _short_races_only(efforts: Iterable[Effort]) -> bool:
    """At least one race used and every race used under ``SHORT_RACE_S``; workouts are not read."""
    races = [effort for effort in efforts if effort.kind == RACE]
    return bool(races) and all(effort.duration_s < SHORT_RACE_S for effort in races)


def active_time_of(row: Mapping[str, Any]) -> float | None:
    """A workout's counted time ``A`` through the finite screen: ``None`` when it is not a real,
    finite number (an inf from finite laps whose sum overflowed included)."""
    return _real(row.get("active_time_s"))


def workout_lap_reason(row: Mapping[str, Any]) -> str | None:
    """F027 reference section 4 orders 1 to 5 for one workout row: the first reason, or ``None``
    when the workout passes them. Reads only ``laps_read_at`` and ``lap_reason`` (``active_laps``'
    decision of orders 3 to 5), so the reader decides them before any record read. A
    ``lap_reason`` that is not one of ``active_laps``' three is a defect of the writer and raises."""
    if row.get("laps_read_at") is None:
        return LAPS_NOT_READ
    lap_reason = row.get("lap_reason")
    if lap_reason is not None:
        if lap_reason not in LAP_REASONS:
            raise ValueError(
                f"session {row.get('session_id')!r} has lap_reason {lap_reason!r}, not one of active_laps'"
            )
        return lap_reason
    return None


def _workout_block_reason(row: Mapping[str, Any]) -> str | None:
    """Orders 8 to 11 for one workout row past the flags: ``block_reason`` (``active_block``'s
    decision) when set, else ``active_time_below_minimum`` unless ``A`` is finite and at least
    ``WORKOUT_MIN_ACTIVE_S``; the GAP row is the caller's. An unknown ``block_reason`` raises."""
    block_reason = row.get("block_reason")
    if block_reason is not None:
        if block_reason not in BLOCK_REASONS:
            raise ValueError(
                f"session {row.get('session_id')!r} has block_reason {block_reason!r}, not one of active_block's"
            )
        return block_reason
    active = active_time_of(row)
    if active is None or active < WORKOUT_MIN_ACTIVE_S:
        return ACTIVE_TIME_BELOW_MINIMUM
    return None


def _admission(row: Mapping[str, Any]) -> tuple[tuple[str, ...] | None, str | None]:
    """The reader's ``admitted_by`` (as a tuple) and ``workout_name``, ``None`` when absent."""
    admitted_by = row.get("admitted_by")
    return (None if admitted_by is None else tuple(admitted_by)), row.get("workout_name")


def _exclusion(session_id: str, start: datetime, reason: str, row: Mapping[str, Any]) -> Exclusion:
    admitted_by, workout_name = _admission(row)
    return Exclusion(session_id, start, reason, row["kind"], admitted_by, workout_name)


def estimate(rows: Iterable[Mapping[str, Any]], as_of: datetime) -> ThresholdPace:
    """Qualify ``rows`` and replay the filter to ``as_of`` (an aware instant)."""
    as_of = _aware(as_of, "as_of")
    candidates: list[tuple[datetime, str, Mapping[str, Any]]] = []
    for row in rows:
        if row.get("sport") != RUNNING or row.get("kind") not in KINDS:
            continue
        start = _aware(row["start_time"], f"session {row.get('session_id')!r} start_time")
        if start > as_of:
            continue
        candidates.append((start, str(row["session_id"]), row))
    candidates.sort(key=lambda item: (item[0], item[1]))

    used: list[tuple[datetime, str, Mapping[str, Any], float, float]] = []
    excluded: list[Exclusion] = []
    for start, session_id, row in candidates:
        kind = row["kind"]
        if kind == RACE:
            duration = duration_of(row)
            if not in_band(duration):
                excluded.append(_exclusion(session_id, start, OUTSIDE_THRESHOLD_BAND, row))
                continue
        else:
            reason = workout_lap_reason(row)
            if reason is not None:
                excluded.append(_exclusion(session_id, start, reason, row))
                continue
        flags = _flags(row)
        flagged = next((flag for flag in FLAG_ORDER if flag in flags), None)
        if flagged is not None:
            excluded.append(_exclusion(session_id, start, flagged, row))
            continue
        if kind != RACE:
            reason = _workout_block_reason(row)
            if reason is not None:
                excluded.append(_exclusion(session_id, start, reason, row))
                continue
            duration = active_time_of(row)
        y = _log_speed(row.get("gap_avg_pace_s_per_km"))
        if y is None:
            excluded.append(_exclusion(session_id, start, GAP_PACE_UNAVAILABLE, row))
            continue
        used.append((start, session_id, row, duration, y))

    if not used:
        return ThresholdPace(
            as_of=as_of,
            efforts=(),
            excluded=tuple(excluded),
            x=None,
            variance=None,
            value=None,
            interval_low=None,
            interval_high=None,
            error=None,
            confidence=None,
            short_races_only=False,
            unavailable=NO_QUALIFYING_EFFORT,
        )

    efforts: list[Effort] = []
    x = 0.0
    variance = 0.0
    previous: datetime | None = None
    for start, session_id, row, duration, y in used:
        if previous is None:
            x, variance, gain = y, R, 1.0
        else:
            variance = variance + Q_PER_DAY * _days(start, previous)
            gain = variance / (variance + R)
            x = x + gain * (y - x)
            variance = (1 - gain) * variance
        previous = start
        admitted_by, workout_name = _admission(row)
        efforts.append(
            Effort(
                session_id=session_id,
                start_time=start,
                duration_s=duration,
                gap_pace_s_per_km=float(row["gap_avg_pace_s_per_km"]),
                gap_coverage=row.get("gap_coverage"),
                gps_degraded_fraction=row.get("gps_degraded_fraction"),
                gain=gain,
                kind=row["kind"],
                active_blocks=None if row["kind"] == RACE else row.get("active_blocks"),
                admitted_by=admitted_by,
                workout_name=workout_name,
            )
        )
    assert previous is not None
    variance = variance + Q_PER_DAY * _days(as_of, previous)
    spread = Z * math.sqrt(variance)
    return ThresholdPace(
        as_of=as_of,
        efforts=tuple(efforts),
        excluded=tuple(excluded),
        x=x,
        variance=variance,
        value=_pace_at(x),
        interval_low=_pace_at(x + spread),
        interval_high=_pace_at(x - spread),
        error=math.sqrt(variance),
        confidence=_label(variance),
        short_races_only=_short_races_only(efforts),
        unavailable=None,
    )


# -- Admission by workout name (F027 reference section 2) ------------------------------------------


def _name_key(value: Any) -> str | None:
    return value.strip().casefold() if isinstance(value, str) else None


def workout_name_admits(names: Iterable[Any], step_workout_names: Iterable[Any]) -> bool:
    """Whether any step row's ``workout_name`` equals any configured name, both sides compared as
    Python ``str.strip().casefold()`` (never SQL ``TRIM``/``LOWER``); a row whose name is not a
    string (null included) matches nothing."""
    wanted = {key for key in map(_name_key, names) if key is not None}
    return any(key in wanted for key in map(_name_key, step_workout_names) if key is not None)


# -- The sustained work reps of a threshold workout (F027 reference sections 3 and 4) --------------

ACTIVE = "active"
INTERVAL = "interval"
WORK_INTENSITIES = (ACTIVE, INTERVAL)
"""A work lap's and a work step's intensity: Garmin Coach writes its work steps ``interval``."""
OPEN_TARGET = "open"
REPEAT_PREFIX = "repeat_until"
"""A step whose ``duration_type`` starts so is a repeat step: it loops over other steps and is never
work, whatever intensity and target were written on it (``repeat_until_steps_cmplt``, ``_time``,
``_distance``, ``_hr_less_than`` and the rest)."""
REMAINDER_MAX_S = 2.0
"""A work lap with ``end_t == start_t`` and ``0 <= timer_s`` under this is a remainder lap (section
3: whole-second stamps close a step's last metres in a lap of under 2 s)."""
LAP_TIMES_UNREADABLE = "lap_times_unreadable"
NO_WORKOUT_STEPS = "no_workout_steps"
NO_SUSTAINED_WORK = "no_sustained_work"
LAP_REASONS = (LAP_TIMES_UNREADABLE, NO_WORKOUT_STEPS, NO_SUSTAINED_WORK)
"""``active_laps``' reasons, orders 3, 4 and 5 in that order."""
BLOCK_REASONS = (GAP_PACE_UNAVAILABLE, ACTIVE_TIME_BELOW_MINIMUM)
"""``active_block``'s reasons, orders 8 (and 11) and 10."""


@dataclass(frozen=True)
class Rep:
    """A maximal run of work laps adjacent in ``lap_index`` order sharing one step index.

    ``length_s`` is its laps' summed ``timer_s`` (it may overflow to inf from finite laps);
    ``windows`` its laps' ``[start_t, end_t)`` in order, a remainder lap giving none; ``first_lap``
    and ``last_lap`` the positions of its first and last lap in the laps as given, so two reps are
    adjacent when one's ``first_lap`` is the other's ``last_lap + 1``."""

    step_index: int
    length_s: float
    windows: tuple[tuple[float, float], ...]
    first_lap: int
    last_lap: int

    @property
    def sustained(self) -> bool:
        """Order 5: ``length_s >= WORKOUT_MIN_REP_S`` (an inf length is sustained; order 10 refuses it)."""
        return self.length_s >= WORKOUT_MIN_REP_S


@dataclass(frozen=True)
class ActiveLaps:
    """Orders 3, 4 and 5 decided from laps and steps alone; ``laps`` and ``reps`` are empty when
    ``unavailable`` is set.

    ``laps`` are the work laps in the order given, ``reps`` their reps in that order (short ones
    included, so the caller can show what was read); ``sustained_reps`` the reps of at least
    ``WORKOUT_MIN_REP_S``. The band, ``A`` and the pace are ``active_block``'s.
    """

    laps: tuple[Mapping[str, Any], ...]
    reps: tuple[Rep, ...]
    unavailable: str | None

    @property
    def sustained_reps(self) -> tuple[Rep, ...]:
        return tuple(rep for rep in self.reps if rep.sustained)


@dataclass(frozen=True)
class ActiveBlock:
    """Orders 8 to 11 on ``active_laps``' sustained reps: the counted time ``A``, the GAP pace, the
    block count and the window fractions over the counted windows; every number is ``None`` and the
    windows empty when ``unavailable`` is set (``active_laps``' reason or this function's)."""

    active_time_s: float | None
    gap_pace_s_per_km: float | None
    active_blocks: int
    windows: tuple[tuple[float, float], ...]
    gap_coverage: float | None
    gps_degraded_fraction: float | None
    unavailable: str | None


def _step_index(value: Any) -> int | None:
    """An int index, or ``None``: a bool is not an index (``True`` would otherwise name step 1)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_work_step(step: Mapping[str, Any]) -> bool:
    """Section 3: intensity ``active`` or ``interval``, not a repeat step, and a ``target_type`` that
    is neither null nor ``open``."""
    duration_type = step.get("duration_type")
    if isinstance(duration_type, str) and duration_type.startswith(REPEAT_PREFIX):
        return False
    target = step.get("target_type")
    return step.get("intensity") in WORK_INTENSITIES and target is not None and target != OPEN_TARGET


def _work_step_indices(steps: Iterable[Mapping[str, Any]]) -> frozenset[int]:
    """The step indices that name a work step: an index carried by exactly one step row, a work step.

    An index carried by two rows names no work step, whether the second is a non-work step
    (section 3) or another work step (the store keeps one row per step index, so a second row is a
    defect, not a step). A row without an int index names nothing.
    """
    seen: dict[int, bool | None] = {}
    for step in steps:
        index = _step_index(step.get("step_index"))
        if index is None:
            continue
        seen[index] = None if index in seen else _is_work_step(step)
    return frozenset(index for index, work in seen.items() if work)


def _lap_times(lap: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """``(start_t, end_t, timer_s)`` when all three are finite reals (``metrics.finite``, so a bool is
    refused) and either ``end_t > start_t`` with ``timer_s >= 0``, or the lap is a remainder lap
    (``end_t == start_t``, ``0 <= timer_s < REMAINDER_MAX_S``); else ``None``."""
    start, end, timer = (_real(lap.get(key)) for key in ("start_t", "end_t", "timer_s"))
    if start is None or end is None or timer is None or timer < 0:
        return None
    if end > start or (end == start and timer < REMAINDER_MAX_S):
        return start, end, timer
    return None


@dataclass
class _OpenRep:
    """A rep being built by ``active_laps`` while its laps are read."""

    step_index: int
    first_lap: int
    length_s: float = 0.0
    windows: list[tuple[float, float]] = field(default_factory=list)
    last_lap: int = -1

    def close(self) -> Rep:
        return Rep(self.step_index, self.length_s, tuple(self.windows), self.first_lap, self.last_lap)


def active_laps(laps: Iterable[Mapping[str, Any]], steps: Iterable[Mapping[str, Any]]) -> ActiveLaps:
    """The work laps of a workout and their reps: F027 reference section 3, deciding section 4's
    orders 3, 4 and 5.

    ``laps`` arrive in ``lap_index`` order, as ``mapping.to_laps`` maps them and the store returns
    them. A lap is a *work lap* when its intensity is ``active`` or ``interval`` and its
    ``wkt_step_index`` (an int, not a bool) names a work step of ``steps`` (``_work_step_indices``:
    intensity ``active`` or ``interval``, not a repeat step, a target that is neither null nor
    ``open``, the index carried by that one row). A null step index (the lap after a workout ends,
    a free run's auto-lap) is never a work lap.

    Order 3 first: every work lap's ``start_t``, ``end_t`` and ``timer_s`` must be finite reals
    (``metrics.finite``), ``end_t > start_t`` and ``timer_s >= 0``, or the lap is a remainder lap
    (``end_t == start_t`` and ``0 <= timer_s < 2``); any other work lap gives
    ``lap_times_unreadable``, never a smaller rep. A fault on a lap that is not a work lap changes
    nothing. Then order 4: no work lap gives ``no_workout_steps``. The reps are the maximal runs of
    work laps adjacent in the order given that share one step index (any other lap between two
    ends the rep; a step repeated back to back merges); a rep's length is its laps' summed
    ``timer_s``, its windows its laps' ``[start_t, end_t)``, a remainder lap adding its time and no
    window. Then order 5: no rep of at least ``WORKOUT_MIN_REP_S`` gives ``no_sustained_work``.
    """
    work_steps = _work_step_indices(steps)
    chosen: list[Mapping[str, Any]] = []
    reps: list[Rep] = []
    current: _OpenRep | None = None
    for position, lap in enumerate(laps):
        index = _step_index(lap.get("wkt_step_index"))
        if lap.get("intensity") not in WORK_INTENSITIES or index is None or index not in work_steps:
            if current is not None:
                reps.append(current.close())
                current = None
            continue
        times = _lap_times(lap)
        if times is None:
            return ActiveLaps((), (), LAP_TIMES_UNREADABLE)
        start, end, timer = times
        chosen.append(lap)
        if current is not None and current.step_index != index:
            reps.append(current.close())
            current = None
        if current is None:
            current = _OpenRep(index, position)
        current.length_s += timer
        if end > start:
            current.windows.append((start, end))
        current.last_lap = position
    if current is not None:
        reps.append(current.close())
    if not chosen:
        return ActiveLaps((), (), NO_WORKOUT_STEPS)
    if not any(rep.sustained for rep in reps):
        return ActiveLaps(tuple(chosen), tuple(reps), NO_SUSTAINED_WORK)
    return ActiveLaps(tuple(chosen), tuple(reps), None)


@dataclass
class _Sums:
    seconds: float = 0.0
    distance: float = 0.0
    gap_distance: float = 0.0
    graded_m: float = 0.0
    degraded_m: float = 0.0

    def add(self, row: Mapping[str, Any]) -> None:
        metres = row["contributed_m"]
        self.seconds += row["dt"]
        self.distance += metres
        self.gap_distance += metres * row["g"]
        if row["graded"]:
            self.graded_m += metres
        if row["gps_degraded"]:
            self.degraded_m += metres

    def merge(self, other: _Sums) -> None:
        """Add another rep's sums to these (the counted windows together)."""
        self.seconds += other.seconds
        self.distance += other.distance
        self.gap_distance += other.gap_distance
        self.graded_m += other.graded_m
        self.degraded_m += other.degraded_m

    def pace(self) -> float | None:
        """``1000 * sum(dt) / sum(contributed_m * g)``, F013's whole-session formula restricted to
        the windows; ``None`` when the denominator is 0 or the result is not finite and > 0."""
        if self.gap_distance <= 0.0:
            return None
        pace = 1000.0 * self.seconds / self.gap_distance
        return pace if math.isfinite(pace) and pace > 0.0 else None


def _rep_sums(reps: Sequence[Rep], segment_rows: Iterable[Mapping[str, Any]]) -> list[_Sums]:
    """One walk over ``segment_rows`` against every rep's windows sorted by start (``bisect``): a
    segment belongs to the window whose start is the latest at or before its ``t_start`` when that
    is before the window's end, and counts there in full. Windows do not overlap (laps are
    consecutive), so this is the ``start <= t_start < end`` scan of each rep in one pass."""
    sums = [_Sums() for _ in reps]
    indexed = sorted(
        ((start, end, k) for k, rep in enumerate(reps) for start, end in rep.windows), key=lambda w: (w[0], w[1])
    )
    starts = [start for start, _end, _k in indexed]
    for row in segment_rows:
        t_start = row["t_start"]
        i = bisect_right(starts, t_start) - 1
        if i < 0:
            continue
        _start, end, k = indexed[i]
        if t_start < end:
            sums[k].add(row)
    return sums


def rep_paces(reps: Sequence[Rep], segment_rows: Iterable[Mapping[str, Any]]) -> tuple[float | None, ...]:
    """Each rep's GAP pace over its windows (order 8's input), ``None`` for a rep without one:
    F026's active GAP formula, ``1000 * sum(dt) / sum(contributed_m * g)`` over the counted
    segments whose ``t_start`` lies in one of the rep's windows."""
    return tuple(sums.pace() for sums in _rep_sums(reps, segment_rows))


def _block_count(reps: Sequence[Rep]) -> int:
    """Runs of consecutive counted laps: two counted reps whose laps touch (steps 1 then 2 of a
    Goal Pace Run) are one block; a lap between them (a recovery) starts another."""
    blocks = 0
    previous_last: int | None = None
    for rep in sorted(reps, key=lambda rep: rep.first_lap):
        if previous_last is None or rep.first_lap != previous_last + 1:
            blocks += 1
        previous_last = rep.last_lap
    return blocks


def _unavailable_block(reason: str) -> ActiveBlock:
    """``active_block``'s result under ``reason``: every number ``None``, no windows, no blocks."""
    return ActiveBlock(None, None, 0, (), None, None, reason)


def active_block(
    laps: Iterable[Mapping[str, Any]],
    steps: Iterable[Mapping[str, Any]],
    segment_rows: Iterable[Mapping[str, Any]],
) -> ActiveBlock:
    """``active_laps`` and F027 reference section 4 orders 8 to 11 over F013's counted segments.

    ``segment_rows`` is ``session_features.segment_rows``' stream (counted segments only), on the
    same time origin as the laps. A segment is in a window when its ``t_start`` lies in
    ``[start_t, end_t)``, and then counts there in full: a moving dropout that starts inside a work
    lap contributes all its ``dt`` and metres even when it ends after the lap, and one that starts
    outside every window contributes nothing (``_rep_sums``, one walk for every rep).

    In order, the first that fails being ``unavailable``:

    8. each sustained rep's GAP pace (``rep_paces``); a rep whose pace is not finite and > 0 is
       dropped; none left gives ``gap_pace_unavailable``;
    9. the band: a rep counts when ``rep_pace <= WORKOUT_REP_BAND * fastest_pace`` (the fastest
       always counts);
    10. ``A``, the counted reps' summed length, finite and at least ``WORKOUT_MIN_ACTIVE_S``, else
        ``active_time_below_minimum``;
    11. the GAP pace over the counted windows together, finite and > 0, else
        ``gap_pace_unavailable`` (reachable only by overflow once order 8 has passed).

    Over the counted windows, with ``D`` their contributed metres: ``gap_coverage`` is the graded
    share of ``D`` and ``gps_degraded_fraction`` the share whose start record carries
    ``gps_degraded``, both ``None`` when ``D`` is 0. ``active_blocks`` counts runs of consecutive
    counted laps, remainder laps included; ``windows`` are the counted reps' windows in lap order.
    When ``active_laps`` sets a reason, it is carried and every number is ``None``.
    """
    decided = active_laps(laps, steps)
    if decided.unavailable is not None:
        return _unavailable_block(decided.unavailable)
    sustained = decided.sustained_reps
    sums = _rep_sums(sustained, segment_rows)
    paced = [(rep, total, total.pace()) for rep, total in zip(sustained, sums, strict=True)]
    paced = [(rep, total, pace) for rep, total, pace in paced if pace is not None]
    if not paced:
        return _unavailable_block(GAP_PACE_UNAVAILABLE)
    fastest = min(pace for _rep, _total, pace in paced)
    counted = [(rep, total) for rep, total, pace in paced if pace <= WORKOUT_REP_BAND * fastest]
    active_time = sum(rep.length_s for rep, _total in counted)
    if not math.isfinite(active_time) or active_time < WORKOUT_MIN_ACTIVE_S:
        return _unavailable_block(ACTIVE_TIME_BELOW_MINIMUM)
    whole = _Sums()
    for _rep, total in counted:
        whole.merge(total)
    pace = whole.pace()
    if pace is None:
        return _unavailable_block(GAP_PACE_UNAVAILABLE)
    return ActiveBlock(
        active_time_s=active_time,
        gap_pace_s_per_km=pace,
        active_blocks=_block_count([rep for rep, _total in counted]),
        windows=tuple(window for rep, _total in counted for window in rep.windows),
        gap_coverage=whole.graded_m / whole.distance if whole.distance > 0.0 else None,
        gps_degraded_fraction=whole.degraded_m / whole.distance if whole.distance > 0.0 else None,
        unavailable=None,
    )
