"""The individual endurance exponent (Riegel b) from tagged races (F029, spec/04 section 4.2.6): the
screen and the fit.

**Pure, by design**, on the pattern of ``metrics/threshold_pace.py``: ``estimate`` takes plain
candidate rows, an instant and the athlete's fitness by race date, and returns a frozen dataclass;
it imports neither ``config`` nor ``db`` nor ``fastapi``. Rows are read **by key**, so a
``sqlite3.Row``-derived dict and a plain dict are interchangeable:

- ``session_id`` and ``start_time`` (an aware ``datetime`` or an aware ISO 8601 string; a naive
  value is a defect of the writer and raises, as ``hrv_trend.local_day`` does);
- ``sport`` and ``activity_tag`` when the reader carries them (a row whose ``sport`` is not
  ``running`` or whose ``activity_tag`` is not ``race`` is in neither list; a row without the key
  is the reader's race-tagged running session);
- ``admitted_as_workout``: ``True`` when the reader admitted the race-tagged session as a threshold
  workout by its name, with ``workout_name`` carried onto the exclusion when given;
- ``timer_time_s``, and ``duration_s`` or the raw ``summary`` (a JSON object as text, or a decoded
  mapping) whose ``duration_s`` and ``distance_m`` are read;
- ``quality_flags``: the session's stored flags, a JSON list as text or a decoded list (``None``
  reads as none);
- ``features``, where ``needs_features`` was true: F013's ``gap_avg_pace_s_per_km``,
  ``gap_coverage``, ``gps_degraded_fraction`` and ``flags`` as a mapping, or ``None`` when they did
  not compute.

**Fitness.** ``fitness`` is a mapping, or a callable, from a race's local date (its ``start_time``
in ``zone``) to ``(ctl_before, ctl_earlier, provisional, after_gap)``: the F018 chart's CTL on the
local day before the race and on the day ``FITNESS_LOOKBACK_DAYS + 1`` days before it, and the
day-before day's two flags. A figure is ``None`` where its day is before the chart's ``first_day``;
a date the mapping lacks, or ``None`` from the callable, reads as all four null. The race day's own
CTL is never read here (it includes the race's load).

**Which races qualify** (F029 reference section 2), checked in this order, the first that fails
being the reason. A row after the instant is in neither list, though its ``start_time`` is still
read, so a naive one raises there too:

1. ``admitted_as_workout`` is not true, else ``admitted_as_workout``.
2. The age ``(instant - start_time)`` in fractional days is under ``WINDOW_DAYS``, else
   ``outside_window``.
3. The duration ``T`` is ``timer_time_s``, or ``duration_s`` (the row's, else the summary's) when
   ``timer_time_s`` is ``None``: a real, finite number > 0 (a bool or a string is refused), else
   ``duration_unavailable``. A summary that is read and does not decode to a JSON object is
   ``summary_unreadable``.
4. The stored flags hold neither ``distance_regressed`` nor ``smart_recording``, in that order;
   flags that do not decode to a list are ``quality_flags_unreadable``.
5. The fitness screen: with both figures, ``ctl_before >= (1 - FITNESS_DROP) * ctl_earlier``, else
   ``fitness_dropped``; with either null it passes.
6. ``features`` is a mapping with a list of flags, else ``features_unavailable``; its flags hold
   none of ``distance_regressed``, ``smart_recording``, ``grade_clamped``, in that order.
7. ``gap_avg_pace_s_per_km`` is a real, finite number > 0, else ``gap_pace_unavailable``.

``needs_features`` answers rows 1 to 5 from the stored values alone, so the reader computes F013's
features only for a race that can still be used. Degraded GPS and a GAP coverage below 1 exclude
nothing; both are carried on the effort.

**The fit** (reference section 3): ``D_eq = 1000 * T / gap_pace``; weight ``w = min(1, (WINDOW_DAYS
- age) / FADE_DAYS)``; weighted least squares of ``ln T`` on ``ln D_eq`` by centred weighted sums;
residuals served as ``exp(r) - 1``. A fading race counts by its weight: the count is the effective
number of races ``n_eff = (sum w)^2 / sum(w^2)`` (``n`` exactly when every weight is 1);
``se = sqrt(sum(w r^2) / (n_eff - 2) / sum(w (x - x_w)^2))``, the weighted standard error with the
weights rescaled to sum to ``n_eff`` (the scale cancels); the 80 % interval ``b +- t se`` with
Student's t at ``floor(n_eff) - 2`` degrees of freedom (``T_TABLE``, ``Z`` above 30). The bar,
``n_eff >= MIN_RACES`` over the races with ``w > 0`` and ``max D_eq / min D_eq >= MIN_SPREAD``
among them, is checked before any division (both read ``n_eff`` with ``COUNT_SLACK``); with
exactly three races any weight under 1 leaves ``n_eff`` under 3. Row 2 already excludes a race at
weight 0, so every race used has ``w > 0``. Below the bar the population ``POPULATION_B`` with
``POPULATION_ERROR`` and ``Z`` stands, ``low``, provisional, every race listed with a null residual.
Labels compare ``se`` with ``HIGH_MAX_ERROR`` and ``MEDIUM_MAX_ERROR``, inclusive on the better side;
``below_one`` marks a fitted b under 1 and changes no label. Any ``D_eq``, b, se, interval end or
residual that is not finite makes the estimate unavailable, ``estimate_not_finite``, with no
population substitution.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, tzinfo
from statistics import NormalDist
from types import MappingProxyType
from typing import Any

from runcoach_api.metrics import finite as _finite_screen

WINDOW_DAYS = 1095
"""A race counts while its age in fractional days is under this (reference section 1)."""
FADE_DAYS = 182
"""A race's weight falls from 1 to 0 over the last this many days of the window."""
MIN_RACES = 3
MIN_SPREAD = 2.0
FITNESS_DROP = 0.30
FITNESS_LOOKBACK_DAYS = 28
POPULATION_B = 1.06
POPULATION_ERROR = 0.05
COVERAGE = 0.80
HIGH_MAX_ERROR = 0.02
MEDIUM_MAX_ERROR = 0.04
SECONDS_PER_DAY = 86400.0
COUNT_SLACK = 1e-9
"""The bar and the t multiplier's floor read the effective count with this slack. Weights of 1 give
a whole count exactly, but n equal weights under 1 can come out an ulp under n as a ratio of float
sums (three races at 913.008 days: 2.9999999999999996), which would drop them below the bar or a
degree of freedom. The slack also counts any count within 1e-9 under a whole number as that number:
with three races, one within about 4e-5 of weight 1, the first ten minutes of its fade."""

Z = 1.2815515655446004
"""The 0.9 quantile of the standard normal, as ``threshold_pace.Z``: the multiplier above 30
degrees of freedom and for the population interval."""
assert abs(Z - NormalDist().inv_cdf(0.5 + COVERAGE / 2)) < 1e-12, (Z, COVERAGE)

T_TABLE: Mapping[int, float] = MappingProxyType(
    {
        1: 3.077683537175,
        2: 1.885618083164,
        3: 1.637744353696,
        4: 1.533206274059,
        5: 1.475884048824,
        6: 1.439755747265,
        7: 1.414923927651,
        8: 1.396815309744,
        9: 1.383028738397,
        10: 1.372183641110,
        11: 1.363430318021,
        12: 1.356217334023,
        13: 1.350171288780,
        14: 1.345030374455,
        15: 1.340605607850,
        16: 1.336757167327,
        17: 1.333379389722,
        18: 1.330390943570,
        19: 1.327728209027,
        20: 1.325340706985,
        21: 1.323187873865,
        22: 1.321236741613,
        23: 1.319460239816,
        24: 1.317835933673,
        25: 1.316345072674,
        26: 1.314971864271,
        27: 1.313702912829,
        28: 1.312526781593,
        29: 1.311433647302,
        30: 1.310415025391,
    }
)
"""Student's t at 0.90 (the 80 % two-sided interval's half-width) for 1 to 30 degrees of freedom,
to 12 decimals; the reference section 1 lists them to 6. The df 1 entry is ``tan(0.4 pi)`` in
closed form, which the assert below ties it to."""
assert abs(T_TABLE[1] - math.tan(0.4 * math.pi)) < 1e-12, T_TABLE[1]

PARAMETERS: Mapping[str, float | int] = MappingProxyType(
    {
        "window_days": WINDOW_DAYS,
        "fade_days": FADE_DAYS,
        "min_races": MIN_RACES,
        "min_spread": MIN_SPREAD,
        "fitness_drop": FITNESS_DROP,
        "fitness_lookback_days": FITNESS_LOOKBACK_DAYS,
        "population_b": POPULATION_B,
        "population_error": POPULATION_ERROR,
        "coverage": COVERAGE,
        "high_max_error": HIGH_MAX_ERROR,
        "medium_max_error": MEDIUM_MAX_ERROR,
    }
)
"""The F029 reference section 1 keys and values, served as the response's ``parameters``."""

RUNNING = "running"
RACE = "race"
STORED_FLAG_ORDER = ("distance_regressed", "smart_recording")
FEATURE_FLAG_ORDER = ("distance_regressed", "smart_recording", "grade_clamped")
ADMITTED_AS_WORKOUT = "admitted_as_workout"
OUTSIDE_WINDOW = "outside_window"
DURATION_UNAVAILABLE = "duration_unavailable"
SUMMARY_UNREADABLE = "summary_unreadable"
QUALITY_FLAGS_UNREADABLE = "quality_flags_unreadable"
FITNESS_DROPPED = "fitness_dropped"
FEATURES_UNAVAILABLE = "features_unavailable"
GAP_PACE_UNAVAILABLE = "gap_pace_unavailable"
ESTIMATE_NOT_FINITE = "estimate_not_finite"

Fitness = Mapping[date, Any] | Callable[[date], Any]


@dataclass(frozen=True)
class Effort:
    """A race used by the fit, with its weight, its fitness figures and its residual
    (``exp(r) - 1``, positive slower than the line; ``None`` below the bar or when unavailable)."""

    session_id: str
    start_time: datetime
    duration_s: float
    distance_m: float | None
    gap_pace_s_per_km: float
    gap_equivalent_m: float
    gap_coverage: Any
    gps_degraded_fraction: Any
    weight: float
    fitness_before: float | None
    fitness_earlier: float | None
    fitness_drop: float | None
    fitness_provisional: bool | None
    fitness_after_gap: bool | None
    residual: float | None


@dataclass(frozen=True)
class Exclusion:
    """A candidate at or before the instant that was not used, and why; the three fitness figures
    are set for ``fitness_dropped`` only, ``workout_name`` for ``admitted_as_workout`` when given."""

    session_id: str
    start_time: datetime
    reason: str
    fitness_before: float | None = None
    fitness_earlier: float | None = None
    fitness_drop: float | None = None
    workout_name: str | None = None


@dataclass(frozen=True)
class RiegelEstimate:
    """b at ``as_of``; ``value``, the interval, ``error``, ``t_multiplier`` and ``confidence`` are
    ``None`` when ``unavailable`` is set."""

    as_of: datetime
    efforts: tuple[Effort, ...]
    excluded: tuple[Exclusion, ...]
    value: float | None
    interval_low: float | None
    interval_high: float | None
    error: float | None
    t_multiplier: float | None
    confidence: str | None
    provisional: bool
    population_default: bool
    below_one: bool
    unavailable: str | None
    parameters: Mapping[str, float | int] = field(default_factory=lambda: dict(PARAMETERS))


@dataclass(frozen=True)
class _Stored:
    """What rows 1 to 5 read from one candidate: its reason when it failed, else its figures."""

    session_id: str
    start: datetime
    reason: str | None
    weight: float = 0.0
    duration: float = 0.0
    distance: float | None = None
    fitness: tuple[float | None, float | None, bool | None, bool | None] = (None, None, None, None)
    drop: float | None = None


def _aware(value: Any, what: str) -> datetime:
    instant = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
        raise ValueError(f"{what} is naive ({value!r}); an instant without a zone cannot be aged or dated")
    return instant


def resolve_instant(as_of: datetime | date, zone: tzinfo | None = None) -> tuple[datetime, tzinfo]:
    """The aware instant ``as_of`` names and the zone races are dated in: a ``date`` is the last
    instant of that local day in ``zone`` (required then); a ``datetime`` must be aware, and
    ``zone`` defaults to its own."""
    if isinstance(as_of, datetime):
        instant = _aware(as_of, "as_of")
        return instant, zone if zone is not None else instant.tzinfo  # type: ignore[return-value]
    if zone is None:
        raise ValueError("as_of given as a date needs the zone its local day is in")
    return datetime.combine(as_of, time.max, tzinfo=zone), zone


def _real(value: Any) -> float | None:
    return _finite_screen.finite(value)


def _decoded(value: Any) -> tuple[bool, Any]:
    """``(readable, value)``: text or bytes decoded as JSON, anything else as given."""
    if isinstance(value, str | bytes | bytearray):
        try:
            return True, json.loads(value)
        except (ValueError, UnicodeDecodeError):
            return False, None
    return True, value


def _summary(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The row's summary as a mapping, ``{}`` when absent; ``None`` when it does not read."""
    readable, summary = _decoded(row.get("summary"))
    if not readable:
        return None
    if summary is None:
        return {}
    return summary if isinstance(summary, Mapping) else None


def weight_at(age_days: float) -> float:
    """``min(1, (WINDOW_DAYS - age) / FADE_DAYS)``; 0 at or past the window."""
    if not age_days < WINDOW_DAYS:
        return 0.0
    return min(1.0, (WINDOW_DAYS - age_days) / FADE_DAYS)


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _fitness_of(
    fitness: Fitness | None, race_date: date
) -> tuple[float | None, float | None, bool | None, bool | None]:
    if fitness is None:
        entry = None
    elif callable(fitness):
        entry = fitness(race_date)
    else:
        entry = fitness.get(race_date)
    if entry is None:
        return None, None, None, None
    before, earlier, provisional, after_gap = entry
    return _real(before), _real(earlier), _flag(provisional), _flag(after_gap)


def _drop(before: float | None, earlier: float | None) -> float | None:
    """The fall ``1 - before / earlier``, ``None`` without both figures or with ``earlier <= 0``."""
    if before is None or earlier is None or earlier <= 0:
        return None
    return 1 - before / earlier


def _is_candidate(row: Mapping[str, Any]) -> bool:
    return row.get("sport", RUNNING) == RUNNING and row.get("activity_tag", RACE) == RACE


def _screen_stored(
    row: Mapping[str, Any], instant: datetime, zone: tzinfo, fitness: Fitness | None
) -> _Stored:
    """Rows 1 to 5 of reference section 2 from the stored values and the fitness alone."""
    session_id = str(row["session_id"])
    start = _aware(row["start_time"], f"session {session_id!r} start_time")
    if row.get("admitted_as_workout") is True:
        return _Stored(session_id, start, ADMITTED_AS_WORKOUT)
    age = (instant - start).total_seconds() / SECONDS_PER_DAY
    weight = weight_at(age)
    if not weight > 0:
        return _Stored(session_id, start, OUTSIDE_WINDOW)
    summary = _summary(row) if "summary" in row else {}
    timer = row.get("timer_time_s")
    if timer is None:
        raw = row.get("duration_s")
        if raw is None:
            if summary is None:
                return _Stored(session_id, start, SUMMARY_UNREADABLE)
            raw = summary.get("duration_s")
        duration = _real(raw)
    else:
        duration = _real(timer)
    if duration is None or not duration > 0:
        return _Stored(session_id, start, DURATION_UNAVAILABLE)
    readable, flags = _decoded(row.get("quality_flags"))
    if not readable or not (flags is None or isinstance(flags, list)):
        return _Stored(session_id, start, QUALITY_FLAGS_UNREADABLE)
    stored = flags or []
    flagged = next((flag for flag in STORED_FLAG_ORDER if flag in stored), None)
    if flagged is not None:
        return _Stored(session_id, start, flagged)
    figures = _fitness_of(fitness, start.astimezone(zone).date())
    before, earlier = figures[0], figures[1]
    drop = _drop(before, earlier)
    distance = _real(row["distance_m"]) if "distance_m" in row else _real((summary or {}).get("distance_m"))
    if before is not None and earlier is not None and before < (1 - FITNESS_DROP) * earlier:
        return _Stored(session_id, start, FITNESS_DROPPED, weight, duration, distance, figures, drop)
    return _Stored(session_id, start, None, weight, duration, distance, figures, drop)


def needs_features(
    row: Mapping[str, Any], instant: datetime | date, fitness: Fitness | None, zone: tzinfo | None = None
) -> bool:
    """Whether ``row`` passes reference section 2 rows 1 to 5, so its F013 features decide it. False
    for a row after the instant and for a row that is no candidate."""
    instant, zone = resolve_instant(instant, zone)
    if not _is_candidate(row):
        return False
    stored = _screen_stored(row, instant, zone, fitness)
    return stored.start <= instant and stored.reason is None


def _label(error: float) -> str:
    if error <= HIGH_MAX_ERROR:
        return "high"
    if error <= MEDIUM_MAX_ERROR:
        return "medium"
    return "low"


def effective_count(weights: Iterable[float]) -> float:
    """The effective number of races, ``(sum w)^2 / sum(w^2)``; 0 for none. ``n`` weights of 1
    give ``n`` exactly, since the sums of ones are exact."""
    weights = list(weights)
    squares = sum(w * w for w in weights)
    return sum(weights) ** 2 / squares if squares > 0 else 0.0


def t_multiplier(df: int) -> float:
    """Student's t at 0.90 for ``df`` degrees of freedom: ``T_TABLE`` to 30, ``Z`` above."""
    return T_TABLE[df] if df <= 30 else Z


def _all_finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def estimate(
    rows: Iterable[Mapping[str, Any]],
    instant: datetime | date,
    fitness: Fitness | None,
    zone: tzinfo | None = None,
) -> RiegelEstimate:
    """Screen ``rows`` (reference section 2) and fit b at ``instant`` (section 3)."""
    instant, zone = resolve_instant(instant, zone)
    candidates: list[tuple[_Stored, Mapping[str, Any]]] = []
    for row in rows:
        if not _is_candidate(row):
            continue
        stored = _screen_stored(row, instant, zone, fitness)
        if stored.start > instant:
            continue
        candidates.append((stored, row))
    candidates.sort(key=lambda item: (item[0].start, item[0].session_id))

    used: list[tuple[_Stored, Mapping[str, Any], float, float]] = []
    excluded: list[Exclusion] = []
    for stored, row in candidates:
        reason = stored.reason
        pace: float | None = None
        features: Any = None
        if reason is None:
            features = row.get("features")
            flags = features.get("flags") if isinstance(features, Mapping) else None
            if not isinstance(flags, list | tuple):
                reason = FEATURES_UNAVAILABLE
            else:
                reason = next((flag for flag in FEATURE_FLAG_ORDER if flag in flags), None)
        if reason is None:
            pace = _real(features.get("gap_avg_pace_s_per_km"))
            if pace is None or not pace > 0:
                reason = GAP_PACE_UNAVAILABLE
        if reason is not None:
            dropped = reason == FITNESS_DROPPED
            excluded.append(
                Exclusion(
                    session_id=stored.session_id,
                    start_time=stored.start,
                    reason=reason,
                    fitness_before=stored.fitness[0] if dropped else None,
                    fitness_earlier=stored.fitness[1] if dropped else None,
                    fitness_drop=stored.drop if dropped else None,
                    workout_name=row.get("workout_name") if reason == ADMITTED_AS_WORKOUT else None,
                )
            )
            continue
        assert pace is not None
        used.append((stored, features, pace, 1000 * stored.duration / pace))

    def efforts(residuals: list[float | None]) -> tuple[Effort, ...]:
        return tuple(
            Effort(
                session_id=stored.session_id,
                start_time=stored.start,
                duration_s=stored.duration,
                distance_m=stored.distance,
                gap_pace_s_per_km=pace,
                gap_equivalent_m=d_eq,
                gap_coverage=features.get("gap_coverage"),
                gps_degraded_fraction=features.get("gps_degraded_fraction"),
                weight=stored.weight,
                fitness_before=stored.fitness[0],
                fitness_earlier=stored.fitness[1],
                fitness_drop=stored.drop,
                fitness_provisional=stored.fitness[2],
                fitness_after_gap=stored.fitness[3],
                residual=residual,
            )
            for (stored, features, pace, d_eq), residual in zip(used, residuals, strict=True)
        )

    def unavailable() -> RiegelEstimate:
        return RiegelEstimate(
            as_of=instant,
            efforts=efforts([None] * len(used)),
            excluded=tuple(excluded),
            value=None,
            interval_low=None,
            interval_high=None,
            error=None,
            t_multiplier=None,
            confidence=None,
            provisional=False,
            population_default=False,
            below_one=False,
            unavailable=ESTIMATE_NOT_FINITE,
        )

    distances = [d_eq for _stored, _features, _pace, d_eq in used]
    if not _all_finite(*distances) or not all(d_eq > 0 for d_eq in distances):
        return unavailable()  # an underflow to 0 has no finite logarithm either
    weighted = [item for item in used if item[0].weight > 0]
    served = [d_eq for _stored, _features, _pace, d_eq in weighted]
    n_eff = effective_count([stored.weight for stored, _features, _pace, _d_eq in weighted])
    if n_eff + COUNT_SLACK < MIN_RACES or max(served) / min(served) < MIN_SPREAD:
        return RiegelEstimate(
            as_of=instant,
            efforts=efforts([None] * len(used)),
            excluded=tuple(excluded),
            value=POPULATION_B,
            interval_low=POPULATION_B - Z * POPULATION_ERROR,
            interval_high=POPULATION_B + Z * POPULATION_ERROR,
            error=POPULATION_ERROR,
            t_multiplier=Z,
            confidence="low",
            provisional=True,
            population_default=True,
            below_one=False,
            unavailable=None,
        )

    w = [stored.weight for stored, _features, _pace, _d_eq in used]
    x = [math.log(d_eq) for d_eq in distances]
    y = [math.log(stored.duration) for stored, _features, _pace, _d_eq in used]
    total = sum(w)
    x_w = sum(wi * xi for wi, xi in zip(w, x, strict=True)) / total
    y_w = sum(wi * yi for wi, yi in zip(w, y, strict=True)) / total
    sxx = sum(wi * (xi - x_w) ** 2 for wi, xi in zip(w, x, strict=True))
    sxy = sum(wi * (xi - x_w) * (yi - y_w) for wi, xi, yi in zip(w, x, y, strict=True))
    b = sxy / sxx
    r = [yi - (y_w + b * (xi - x_w)) for xi, yi in zip(x, y, strict=True)]
    # The weighted standard error over the weights rescaled to sum to n_eff, w' = w * n_eff / sum(w):
    # sum(w' r^2) and sum(w' (x - x_w)^2) both carry the factor n_eff / sum(w), which cancels, so
    # se = sqrt(sum(w r^2) / (n_eff - 2) / sxx); b and the residuals do not move with the scale.
    error = math.sqrt(sum(wi * ri * ri for wi, ri in zip(w, r, strict=True)) / (n_eff - 2) / sxx)
    t = t_multiplier(math.floor(n_eff + COUNT_SLACK) - 2)  # the bar's own reading, so at least 1
    low, high = b - t * error, b + t * error
    try:
        residuals = [math.expm1(ri) for ri in r]
    except OverflowError:
        return unavailable()
    if not _all_finite(b, error, low, high, *residuals):
        return unavailable()
    return RiegelEstimate(
        as_of=instant,
        efforts=efforts(list(residuals)),
        excluded=tuple(excluded),
        value=b,
        interval_low=low,
        interval_high=high,
        error=error,
        t_multiplier=t,
        confidence=_label(error),
        provisional=False,
        population_default=False,
        below_one=b < 1,
        unavailable=None,
    )
