"""The session time base (F013, spec/03 section 3.2): the finite screen, segments and recorded time.

Every F013 figure (the gradient window, NGP, the descriptors) is built on the
three things defined here, in this order:

1. **The finite screen** (``screen``), applied once at the module boundary. A
   value that is ``None``, ``nan``, ``inf``, ``-inf`` or an int beyond float
   range, or whose key the row does not carry, is *absent* (``None``) for every
   later rule; ``heart_rate <= 0`` is absent too. The screen is
   ``metrics.finite.finite`` rather than ``hrv_classification._numeric``, whose
   ratified HRV semantics belong to its own call sites. The one record ``screen`` *removes* rather than screens is a
   row whose ``t`` is missing or non-finite: a record with no place on the time
   axis cannot start or end a segment. Upload cannot deliver a non-finite value
   (SQLite stores ``nan`` as NULL; FIT altitude and HR are integer-encoded), so
   the screen is pinned at this seam with constructed records.
2. **Segments and the recorded-time base** (``build_time_base``). A *segment*
   is the interval between consecutive records ``k`` and ``k + 1``. It is
   **sampled** when ``0 < dt <= SEGMENT_MAX_DT_S``; it is a **break** (a pause
   or an unfilled gap) when ``dt > SEGMENT_MAX_DT_S``; a ``dt = 0`` segment
   (the resampler can emit duplicate instants) contributes nothing and is
   *not* a break. The duplicate record itself stays in ``records``. A break
   across which the device distance advanced at ``MOVING_GAP_MIN_SPEED_M_S``
   or more (``dd / dt``, both distances present, no length cap) is a **moving
   gap**: the runner kept going while the watch wrote nothing (IDEA-126,
   ruled 2026-10-10). A segment is **counted** when it is sampled or a moving
   gap; any other break contributes neither time nor distance. Recorded time
   ``T = sum(dt)`` and distance ``D = sum(max(dd, 0))`` run over counted
   segments only, so a 600 s standstill and a dt = 0 duplicate leave both
   unchanged while a 60 s dropout at running speed counts in full: the time
   base is recorded time, not elapsed time (F013 Decision Log, "recorded
   time"). A sampled segment whose distance decreases contributes 0 m and
   raises ``distance_regressed``; one with either distance absent contributes
   0 m and is still counted for time. A moving gap is still a break for the
   rules that split at a gap (NGP's blocks). The **reconstructed distance**
   ``s`` is the running sum of the contributions, monotone by construction, so
   the gradient window that walks it can never become non-contiguous.
3. **The shared ``Feature`` type**, ``{value, unavailable}`` with exactly one
   of the two set (reference section 7), which every descriptor returns.

**Pure, by design.** Nothing here imports ``config``, ``db`` or ``fastapi``,
reads a clock or opens a connection. Rows are read **by key**, so a
``sqlite3.Row`` and a plain dict are interchangeable; ``sample_quality``
arrives already JSON-decoded by the caller and keeps the caller's order.
Records are taken in the order given (``db`` returns them in ``t`` order), and
equal ``t`` keep their input order; nothing here sorts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple, Self

from runcoach_api.metrics import finite as _finite_screen

# spec/02 section 2.4.1's resampling gap bound (``quality_gates._MAX_INTERPOLATION_GAP_S``),
# restated rather than imported: a segment longer than this is a break, not a stride.
SEGMENT_MAX_DT_S = 5.0

# The device speed across a gap over SEGMENT_MAX_DT_S that makes it a moving gap (IDEA-126, ruled
# 2026-10-10): dd / dt at or above this counts the gap for time and distance. Every observed pause on a
# run moves at most 0.035 m/s in the fixture corpus and 0.16 m/s in the athlete's store (GPS drift while
# the timer was stopped); a walk is about 1.2 m/s. Half a metre a second sits between the two.
MOVING_GAP_MIN_SPEED_M_S = 0.5


class _FeatureFields(NamedTuple):
    value: float | None
    unavailable: str | None


class Feature(_FeatureFields):
    """One descriptor: a ``value``, or an ``unavailable`` reason code, never both or neither.

    A value is never imputed (spec/03 section 3.2), so a reason stands in its
    place; the constructor refuses the two states that would let a consumer
    read a missing value as a number.
    """

    __slots__ = ()

    def __new__(cls, value: float | None, unavailable: str | None) -> Self:
        if (value is None) == (unavailable is None):
            raise ValueError(
                f"Feature needs exactly one of value and unavailable, got {value!r} and {unavailable!r}"
            )
        return super().__new__(cls, value, unavailable)


@dataclass(frozen=True)
class ScreenedRecord:
    t: float
    distance: float | None
    speed: float | None
    altitude: float | None
    heart_rate: float | None  # <= 0 screened to None
    cadence: float | None
    power: float | None
    power_model: str | None
    gps_degraded: bool | None
    sample_quality: tuple[str, ...]


@dataclass(frozen=True)
class Segment:
    k: int  # index of the start record; the segment runs k -> k+1
    dt: float
    sampled: bool  # 0 < dt <= SEGMENT_MAX_DT_S
    is_break: bool  # dt > SEGMENT_MAX_DT_S
    moving_gap: bool  # a break with both distances present and dd / dt >= MOVING_GAP_MIN_SPEED_M_S
    counted: bool  # sampled or moving_gap: contributes to T and D
    contributed_m: float  # max(dd, 0) when counted and both distances present, else 0.0
    regressed: bool  # sampled, both present, dd < 0
    v_actual: float | None  # contributed_m / dt when counted, else None


@dataclass(frozen=True)
class TimeBase:
    records: list[ScreenedRecord]
    segments: list[Segment]  # len(records) - 1 entries, one per consecutive pair
    s: list[float]  # reconstructed distance per record, s[0] = 0.0
    T: float
    D: float
    distance_regressed: bool


def _get(row: Mapping[str, object], key: str) -> object:
    """Read ``key`` from a dict or a ``sqlite3.Row``; a missing key is ``None``."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _finite(raw: object) -> float | None:
    """The finite screen for one numeric field: ``metrics.finite.finite`` (an int beyond float
    range reads as absent, like any other non-finite value)."""
    return _finite_screen.finite(raw)


def _heart_rate(raw: object) -> float | None:
    """Finiteness first, then the sign: ``nan <= 0`` is False, so the order matters."""
    value = _finite(raw)
    if value is None or value <= 0:
        return None
    return value


def _gps_degraded(raw: object) -> bool | None:
    """SQLite hands back 0/1; normalise to ``bool``, keeping ``None`` as unknown."""
    return None if raw is None else bool(raw)


def _sample_quality(raw: object) -> tuple[str, ...]:
    if raw is None or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(flag) for flag in raw)  # type: ignore[union-attr]


def screen(rows: Iterable[Mapping[str, object]]) -> list[ScreenedRecord]:
    """Apply the finite screen to each row, dropping any row without a finite ``t``."""
    out: list[ScreenedRecord] = []
    for row in rows:
        t = _finite(_get(row, "t"))
        if t is None:
            continue
        power_model = _get(row, "power_model")
        out.append(
            ScreenedRecord(
                t=t,
                distance=_finite(_get(row, "distance")),
                speed=_finite(_get(row, "speed")),
                altitude=_finite(_get(row, "altitude")),
                heart_rate=_heart_rate(_get(row, "heart_rate")),
                cadence=_finite(_get(row, "cadence")),
                power=_finite(_get(row, "power")),
                power_model=power_model if isinstance(power_model, str) else None,
                gps_degraded=_gps_degraded(_get(row, "gps_degraded")),
                sample_quality=_sample_quality(_get(row, "sample_quality")),
            )
        )
    return out


class Contribution(NamedTuple):
    """What one segment adds to the recorded-time base: the rule of the module docstring, point 2."""

    counted: bool  # sampled (0 < dt <= SEGMENT_MAX_DT_S) or a moving gap
    contributed_m: float  # max(dd, 0) when counted and both distances present, else 0.0
    regressed: bool  # sampled, both present, dd < 0


def _contribution(dt: float, d0: float | None, d1: float | None) -> Contribution:
    """The one per-segment rule. ``_segment`` and ``recorded_totals`` both call it; nothing restates it.

    ``dt`` is the segment's duration and ``d0``, ``d1`` the screened distances
    at its two ends. A sampled segment (``0 < dt <= SEGMENT_MAX_DT_S``) is
    counted whatever its distances say. A longer gap is counted only as a
    moving gap: both distances present and ``dd / dt >= MOVING_GAP_MIN_SPEED_M_S``
    (equality counts; a gap at 0.4999 m/s, or with a distance absent, is a
    break that contributes nothing). Whether a segment is a break
    (``dt > SEGMENT_MAX_DT_S``) is left to ``_segment``: a non-moving break and
    a ``dt = 0`` duplicate both contribute nothing here, and only the
    per-segment view tells them apart.
    """
    sampled = 0.0 < dt <= SEGMENT_MAX_DT_S
    if d0 is None or d1 is None:
        return Contribution(sampled, 0.0, False)
    dd = d1 - d0
    if sampled:
        return Contribution(True, max(dd, 0.0), dd < 0.0)
    if dt > SEGMENT_MAX_DT_S and dd / dt >= MOVING_GAP_MIN_SPEED_M_S:
        return Contribution(True, dd, False)
    return Contribution(False, 0.0, False)


def _segment(k: int, start: ScreenedRecord, end: ScreenedRecord) -> Segment:
    dt = end.t - start.t
    counted, contributed_m, regressed = _contribution(dt, start.distance, end.distance)
    is_break = dt > SEGMENT_MAX_DT_S
    return Segment(
        k=k,
        dt=dt,
        sampled=counted and not is_break,
        is_break=is_break,
        moving_gap=counted and is_break,
        counted=counted,
        contributed_m=contributed_m,
        regressed=regressed,
        v_actual=contributed_m / dt if counted else None,
    )


def build_time_base(records: Sequence[ScreenedRecord]) -> TimeBase:
    """Segments, recorded time ``T``, distance ``D`` and the reconstructed distance ``s``."""
    records = list(records)
    segments = [_segment(k, records[k], records[k + 1]) for k in range(len(records) - 1)]
    s: list[float] = [0.0] if records else []
    T = 0.0
    D = 0.0
    for seg in segments:
        if seg.counted:
            T += seg.dt
            D += seg.contributed_m
        s.append(s[-1] + seg.contributed_m)
    return TimeBase(
        records=records,
        segments=segments,
        s=s,
        T=T,
        D=D,
        distance_regressed=any(seg.regressed for seg in segments),
    )


# The record columns ``recorded_totals`` reads. A caller that selects columns
# for it selects these; a rule that needs another input widens this tuple in
# the same change as the rule, so the reader and the rule cannot drift apart.
TOTALS_COLUMNS = ("t", "distance")


def recorded_totals(rows: Iterable[Mapping[str, object]]) -> tuple[float, float]:
    """Recorded time ``T`` and distance ``D`` of ``rows``, exactly ``build_time_base(screen(rows))``'s.

    The lean path for a page that lists many sessions and needs only the two
    totals. It applies the same finite screen (a row without a finite ``t`` is
    skipped, a non-finite distance is absent) and the same ``_contribution``
    rule, accumulating in the same order as ``build_time_base`` with no
    per-record dataclass, so the two sums are bit-identical, not merely close.
    Rows are taken in the order given, as ``build_time_base`` takes records.
    """
    T = 0.0
    D = 0.0
    prev_t: float | None = None
    prev_d: float | None = None
    for row in rows:
        t = _finite(_get(row, "t"))
        if t is None:
            continue
        d = _finite(_get(row, "distance"))
        if prev_t is not None:
            dt = t - prev_t
            counted, contributed_m, _regressed = _contribution(dt, prev_d, d)
            if counted:
                T += dt
                D += contributed_m
        prev_t, prev_d = t, d
    return T, D
