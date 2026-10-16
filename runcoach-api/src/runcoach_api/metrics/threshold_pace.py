"""Functional threshold pace from tagged races (F024, spec/04 section 4.3): the qualifier and the filter.

**Pure, by design**, on the pattern of ``metrics/load_chart.py`` and ``metrics/hrv_trend.py``:
``estimate`` takes plain candidate rows and an aware ``as_of`` instant and returns a frozen
dataclass; it imports neither ``config`` nor ``db`` nor ``fastapi``. The route reads the tagged
sessions and their F013 features and hands them over; the suites drive the computation with
hand-built dicts and no database. Rows are read **by key**, so a ``sqlite3.Row``-derived dict and a
plain dict are interchangeable:

- ``session_id``, ``start_time`` (an aware ``datetime`` or an aware ISO 8601 string; a naive value
  is a defect of the writer and raises, as ``hrv_trend.local_day`` does), ``sport``,
  ``activity_tag``;
- ``timer_time_s`` and the summary's ``duration_s``;
- ``quality_flags``: every flag the session and F013 carry, together, as an iterable of strings
  (``None`` reads as none);
- F013's ``gap_avg_pace_s_per_km``, ``gap_coverage`` and ``gps_degraded_fraction`` (the last two
  are shown, never judged).

**Which runs qualify** (F024 reference section 2), checked in this order; the first that fails is
the reason:

1. ``sport`` is ``running`` and ``activity_tag`` is ``race`` (or ``threshold_workout``, read as
   below); otherwise the row is in neither ``efforts`` nor ``excluded``.
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

**Threshold workouts** (F026 reference sections 3 and 4). The candidate filter admits
``activity_tag`` ``threshold_workout`` beside ``race``; a workout enters the same filter with the
same ``R``, at its session ``start_time``, with ``y`` from its active GAP pace. Its row carries
what the reader decided from its laps and steps, by key:

- ``laps_read_at`` (null when the laps were never read);
- ``lap_reason``: ``active_laps``' reason (``lap_times_unreadable`` or ``no_workout_steps``) or null;
- ``active_time_s`` (``A``) and ``active_blocks``, from ``active_laps``;
- ``quality_flags``, and the active block's ``gap_avg_pace_s_per_km``, ``gap_coverage`` and
  ``gps_degraded_fraction`` over the active windows (read only for a workout past row 3).

Checked in this order, the first that fails being the reason: row 1 ``laps_read_at`` set, else
``laps_not_read``; rows 1a and 2, ``lap_reason``; row 3, ``A`` finite (an overflowed sum of finite
laps is refused) and ``A >= WORKOUT_MIN_ACTIVE_S``, else ``active_time_below_minimum``; then the
race's flag and GAP rows (3 and 4 above) unchanged. ``workout_lap_reason`` decides rows 1 to 3, so
the reader applies them before any record read with the estimator's own rule. Each ``Effort`` and
``Exclusion`` carries its ``kind``; a workout's ``duration_s`` is ``A`` and its ``active_blocks``
the count of runs of consecutive active laps (``None`` for a race).

**The active block of a threshold workout** (F026 reference section 3), also pure: ``active_laps``
and ``active_block`` read laps, workout steps and F013's per-segment stream (``session_features.
segment_rows``) as mappings, by key, and are documented where they are defined.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
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
WORKOUT_MIN_ACTIVE_S = 600
"""A threshold workout's least active time ``A`` in seconds; no maximum (F026 reference section 3 row 3)."""

PARAMETERS: Mapping[str, float | int] = MappingProxyType(
    {
        "typical_error": TYPICAL_ERROR,
        "doubling_days": DOUBLING_DAYS,
        "coverage": COVERAGE,
        "high_max_error": HIGH_MAX_ERROR,
        "medium_max_error": MEDIUM_MAX_ERROR,
        "short_race_s": SHORT_RACE_S,
        "workout_min_active_s": WORKOUT_MIN_ACTIVE_S,
    }
)
"""The F024 reference section 1 keys and values and F026's ``workout_min_active_s``, served as the
response's ``parameters``."""

RUNNING = "running"
RACE = "race"
THRESHOLD_WORKOUT = "threshold_workout"
KINDS = (RACE, THRESHOLD_WORKOUT)
"""The ``activity_tag`` values the candidate filter admits, each an effort's ``kind``."""
FLAG_ORDER = ("distance_regressed", "smart_recording")
OUTSIDE_THRESHOLD_BAND = "outside_threshold_band"
GAP_PACE_UNAVAILABLE = "gap_pace_unavailable"
NO_QUALIFYING_EFFORT = "no_qualifying_effort"
LAPS_NOT_READ = "laps_not_read"
ACTIVE_TIME_BELOW_MINIMUM = "active_time_below_minimum"


@dataclass(frozen=True)
class Effort:
    """A qualifying race or threshold workout used by the filter, with the gain ``K`` it entered with.

    For a workout, ``duration_s`` is its active time ``A``, the GAP pace and the two fractions are
    over its active windows, and ``active_blocks`` counts runs of consecutive active laps; a race's
    ``active_blocks`` is ``None``."""

    session_id: str
    start_time: datetime
    duration_s: float
    gap_pace_s_per_km: float
    gap_coverage: Any
    gps_degraded_fraction: Any
    gain: float
    kind: str
    active_blocks: int | None


@dataclass(frozen=True)
class Exclusion:
    """A tagged running race or threshold workout at or before ``as_of`` that did not qualify, and why."""

    session_id: str
    start_time: datetime
    reason: str
    kind: str


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
    """A workout's active time ``A`` through the finite screen: ``None`` when it is not a real,
    finite number (an inf from finite laps whose sum overflowed included)."""
    return _real(row.get("active_time_s"))


def workout_lap_reason(row: Mapping[str, Any]) -> str | None:
    """F026 reference section 3 rows 1, 1a, 2 and 3 for one workout row, in that order: the first
    reason, or ``None`` when the workout passes all four. Reads only ``laps_read_at``,
    ``lap_reason`` and ``active_time_s``, so the reader decides them before any record read."""
    if row.get("laps_read_at") is None:
        return LAPS_NOT_READ
    lap_reason = row.get("lap_reason")
    if lap_reason is not None:
        if lap_reason not in (LAP_TIMES_UNREADABLE, NO_WORKOUT_STEPS):
            raise ValueError(f"session {row.get('session_id')!r} has lap_reason {lap_reason!r}, not one of active_laps'")
        return lap_reason
    active = active_time_of(row)
    if active is None or active < WORKOUT_MIN_ACTIVE_S:
        return ACTIVE_TIME_BELOW_MINIMUM
    return None


def estimate(rows: Iterable[Mapping[str, Any]], as_of: datetime) -> ThresholdPace:
    """Qualify ``rows`` and replay the filter to ``as_of`` (an aware instant)."""
    as_of = _aware(as_of, "as_of")
    candidates: list[tuple[datetime, str, Mapping[str, Any]]] = []
    for row in rows:
        if row.get("sport") != RUNNING or row.get("activity_tag") not in KINDS:
            continue
        start = _aware(row["start_time"], f"session {row.get('session_id')!r} start_time")
        if start > as_of:
            continue
        candidates.append((start, str(row["session_id"]), row))
    candidates.sort(key=lambda item: (item[0], item[1]))

    used: list[tuple[datetime, str, Mapping[str, Any], float, float]] = []
    excluded: list[Exclusion] = []
    for start, session_id, row in candidates:
        kind = row["activity_tag"]
        if kind == RACE:
            duration = duration_of(row)
            if not in_band(duration):
                excluded.append(Exclusion(session_id, start, OUTSIDE_THRESHOLD_BAND, kind))
                continue
        else:
            reason = workout_lap_reason(row)
            if reason is not None:
                excluded.append(Exclusion(session_id, start, reason, kind))
                continue
            duration = active_time_of(row)
        flags = _flags(row)
        flagged = next((flag for flag in FLAG_ORDER if flag in flags), None)
        if flagged is not None:
            excluded.append(Exclusion(session_id, start, flagged, kind))
            continue
        y = _log_speed(row.get("gap_avg_pace_s_per_km"))
        if y is None:
            excluded.append(Exclusion(session_id, start, GAP_PACE_UNAVAILABLE, kind))
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
        efforts.append(
            Effort(
                session_id=session_id,
                start_time=start,
                duration_s=duration,
                gap_pace_s_per_km=float(row["gap_avg_pace_s_per_km"]),
                gap_coverage=row.get("gap_coverage"),
                gps_degraded_fraction=row.get("gps_degraded_fraction"),
                gain=gain,
                kind=row["activity_tag"],
                active_blocks=None if row["activity_tag"] == RACE else row.get("active_blocks"),
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


# -- The active block of a threshold workout (F026 reference section 3) -----------------------------

ACTIVE = "active"
REPEAT_PREFIX = "repeat_until"
"""A step whose ``duration_type`` starts so is a repeat step: it loops over other steps and its
``intensity`` means nothing (a Garmin repeat step keeps whatever intensity was written on it)."""
LAP_TIMES_UNREADABLE = "lap_times_unreadable"
NO_WORKOUT_STEPS = "no_workout_steps"


@dataclass(frozen=True)
class ActiveLaps:
    """Rows 1a and 2 decided from laps and steps alone; every number is empty when ``unavailable`` is set.

    ``active_time_s`` is ``A``, the active laps' summed ``timer_s``; it may overflow to inf from finite
    laps, and row 3 (``A`` finite and at least the minimum) is the caller's.
    """

    laps: tuple[Mapping[str, Any], ...]
    active_time_s: float | None
    windows: tuple[tuple[float, float], ...]
    active_blocks: int
    unavailable: str | None


@dataclass(frozen=True)
class ActiveBlock:
    """``active_laps`` plus the active GAP pace and the window fractions over the segment stream."""

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


def _active_step_indices(steps: Iterable[Mapping[str, Any]]) -> frozenset[int]:
    """The step indices that name an active step unambiguously.

    An index qualifies when every step carrying it has intensity ``active`` and none is a repeat step;
    an index shared by an active and a non-active step names no active step.
    """
    active: set[int] = set()
    refused: set[int] = set()
    for step in steps:
        index = _step_index(step.get("step_index"))
        if index is None:
            continue
        duration_type = step.get("duration_type")
        is_repeat = isinstance(duration_type, str) and duration_type.startswith(REPEAT_PREFIX)
        if step.get("intensity") == ACTIVE and not is_repeat:
            active.add(index)
        else:
            refused.add(index)
    return frozenset(active - refused)


def _lap_times(lap: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """``(start_t, end_t, timer_s)`` when all three are finite, ``end_t > start_t`` and ``timer_s >= 0``."""
    start, end, timer = (_real(lap.get(key)) for key in ("start_t", "end_t", "timer_s"))
    if start is None or end is None or timer is None or end <= start or timer < 0:
        return None
    return start, end, timer


def active_laps(laps: Iterable[Mapping[str, Any]], steps: Iterable[Mapping[str, Any]]) -> ActiveLaps:
    """The active laps of a workout, its active time ``A``, windows and block count (rows 1a and 2).

    ``laps`` arrive in ``lap_index`` order, as ``mapping.to_laps`` maps them and the store returns
    them. A lap is *otherwise active* when its intensity is exactly ``active`` and its
    ``wkt_step_index`` (an int, not a bool) names an active step of ``steps``; it is *active* when its
    ``start_t``, ``end_t`` and ``timer_s`` are also finite numbers (``metrics.finite``), ``end_t >
    start_t`` and ``timer_s >= 0``. A null step index (the lap after a workout ends) is never active.

    Row 1a first: any otherwise-active lap that is not active gives ``lap_times_unreadable``, never a
    smaller block. Then row 2: no active lap gives ``no_workout_steps``. The windows are each active
    lap's ``[start_t, end_t)``; ``active_blocks`` counts runs of consecutive active laps in ``laps``.
    """
    active_steps = _active_step_indices(steps)
    chosen: list[Mapping[str, Any]] = []
    windows: list[tuple[float, float]] = []
    total = 0.0
    blocks = 0
    previous_active = False
    for lap in laps:
        index = _step_index(lap.get("wkt_step_index"))
        if lap.get("intensity") != ACTIVE or index is None or index not in active_steps:
            previous_active = False
            continue
        times = _lap_times(lap)
        if times is None:
            return ActiveLaps((), None, (), 0, LAP_TIMES_UNREADABLE)
        start, end, timer = times
        chosen.append(lap)
        windows.append((start, end))
        total += timer
        if not previous_active:
            blocks += 1
        previous_active = True
    if not chosen:
        return ActiveLaps((), None, (), 0, NO_WORKOUT_STEPS)
    return ActiveLaps(tuple(chosen), total, tuple(windows), blocks, None)


def active_block(
    laps: Iterable[Mapping[str, Any]],
    steps: Iterable[Mapping[str, Any]],
    segment_rows: Iterable[Mapping[str, Any]],
) -> ActiveBlock:
    """``active_laps`` and the active GAP pace over F013's counted segments in the active windows.

    ``segment_rows`` is ``session_features.segment_rows``' stream (counted segments only), on the
    same time origin as the laps. A segment is in the block when its ``t_start`` lies in a window
    ``[start_t, end_t)``, and then counts there in full: a moving dropout that starts inside an active
    lap contributes all its ``dt`` and metres even when it ends after the lap, and one that starts
    outside every window contributes nothing. Over those segments, with ``D`` their contributed
    metres:

    - the pace is ``1000 * sum(dt) / sum(contributed_m * g)``, F013's whole-session formula
      restricted to the windows; ``None`` when that denominator is 0 or the result is not finite;
    - ``gap_coverage`` is the graded share of ``D`` and ``gps_degraded_fraction`` the share whose
      start record carries ``gps_degraded``; both ``None`` when ``D`` is 0.

    When ``active_laps`` sets a reason, it is carried and every number is ``None``.
    """
    decided = active_laps(laps, steps)
    if decided.unavailable is not None:
        return ActiveBlock(None, None, 0, (), None, None, decided.unavailable)
    windows = decided.windows
    seconds = 0.0
    distance = 0.0
    gap_distance = 0.0
    graded_m = 0.0
    degraded_m = 0.0
    for row in segment_rows:
        t_start = row["t_start"]
        if not any(start <= t_start < end for start, end in windows):
            continue
        metres = row["contributed_m"]
        seconds += row["dt"]
        distance += metres
        gap_distance += metres * row["g"]
        if row["graded"]:
            graded_m += metres
        if row["gps_degraded"]:
            degraded_m += metres
    pace = 1000.0 * seconds / gap_distance if gap_distance > 0.0 else None
    if pace is not None and not math.isfinite(pace):
        pace = None
    return ActiveBlock(
        active_time_s=decided.active_time_s,
        gap_pace_s_per_km=pace,
        active_blocks=decided.active_blocks,
        windows=windows,
        gap_coverage=graded_m / distance if distance > 0.0 else None,
        gps_degraded_fraction=degraded_m / distance if distance > 0.0 else None,
        unavailable=None,
    )
