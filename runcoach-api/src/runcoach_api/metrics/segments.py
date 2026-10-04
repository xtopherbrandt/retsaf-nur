"""The session time base (F013, spec/03 section 3.2): the finite screen, segments and recorded time.

Every F013 figure (the gradient window, NGP, the descriptors) is built on the
three things defined here, in this order:

1. **The finite screen** (``screen``), applied once at the module boundary. A
   value that is ``None``, ``nan``, ``inf`` or ``-inf``, or whose key the row
   does not carry, is *absent* (``None``) for every later rule; ``heart_rate
   <= 0`` is absent too. The screen is written here rather than borrowed from
   ``hrv_classification._numeric``, whose ratified HRV semantics belong to its
   own call sites. The one record ``screen`` *removes* rather than screens is a
   row whose ``t`` is missing or non-finite: a record with no place on the time
   axis cannot start or end a segment. Upload cannot deliver a non-finite value
   (SQLite stores ``nan`` as NULL; FIT altitude and HR are integer-encoded), so
   the screen is pinned at this seam with constructed records.
2. **Segments and the recorded-time base** (``build_time_base``). A *segment*
   is the interval between consecutive records ``k`` and ``k + 1``. It is
   **counted** when ``0 < dt <= SEGMENT_MAX_DT_S``; it is a **break** (a pause
   or an unfilled gap) when ``dt > SEGMENT_MAX_DT_S`` and contributes neither
   time nor distance; a ``dt = 0`` segment (the resampler can emit duplicate
   instants) contributes nothing and is *not* a break. The duplicate record
   itself stays in ``records``. Recorded time ``T = sum(dt)`` and distance
   ``D = sum(max(dd, 0))`` run over counted segments only, so a 600 s pause and
   a dt = 0 duplicate leave both unchanged: the time base is recorded time, not
   elapsed time (F013 Decision Log, "recorded time"). A counted segment whose
   distance decreases contributes 0 m and raises ``distance_regressed``; one
   with either distance absent contributes 0 m and is still counted for time.
   The **reconstructed distance** ``s`` is the running sum of those
   contributions, monotone by construction, so the gradient window that walks
   it can never become non-contiguous.
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

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple, Self

# spec/02 section 2.4.1's resampling gap bound (``quality_gates._MAX_INTERPOLATION_GAP_S``),
# restated rather than imported: a segment longer than this is a break, not a stride.
SEGMENT_MAX_DT_S = 5.0


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
    counted: bool  # 0 < dt <= SEGMENT_MAX_DT_S
    is_break: bool  # dt > SEGMENT_MAX_DT_S
    contributed_m: float  # max(dd, 0) when counted and both distances present, else 0.0
    regressed: bool  # counted, both present, dd < 0
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
    """The finite screen for one numeric field: a finite int or float, else absent."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None


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


def _segment(k: int, start: ScreenedRecord, end: ScreenedRecord) -> Segment:
    dt = end.t - start.t
    counted = 0.0 < dt <= SEGMENT_MAX_DT_S
    is_break = dt > SEGMENT_MAX_DT_S
    contributed_m = 0.0
    regressed = False
    if counted and start.distance is not None and end.distance is not None:
        dd = end.distance - start.distance
        regressed = dd < 0.0
        contributed_m = max(dd, 0.0)
    return Segment(
        k=k,
        dt=dt,
        counted=counted,
        is_break=is_break,
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
