"""The session features response: the GAP transform, NGP and the descriptors assembled with their gates (F013).

This module composes the metrics modules into the response ``GET
/sessions/{session_id}/features`` serves (F013 reference, section 8). It
computes no grade, cost, NGP or descriptor of its own: ``segments`` builds the
time base, ``grade`` reports the raw grade per segment, ``gap`` clamps it and
prices it, ``ngp`` takes the normalized graded speed and ``descriptors`` the
grade-free means. What is written here is the join: the clamp call, the
GAP mean, the three fractions, the per-record g that NGP consumes, the
session-wide overrides and the flag order.

**The clamp is called here, once per graded segment, and never caught.** For
each counted segment with a raw grade, ``gap.clamp_grade`` returns the grade in
Minetti's domain and whether it clamped, and ``gap.g`` prices that grade. A
``ValueError`` out of ``g`` means the clamp was skipped and must stay loud. A
segment with no grade (no altitude in its window, a break, a dt = 0 duplicate)
takes g = 1 and does not count toward ``gap_coverage``: spec/03 section
3.3.4's raw-pace fallback, measured as coverage rather than hidden.

**The GAP mean is distance-weighted.** ``gap_avg_pace_s_per_km = 1000 * T /
sum(v_actual * g * dt)`` over counted segments, with ``v_actual * dt`` the
segment's contributed distance, so ``avg_pace / gap_avg_pace`` is the
distance-weighted mean g by construction (reference section 5), and a flat run
leaves pace unchanged exactly. ``gap_coverage``, ``grade_clamped_fraction`` and
``gps_degraded_fraction`` are contributed-distance shares of D and are null
when D = 0. NGP reads device speed, not distance, so it may hold a value when D
= 0; that is correct, not a leak.

**Session-wide overrides come before any row** (reference section 7): a sport
other than running makes every feature ``sport_not_running``; a session whose
rows screen to no records makes every feature ``no_records``. Both echo the
session's ``quality_flags`` as the flags and leave the fractions null.

**Flags** (reference section 8): the session's ``quality_flags`` in stored
order, ``smart_recording`` moved to the front when present (F013 AC7); then
``distance_regressed``, ``gap_unavailable`` (D > 0 and coverage < 1) and
``grade_clamped``; each at most once. The caller's list is copied, never
mutated.

**The per-segment stream is in-process only** (``segment_rows``): one dict per
counted segment with the fields decoupling, EF and durability will read.
``speed`` and ``block_id`` are the end record's, the record whose NGP sample
this segment's g adjusts, so ``block_id`` is ``ngp.record_block_ids``'s
numbering; ``heart_rate`` and ``hr_excluded`` are the start record's, as the
descriptor means read them, through ``descriptors.hr_excluded`` so the rule
exists once.

**Pure, by design.** No ``config``, ``db`` or ``fastapi`` import, no clock, no
connection. ``session`` is a mapping with ``session_id``, ``sport``,
``quality_flags`` (a list) and ``context`` (a mapping holding the ``env_*``
values); ``rows`` are the mappings ``segments.screen`` reads. The route reads
the rows and wraps the dict in its response model, as ``hrv_trend`` is served.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from runcoach_api.metrics import descriptors, gap, grade, ngp, segments
from runcoach_api.metrics.segments import Feature, TimeBase

RUNNING = "running"

FEATURE_NAMES = (
    "duration_s",
    "distance_m",
    "avg_pace_s_per_km",
    "gap_avg_pace_s_per_km",
    "ngp_speed_m_s",
    "ngp_pace_s_per_km",
    "avg_hr_bpm",
    "avg_cadence_spm",
    "avg_power_w",
    "total_ascent_m",
    "total_descent_m",
    *descriptors.ENV_FEATURES,
)
"""The 14 features of reference section 7, in response order."""

SMART_RECORDING_FLAG = "smart_recording"
DISTANCE_REGRESSED_FLAG = "distance_regressed"
GAP_UNAVAILABLE_FLAG = "gap_unavailable"
GRADE_CLAMPED_FLAG = "grade_clamped"


@dataclass(frozen=True)
class _GradedSegment:
    """One segment's grade after the clamp: the grade priced, whether it was graded and clamped, its g."""

    i: float | None  # the grade g was priced at (clamped into the domain); None when ungraded
    graded: bool
    clamped: bool
    g: float


def _grade_segments(tb: TimeBase) -> list[_GradedSegment]:
    """Clamp and price every segment's raw grade; ungraded and uncounted segments take g = 1."""
    out: list[_GradedSegment] = []
    for raw_i in grade.segment_grades(tb):
        if raw_i is None:
            out.append(_GradedSegment(None, False, False, 1.0))
            continue
        i, clamped = gap.clamp_grade(raw_i)
        out.append(_GradedSegment(i, True, clamped, gap.g(i)))
    return out


def _g_per_record(graded: Sequence[_GradedSegment], n_records: int) -> list[float]:
    """NGP's per-record g: the g of the segment ending at each record, 1.0 for record 0."""
    return [1.0] + [graded[j - 1].g for j in range(1, n_records)]


def _unique(flags: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            out.append(flag)
    return out


def _session_flags(session: Mapping[str, object]) -> list[str]:
    """A copy of the session's ``quality_flags`` in stored order, ``smart_recording`` first when present."""
    stored = [str(flag) for flag in (session.get("quality_flags") or ())]
    if SMART_RECORDING_FLAG in stored:
        stored = [SMART_RECORDING_FLAG] + [flag for flag in stored if flag != SMART_RECORDING_FLAG]
    return _unique(stored)


def _feature_dict(feature: Feature) -> dict[str, object]:
    return {"value": feature.value, "unavailable": feature.unavailable}


def _all_unavailable(session: Mapping[str, object], reason: str) -> dict[str, object]:
    """The response when a session-wide override applies: every feature carries ``reason``."""
    features: dict[str, object] = {name: _feature_dict(Feature(None, reason)) for name in FEATURE_NAMES}
    features["avg_power_w"] = {**features["avg_power_w"], "power_model": None}  # type: ignore[dict-item]
    return {
        "session_id": session.get("session_id"),
        "sport": session.get("sport"),
        "flags": _session_flags(session),
        "gap_coverage": None,
        "grade_clamped_fraction": None,
        "gps_degraded_fraction": None,
        "features": features,
    }


def _override_reason(session: Mapping[str, object], records: Sequence[segments.ScreenedRecord]) -> str | None:
    """The session-wide reason that pre-empts every row, in precedence order, or None."""
    if session.get("sport") != RUNNING:
        return "sport_not_running"
    if not records:
        return "no_records"
    return None


def _pace(T: float, distance_like: float) -> Feature:
    return Feature(1000.0 * T / distance_like, None) if distance_like > 0.0 else Feature(None, "no_distance")


def _fraction(part: float, D: float) -> float | None:
    return part / D if D > 0.0 else None


def compute_session_features(session: Mapping[str, object], rows: Iterable[Mapping[str, object]]) -> dict:
    """The features response of reference section 8 for one session's stored records.

    ``rows`` are the session's records in ``t`` order as ``db`` returns them;
    ``session`` carries ``session_id``, ``sport``, ``quality_flags`` and
    ``context``. Every feature is ``{"value", "unavailable"}`` with exactly one
    side set; ``avg_power_w`` also carries ``power_model``.
    """
    tb = segments.build_time_base(segments.screen(rows))
    reason = _override_reason(session, tb.records)
    if reason is not None:
        return _all_unavailable(session, reason)

    graded = _grade_segments(tb)
    gap_distance = 0.0  # sum(v_actual * g * dt): each counted segment's contributed metres at its g
    graded_m = 0.0
    clamped_m = 0.0
    for seg, gs in zip(tb.segments, graded):
        if not seg.counted:
            continue
        gap_distance += seg.contributed_m * gs.g
        if gs.graded:
            graded_m += seg.contributed_m
        if gs.clamped:
            clamped_m += seg.contributed_m

    T, D = tb.T, tb.D
    gap_coverage = _fraction(graded_m, D)
    ngp_speed = ngp.ngp(tb, _g_per_record(graded, len(tb.records)))
    ngp_pace = Feature(1000.0 / ngp_speed.value, None) if ngp_speed.value else ngp_speed
    power, power_model = descriptors.avg_power(tb)
    ascent, descent = descriptors.ascent_descent(tb)
    context = session.get("context") or {}

    features: dict[str, Feature] = {
        "duration_s": Feature(T, None) if T > 0.0 else Feature(None, "no_counted_segments"),
        "distance_m": Feature(D, None) if D > 0.0 else Feature(None, "no_distance"),
        "avg_pace_s_per_km": _pace(T, D),
        "gap_avg_pace_s_per_km": _pace(T, gap_distance) if D > 0.0 else Feature(None, "no_distance"),
        "ngp_speed_m_s": ngp_speed,
        "ngp_pace_s_per_km": ngp_pace,
        "avg_hr_bpm": descriptors.avg_hr(tb),
        "avg_cadence_spm": descriptors.avg_cadence(tb),
        "avg_power_w": power,
        "total_ascent_m": ascent,
        "total_descent_m": descent,
        **descriptors.env_features(context),  # type: ignore[arg-type]
    }
    response_features: dict[str, object] = {name: _feature_dict(features[name]) for name in FEATURE_NAMES}
    response_features["avg_power_w"] = {**response_features["avg_power_w"], "power_model": power_model}  # type: ignore[dict-item]

    derived_flags = []
    if tb.distance_regressed:
        derived_flags.append(DISTANCE_REGRESSED_FLAG)
    if D > 0.0 and gap_coverage is not None and gap_coverage < 1.0:
        derived_flags.append(GAP_UNAVAILABLE_FLAG)
    if clamped_m > 0.0 or any(gs.clamped for gs in graded):
        derived_flags.append(GRADE_CLAMPED_FLAG)

    return {
        "session_id": session.get("session_id"),
        "sport": session.get("sport"),
        "flags": _unique(_session_flags(session) + derived_flags),
        "gap_coverage": gap_coverage,
        "grade_clamped_fraction": _fraction(clamped_m, D),
        "gps_degraded_fraction": descriptors.gps_degraded_fraction(tb),
        "features": response_features,
    }


def segment_rows(session: Mapping[str, object], rows: Iterable[Mapping[str, object]]) -> list[dict]:
    """The in-process per-segment stream: one dict per counted segment (reference section 8).

    Fields: ``t_start, dt, s_start, contributed_m, v_actual, speed, i, graded,
    clamped, g, block_id, heart_rate, hr_excluded``. ``speed`` and ``block_id``
    belong to the record the segment ends at (where NGP applies this g);
    ``heart_rate`` and ``hr_excluded`` to the record it starts at. Empty when a
    session-wide override applies.
    """
    tb = segments.build_time_base(segments.screen(rows))
    if _override_reason(session, tb.records) is not None:
        return []
    graded = _grade_segments(tb)
    block_ids = ngp.record_block_ids(tb)
    out: list[dict] = []
    for seg, gs in zip(tb.segments, graded):
        if not seg.counted:
            continue
        start = tb.records[seg.k]
        end = tb.records[seg.k + 1]
        out.append(
            {
                "t_start": start.t,
                "dt": seg.dt,
                "s_start": tb.s[seg.k],
                "contributed_m": seg.contributed_m,
                "v_actual": seg.v_actual,
                "speed": end.speed,
                "i": gs.i,
                "graded": gs.graded,
                "clamped": gs.clamped,
                "g": gs.g,
                "block_id": block_ids[seg.k + 1],
                "heart_rate": start.heart_rate,
                "hr_excluded": descriptors.hr_excluded(start),
            }
        )
    return out
