"""Critical speed (CS) and D' from the best GAP stretches of the last 90 days of runs (F033,
reference sections 1, 2, 4 and 5): the screen and the fit.

**Pure, by design**, on the pattern of ``metrics/riegel_b.py``: ``estimate`` takes plain rows and an
instant and returns a frozen dataclass; it imports neither ``config`` nor ``db`` nor ``fastapi``.
Rows are read **by key** (``db._read_cs_rows`` writes them; tests build plain dicts):
``session_id``, ``start_time`` (an aware ``datetime`` or an aware ISO 8601 string), ``sport``,
``activity_tag``, ``name``, ``timer_time_s``, ``duration_s`` and ``summary`` (the summary's
duration), ``quality_flags`` (the stored flags, a JSON list as text or a decoded list; ``None``
reads as none), ``admitted_as_workout``, ``unreadable`` (``quality_flags_unreadable``,
``features_unavailable`` or ``None``), and, once the records are read, ``flags`` (F013's) and
``segments`` (``session_features.segment_rows``' stream).

**The screen** (reference section 2). A row whose ``start_time`` is not an aware instant cannot be
placed in the window and is skipped: never listed, never read. The window gates listing for every
reason: a row is listed only when its age ``(instant - start).total_seconds() / 86400`` satisfies
``0 <= age < WINDOW_DAYS``, elapsed time measured on the instant (never ``instant - 90 days``,
which overflows for an instant on the calendar's first day and is wall-clock across DST). Inside
it, the first failing step names the exclusion:

1. ``not_running``: ``sport`` is not ``running`` (a row without the key is a running session);
2. ``declared_capture``: the tag is one of ``session_load.DECLARED_CAPTURE_TAGS``;
3. ``quality_flags_unreadable``: the stored flags are not a JSON list (or ``unreadable`` says so);
4. ``distance_regressed``, then 5. ``smart_recording``, in the stored flags (source ``stored``);
6. ``features_unavailable``: ``unreadable`` says so, F013's ``flags`` are not a list, or the
   ``segments`` are not a list of segments ``gap_stretches.best_stretches`` can read;
7. ``distance_regressed``, ``smart_recording``, ``grade_clamped`` in F013's flags (source ``f013``).

``needs_records`` answers steps 1 to 5 from the stored values alone (it reads no ``flags`` and no
``segments``), so the reader reads a session's records only when they can decide it. It never
raises: it runs inside the ``GET /state`` snapshot, where an exception is a 500 for the response.

**A run's best stretches** come from ``gap_stretches.best_stretches`` over the row's segments.

**The fit** (``fit``, reference section 4), over one ``RunBests`` per candidate:

1. One run per length, from the longest length down: the run with the largest best stretch at that
   length among runs not yet used; ties to the later start, then the larger ``session_id``. A
   stretch whose distance is not a finite number ranks first at its length, so it is served, not
   stepped around.
2. Shape check: a stretch is dropped, ``beaten_by_longer``, when a longer stretch (dropped or kept)
   has a higher average speed; ``beaten_by_s`` is the shortest such length. Speeds are compared by
   cross-multiplication, so nothing is divided here.
3. Any served stretch whose distance is not a finite number above 0: ``estimate_not_finite`` (no
   pace can be read from it).
4. Spread: fewer than ``MIN_STRETCHES`` kept is ``too_few_stretches``; the shortest kept over
   ``MAX_SHORTEST_S`` or the longest kept under ``MIN_LONGEST_S`` is ``spread_too_narrow``. No
   candidate run at all is ``no_candidate_runs``.
5. Far off: the excess ``p_long - p_prev * (L_long / L_prev) ** ((b - 1) / b)`` at b
   ``POPULATION_B``, paces in s/km, over the two longest kept stretches; over ``FAR_OFF_S_PER_KM``
   is ``longest_far_off``, the excess served (exactly 15 passes).
6. The line: ordinary least squares of distance on time over the kept stretches by centred sums;
   CS the slope (m/s), D' the intercept (m); ``s2 = SSR / (n - 2)``, ``se = sqrt(s2 / Sxx)``,
   ``se_D = sqrt(s2 * (1/n + mean_t^2 / Sxx))``, t from ``riegel_b.t_multiplier(n - 2)``. The CS
   interval ``CS +/- t se`` on speed is served as pace with the fast end ``low``; D' ``+/- t se_D``.
7. The label: kept stretches whose run is tagged ``race``, is not admitted as a workout by a listed
   name and lasted ``SHORT_RACE_MAX_S`` or less (``timer_time_s``, else the summary's
   ``duration_s``; a real, finite number above 0, else unknown and not counted): 0 ``low``, 1
   ``medium``, 2 or more ``high``. The fit error is not an input.
8. CS, D', se, se_D, an interval end or a pace not finite, CS not positive, or the slow speed bound
   ``CS - t se`` at or below 0: ``estimate_not_finite``. D' at or below 0 is served as computed
   with ``d_prime_not_positive``.

Every unavailable result still carries its stretches and exclusions; no population CS stands in.
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from types import MappingProxyType
from typing import Any

from runcoach_api.metrics import gap_stretches, riegel_b
from runcoach_api.metrics.finite import finite
from runcoach_api.metrics.gap_stretches import BestStretch
from runcoach_api.metrics.session_load import DECLARED_CAPTURE_TAGS

WINDOW_DAYS = 90
STRETCH_LENGTHS_S = gap_stretches.STRETCH_LENGTHS_S
PAUSE_BREAK_S = gap_stretches.PAUSE_BREAK_S
POPULATION_B = 1.06
FAR_OFF_S_PER_KM = 15
MIN_STRETCHES = 3
MAX_SHORTEST_S = 300
MIN_LONGEST_S = 720
SHORT_RACE_MAX_S = 1800
COVERAGE = 0.80
SECONDS_PER_DAY = 86400.0
EXPONENT = (POPULATION_B - 1) / POPULATION_B
"""``E = (b - 1) / b``: a pace at length L predicts ``p * (L' / L) ** E`` at L'."""
assert COVERAGE == riegel_b.COVERAGE, (COVERAGE, riegel_b.COVERAGE)  # t is riegel_b's 0.90 quantile

PARAMETERS: Mapping[str, Any] = MappingProxyType(
    {
        "window_days": WINDOW_DAYS,
        "stretch_lengths_s": STRETCH_LENGTHS_S,
        "pause_break_s": PAUSE_BREAK_S,
        "population_b": POPULATION_B,
        "far_off_s_per_km": FAR_OFF_S_PER_KM,
        "min_stretches": MIN_STRETCHES,
        "max_shortest_s": MAX_SHORTEST_S,
        "min_longest_s": MIN_LONGEST_S,
        "short_race_max_s": SHORT_RACE_MAX_S,
        "coverage": COVERAGE,
    }
)
"""The reference section 1 keys and values, served as the response's ``parameters``."""

RUNNING = "running"
RACE = "race"
NOT_RUNNING = "not_running"
DECLARED_CAPTURE = "declared_capture"
QUALITY_FLAGS_UNREADABLE = "quality_flags_unreadable"
FEATURES_UNAVAILABLE = "features_unavailable"
STORED_FLAG_ORDER = ("distance_regressed", "smart_recording")
FEATURE_FLAG_ORDER = ("distance_regressed", "smart_recording", "grade_clamped")
STORED = "stored"
F013 = "f013"

BEATEN_BY_LONGER = "beaten_by_longer"

NO_CANDIDATE_RUNS = "no_candidate_runs"
TOO_FEW_STRETCHES = "too_few_stretches"
SPREAD_TOO_NARROW = "spread_too_narrow"
LONGEST_FAR_OFF = "longest_far_off"
ESTIMATE_NOT_FINITE = "estimate_not_finite"

LABELS = ("low", "medium", "high")


@dataclass(frozen=True)
class Exclusion:
    """A session in the window that is not a candidate, and why; ``source`` is ``stored`` or
    ``f013`` for a flag reason, else ``None``."""

    session_id: str
    start_time: datetime
    reason: str
    source: str | None = None


@dataclass(frozen=True)
class RunBests:
    """A candidate run and its best stretch at each length (any positive length), keyed by length.
    ``duration_s`` is the label's duration (``None`` when unknown)."""

    session_id: str
    start_time: datetime
    activity_tag: Any
    name: Any
    duration_s: float | None
    admitted_as_workout: bool
    bests: Mapping[float, BestStretch]


@dataclass(frozen=True)
class Stretch:
    """A served stretch: one run's best at one length, kept (``drop_reason`` ``None``) or dropped
    with the shortest longer, faster length that beat it. ``pace_s_per_km`` is ``None`` when the
    distance is not a finite number above 0."""

    length_s: float
    distance_m: float
    pace_s_per_km: float | None
    session_id: str
    start_time: datetime
    activity_tag: Any
    run_name: Any
    run_duration_s: float | None
    drop_reason: str | None = None
    beaten_by_s: float | None = None


@dataclass(frozen=True)
class CriticalSpeedEstimate:
    """CS and D' at ``as_of``. Every figure is ``None`` when ``unavailable`` is set, except
    ``far_off_excess_s_per_km``, which is served with ``longest_far_off``."""

    as_of: Any
    stretches: tuple[Stretch, ...]
    excluded: tuple[Exclusion, ...]
    cs_m_per_s: float | None
    cs_s_per_km: float | None
    interval_low_s_per_km: float | None
    interval_high_s_per_km: float | None
    label: str | None
    d_prime_m: float | None
    d_prime_low_m: float | None
    d_prime_high_m: float | None
    d_prime_error_m: float | None
    d_prime_not_positive: bool | None
    error_m_per_s: float | None
    t_multiplier: float | None
    far_off_excess_s_per_km: float | None
    unavailable: str | None
    parameters: Mapping[str, Any] = field(default_factory=lambda: dict(PARAMETERS))


# ---------------------------------------------------------------------------- the screen


def _start(value: Any) -> datetime | None:
    """``value`` as an aware instant, ``None`` when it is not one."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    try:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return None
    except (ValueError, OverflowError):
        return None
    return value


def _in_window(start: datetime, instant: datetime) -> bool:
    age_days = (instant - start).total_seconds() / SECONDS_PER_DAY
    return 0 <= age_days < WINDOW_DAYS


def _decoded(value: Any) -> tuple[bool, Any]:
    """``(readable, value)``: text or bytes decoded as JSON, anything else as given."""
    if isinstance(value, str | bytes | bytearray):
        try:
            return True, json.loads(value)
        except ValueError:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
            return False, None
    return True, value


def _stored_reason(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Steps 1 to 5 of the screen from the stored values: ``(reason, source)``."""
    if row.get("sport", RUNNING) != RUNNING:
        return NOT_RUNNING, None
    tag = row.get("activity_tag")
    if isinstance(tag, str) and tag in DECLARED_CAPTURE_TAGS:
        return DECLARED_CAPTURE, None
    readable, flags = _decoded(row.get("quality_flags"))
    if row.get("unreadable") == QUALITY_FLAGS_UNREADABLE or not readable:
        return QUALITY_FLAGS_UNREADABLE, None
    if flags is None:
        flags = []
    if not isinstance(flags, list):
        return QUALITY_FLAGS_UNREADABLE, None
    for flag in STORED_FLAG_ORDER:
        if flag in flags:
            return flag, STORED
    return None, None


def needs_records(row: Mapping[str, Any], instant: datetime | date, zone: tzinfo | None = None) -> bool:
    """Whether ``row`` passes every stored step of the screen (window, sport, tag, stored flags),
    so its records decide it. Reads no ``flags`` and no ``segments``; never raises on a row."""
    instant, _zone = riegel_b.resolve_instant(instant, zone)
    start = _start(row.get("start_time"))
    if start is None or not _in_window(start, instant):
        return False
    return _stored_reason(row)[0] is None


def _duration(row: Mapping[str, Any]) -> float | None:
    """The label's duration: ``timer_time_s``, else the row's or the summary's ``duration_s``; a
    real, finite number above 0, else ``None``."""
    timer = row.get("timer_time_s")
    if timer is None:
        raw = row.get("duration_s")
        if raw is None:
            readable, summary = _decoded(row.get("summary"))
            raw = summary.get("duration_s") if readable and isinstance(summary, Mapping) else None
    else:
        raw = timer
    duration = finite(raw)
    return duration if duration is not None and duration > 0 else None


def _bests(segments: Any) -> dict[int, BestStretch] | None:
    """The run's best stretches, ``None`` when its segments cannot be read as a segment stream."""
    if not isinstance(segments, list | tuple) or not all(isinstance(s, Mapping) for s in segments):
        return None
    try:
        return gap_stretches.best_stretches(segments)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def estimate(
    rows: Iterable[Mapping[str, Any]], instant: datetime | date, zone: tzinfo | None = None
) -> CriticalSpeedEstimate:
    """Screen ``rows`` (reference section 2), take each candidate's best stretches (section 3)
    and fit CS and D' at ``instant`` (section 4)."""
    instant, _zone = riegel_b.resolve_instant(instant, zone)
    listed: list[tuple[datetime, str, Mapping[str, Any]]] = []
    for row in rows:
        start = _start(row.get("start_time"))
        if start is None or not _in_window(start, instant):
            continue
        listed.append((start, str(row.get("session_id")), row))
    listed.sort(key=lambda item: (item[0], item[1]))

    candidates: list[RunBests] = []
    excluded: list[Exclusion] = []
    for start, session_id, row in listed:
        reason, source = _stored_reason(row)
        bests: dict[int, BestStretch] | None = None
        if reason is None:
            flags = row.get("flags")
            if row.get("unreadable") == FEATURES_UNAVAILABLE or not isinstance(flags, list | tuple):
                reason = FEATURES_UNAVAILABLE
            else:
                flagged = next((flag for flag in FEATURE_FLAG_ORDER if flag in flags), None)
                if flagged is not None:
                    reason, source = flagged, F013
        if reason is None:
            bests = _bests(row.get("segments"))
            if bests is None:
                reason = FEATURES_UNAVAILABLE
        if reason is not None:
            excluded.append(Exclusion(session_id, start, reason, source))
            continue
        assert bests is not None
        candidates.append(
            RunBests(
                session_id=session_id,
                start_time=start,
                activity_tag=row.get("activity_tag"),
                name=row.get("name"),
                duration_s=_duration(row),
                admitted_as_workout=row.get("admitted_as_workout") is True,
                bests=bests,
            )
        )
    return dataclasses.replace(fit(candidates, instant), excluded=tuple(excluded))


# ---------------------------------------------------------------------------- the fit


def _usable(distance: float) -> bool:
    return math.isfinite(distance) and distance > 0


def _pace(length: float, distance: float) -> float | None:
    if not _usable(distance):
        return None
    pace = 1000.0 * length / distance
    return pace if math.isfinite(pace) else None


def _rank(run: RunBests, length: float) -> tuple:
    """The order runs are chosen in at ``length``: a non-finite distance first, then the larger
    distance, the later start, the larger ``session_id``."""
    distance = run.bests[length].distance_m
    if math.isfinite(distance):
        return (0, distance, run.start_time, run.session_id)
    return (1, 0.0, run.start_time, run.session_id)


def _counts_as_short_race(run: RunBests) -> bool:
    return (
        run.activity_tag == RACE
        and run.admitted_as_workout is not True
        and run.duration_s is not None
        and run.duration_s <= SHORT_RACE_MAX_S
    )


def fit(bests: Sequence[RunBests], instant: Any) -> CriticalSpeedEstimate:
    """Steps 1 to 8 of the module docstring over the candidates' best stretches."""
    lengths = sorted({length for run in bests for length in run.bests}, reverse=True)
    used: set[str] = set()
    chosen: list[tuple[float, BestStretch, RunBests]] = []
    for length in lengths:
        pool = [run for run in bests if run.session_id not in used and length in run.bests]
        if not pool:
            continue
        run = max(pool, key=lambda r: _rank(r, length))
        used.add(run.session_id)
        chosen.append((length, run.bests[length], run))
    chosen.sort(key=lambda item: item[0])

    stretches: list[Stretch] = []
    for k, (length, best, run) in enumerate(chosen):
        beaten_by = next(
            (
                longer
                for longer, other, _run in chosen[k + 1 :]
                # other / longer > best / length, cross-multiplied (both lengths positive)
                if other.distance_m * length > best.distance_m * longer
            ),
            None,
        )
        stretches.append(
            Stretch(
                length_s=length,
                distance_m=best.distance_m,
                pace_s_per_km=_pace(length, best.distance_m),
                session_id=run.session_id,
                start_time=run.start_time,
                activity_tag=run.activity_tag,
                run_name=run.name,
                run_duration_s=run.duration_s,
                drop_reason=None if beaten_by is None else BEATEN_BY_LONGER,
                beaten_by_s=beaten_by,
            )
        )
    kept = [
        (stretch, run)
        for stretch, (_l, _b, run) in zip(stretches, chosen, strict=True)
        if stretch.drop_reason is None
    ]

    def unavailable(reason: str, excess: float | None = None) -> CriticalSpeedEstimate:
        return CriticalSpeedEstimate(
            as_of=instant,
            stretches=tuple(stretches),
            excluded=(),
            cs_m_per_s=None,
            cs_s_per_km=None,
            interval_low_s_per_km=None,
            interval_high_s_per_km=None,
            label=None,
            d_prime_m=None,
            d_prime_low_m=None,
            d_prime_high_m=None,
            d_prime_error_m=None,
            d_prime_not_positive=None,
            error_m_per_s=None,
            t_multiplier=None,
            far_off_excess_s_per_km=excess,
            unavailable=reason,
        )

    if not bests:
        return unavailable(NO_CANDIDATE_RUNS)
    if not all(_usable(stretch.distance_m) for stretch in stretches):
        return unavailable(ESTIMATE_NOT_FINITE)
    if len(kept) < MIN_STRETCHES:
        return unavailable(TOO_FEW_STRETCHES)
    if kept[0][0].length_s > MAX_SHORTEST_S or kept[-1][0].length_s < MIN_LONGEST_S:
        return unavailable(SPREAD_TOO_NARROW)

    prev, longest = kept[-2][0], kept[-1][0]
    p_prev = 1000.0 * prev.length_s / prev.distance_m
    p_long = 1000.0 * longest.length_s / longest.distance_m
    excess = p_long - p_prev * (longest.length_s / prev.length_s) ** EXPONENT
    if not math.isfinite(excess):
        return unavailable(ESTIMATE_NOT_FINITE)
    if excess > FAR_OFF_S_PER_KM:
        return unavailable(LONGEST_FAR_OFF, excess)

    n = len(kept)
    xs = [float(stretch.length_s) for stretch, _run in kept]
    ys = [stretch.distance_m for stretch, _run in kept]
    mean_t = sum(xs) / n
    mean_d = sum(ys) / n
    sxx = sum((x - mean_t) * (x - mean_t) for x in xs)
    sxy = sum((x - mean_t) * (y - mean_d) for x, y in zip(xs, ys, strict=True))
    cs = sxy / sxx
    d_prime = mean_d - cs * mean_t
    residuals = [y - (cs * x + d_prime) for x, y in zip(xs, ys, strict=True)]
    s2 = sum(r * r for r in residuals) / (n - 2)
    se = math.sqrt(s2 / sxx)
    se_d = math.sqrt(s2 * (1 / n + mean_t * mean_t / sxx))
    t = riegel_b.t_multiplier(n - 2)
    fast, slow = cs + t * se, cs - t * se
    d_low, d_high = d_prime - t * se_d, d_prime + t * se_d
    figures = (cs, d_prime, se, se_d, fast, slow, d_low, d_high)
    if not all(math.isfinite(value) for value in figures) or not cs > 0 or not slow > 0:
        return unavailable(ESTIMATE_NOT_FINITE)
    pace, pace_low, pace_high = 1000.0 / cs, 1000.0 / fast, 1000.0 / slow
    if not all(math.isfinite(value) for value in (pace, pace_low, pace_high)):
        return unavailable(ESTIMATE_NOT_FINITE)
    races = sum(1 for _stretch, run in kept if _counts_as_short_race(run))
    return CriticalSpeedEstimate(
        as_of=instant,
        stretches=tuple(stretches),
        excluded=(),
        cs_m_per_s=cs,
        cs_s_per_km=pace,
        interval_low_s_per_km=pace_low,
        interval_high_s_per_km=pace_high,
        label=LABELS[min(races, 2)],
        d_prime_m=d_prime,
        d_prime_low_m=d_low,
        d_prime_high_m=d_high,
        d_prime_error_m=se_d,
        d_prime_not_positive=d_prime <= 0,
        error_m_per_s=se,
        t_multiplier=t,
        far_off_excess_s_per_km=excess,
        unavailable=None,
    )
