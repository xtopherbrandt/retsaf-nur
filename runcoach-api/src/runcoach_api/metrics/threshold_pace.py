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

1. ``sport`` is ``running`` and ``activity_tag`` is ``race``; otherwise the row is in neither
   ``efforts`` nor ``excluded``.
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

``short_races_only`` is true when every effort used lasted under ``SHORT_RACE_S`` (a 20-30 min race
reads threshold fast); it is a display warning and changes no number. With no qualifying effort the
result is unavailable, ``no_qualifying_effort``, and every number is ``None``.
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

PARAMETERS: Mapping[str, float | int] = MappingProxyType(
    {
        "typical_error": TYPICAL_ERROR,
        "doubling_days": DOUBLING_DAYS,
        "coverage": COVERAGE,
        "high_max_error": HIGH_MAX_ERROR,
        "medium_max_error": MEDIUM_MAX_ERROR,
        "short_race_s": SHORT_RACE_S,
    }
)
"""The reference section 1 keys and values, served as the response's ``parameters``."""

RUNNING = "running"
RACE = "race"
FLAG_ORDER = ("distance_regressed", "smart_recording")
OUTSIDE_THRESHOLD_BAND = "outside_threshold_band"
GAP_PACE_UNAVAILABLE = "gap_pace_unavailable"
NO_QUALIFYING_EFFORT = "no_qualifying_effort"


@dataclass(frozen=True)
class Effort:
    """A qualifying race used by the filter, with the gain ``K`` it entered with."""

    session_id: str
    start_time: datetime
    duration_s: float
    gap_pace_s_per_km: float
    gap_coverage: Any
    gps_degraded_fraction: Any
    gain: float


@dataclass(frozen=True)
class Exclusion:
    """A tagged running race at or before ``as_of`` that did not qualify, and why."""

    session_id: str
    start_time: datetime
    reason: str


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


def estimate(rows: Iterable[Mapping[str, Any]], as_of: datetime) -> ThresholdPace:
    """Qualify ``rows`` and replay the filter to ``as_of`` (an aware instant)."""
    as_of = _aware(as_of, "as_of")
    candidates: list[tuple[datetime, str, Mapping[str, Any]]] = []
    for row in rows:
        if row.get("sport") != RUNNING or row.get("activity_tag") != RACE:
            continue
        start = _aware(row["start_time"], f"session {row.get('session_id')!r} start_time")
        if start > as_of:
            continue
        candidates.append((start, str(row["session_id"]), row))
    candidates.sort(key=lambda item: (item[0], item[1]))

    used: list[tuple[datetime, str, Mapping[str, Any], float, float]] = []
    excluded: list[Exclusion] = []
    for start, session_id, row in candidates:
        duration = duration_of(row)
        if duration is None or not in_band(duration):
            excluded.append(Exclusion(session_id, start, OUTSIDE_THRESHOLD_BAND))
            continue
        flags = _flags(row)
        flagged = next((flag for flag in FLAG_ORDER if flag in flags), None)
        if flagged is not None:
            excluded.append(Exclusion(session_id, start, flagged))
            continue
        y = _log_speed(row.get("gap_avg_pace_s_per_km"))
        if y is None:
            excluded.append(Exclusion(session_id, start, GAP_PACE_UNAVAILABLE))
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
        short_races_only=all(effort.duration_s < SHORT_RACE_S for effort in efforts),
        unavailable=None,
    )
