"""Per-session training load, HR-TRIMP first: the load body from a session's own values (F017).

This module computes the body that ``GET /sessions/{session_id}/load`` serves
(F017 reference, "Interface") from inputs the storage layer builds; it reads
no row and resolves no anchor. Of spec/03 section 3.4's three metrics only
HR-TRIMP is computed: rTSS reports ``no_threshold_pace`` (threshold pace is
E004's) and sRPE-load ``no_rpe`` (no RPE input exists), so there is nothing to
reconcile and ``session_load`` is HR-TRIMP on the common scale.

**Time basis** (user ruling 2026-10-08; reference "Time basis"). TRIMP
counts running time with usable HR and never stopped time:

- ``hr_time_s`` is the sum of ``dt`` over F013's counted segments
  (``session_features.segment_rows``: ``0 < dt <= 5`` s, so a watch pause
  writes no records and is never counted) whose start HR is usable (present,
  > 0, not ``cadence_lock``; the rows carry that as ``hr_excluded``, through
  ``descriptors.hr_excluded``, so the rule exists once). Nothing is imputed.
- ``avg_hr_bpm`` is F013's served ``features.avg_hr_bpm``, the dt-weighted
  mean over the same segments; it is read, never re-summed.
- ``timer_time_s`` is the FIT ``session.total_timer_time`` the session row
  stores; ``hr_time_fraction = hr_time_s / timer_time_s`` with no cut-off
  and no clamp (a full run reads 1.0008 or 0.9972: the timer is fractional
  and records land on whole seconds). ``recorded_time_s`` is F013's
  ``duration_s``, reported beside it.

**Formulas** (spec/03 section 3.4.1 and 3.4.2 step 1):
``r = (HR_avg - HR_rest) / (HR_max - HR_rest)``; ``TRIMP = (hr_time_s / 60)
* r * k * e^(c r)`` with ``(k, c)`` = (0.64, 1.92) for men and (0.86, 1.67)
for women; the threshold-hour reference ``60 * r_thr * k * e^(c r_thr)``
uses the **same** pair as the session's TRIMP (user ruling R4), so one hour
at threshold scores 100 for either sex; ``session_load = TRIMP / reference *
100``. No sex value takes the men's pair and the flag ``sex_defaulted``,
set only when TRIMP is computed. Equality at the bounds is computed: ``r =
0`` gives a zero load and ``r = 1`` the full exponent.

**Gates, in order, first match wins** (reference "Gates"). Built here:
1 ``sport_not_running`` (rtss and srpe carry it too), 2 ``declared_capture``
for ``resting_hrv_check`` or ``health_snapshot`` (rtss and srpe too), 3
``no_hr`` when ``hr_time_s`` is 0, 4 ``missing_anchor`` when resting or max
has no value (``unavailable_fields`` names them), 5 ``order_conflict`` when
resting >= max among the values used (equality included), 6
``avg_hr_below_resting`` / ``avg_hr_above_max`` when the average HR lies
outside the bounds (R3; equality is computed), 7 ``wrist_hr_threshold_unknown``
on the wrist path with no usable threshold (none, or not ``resting <
threshold < max``), 8 ``wrist_hr_at_threshold`` on the wrist path when the
average HR is at or above the threshold (R2; judged by average HR, user
ruling 2026-10-08; equality refused), 9 ``not_representable`` when
resting, max or a present threshold does not convert to a finite float
(``float(10**400)`` raises ``OverflowError``; it is caught per value, so no
upload is a 500) or when ``float(max) - float(resting)`` is not a positive
finite float (``2**53`` and ``2**53 + 1`` pass gate 5 as integers and are one
float). After TRIMP, the reference needs the threshold: no value
gives ``no_threshold_hr``; ``resting < threshold < max`` failing (equality at
either bound included) gives ``threshold_order_conflict``;
``float(threshold) - float(resting)`` not a positive finite float gives
``not_representable``; TRIMP is still
served. When TRIMP is unavailable ``session_load`` carries the same reason
and ``driver`` is null; under gates 1 and 2 the inputs are still reported.

**The wrist path.** ``hr_source`` is ``chest_strap``, ``wrist_ppg`` or null;
anything other than ``chest_strap`` (null and an unknown string included)
takes the wrist path. The flag ``wrist_hr`` is set on every running session
on the wrist path whatever the outcome (REG-23: PPG input is flagged), so a
session gates 2-6 or 9 refuse still carries it; a non-running session is
refused whole by gate 1 and carries no flag. On the wrist path a threshold
that passes gate 7 is usable, so the reference step after TRIMP withholds
``session_load`` there only as ``not_representable``. Gate comparisons are on
exact integers (Python compares an int of any size with a float exactly, so
the average HR against ``10**400`` is a comparison, not a conversion); a value
becomes a float only in gate 9's checks and the formula step.
``SERVED_REASONS`` lists every reason this module writes, in the reference's
order.

**Where the values come from.** This module reads the four value blocks as
given. ``db._save_session_load`` builds them at upload: the session's own
file value as ``session_file``, else the F016 anchor in effect inside the
upload's transaction as ``anchor`` with its version, else an empty block whose
``anchor_unavailable`` carries F016's reason (``missing`` or
``order_conflict``), so gate 4 reports a conflicting anchor as missing here
while the inputs say why.

**Pure, by design.** No ``config``, ``db`` or ``fastapi`` import. ``inputs``
is a plain mapping: ``session_id``, ``sport``, ``activity_tag``,
``hr_source``, ``quality_flags``, ``timer_time_s``, ``segment_rows`` and
``features`` (``session_features.segment_rows`` and
``compute_session_features`` for the session), and per HR field
(``resting_hr_bpm``, ``max_hr_bpm``, ``threshold_hr_bpm``, ``sex``) a mapping
``{value, source, session_id, anchor_version, anchor_unavailable}`` holding
the value used and where it came from; those four blocks are echoed in
``inputs`` unchanged. A block that is absent or not a mapping is the
writer's defect and raises ``ValueError`` naming the field.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from runcoach_api.metrics.session_features import unique_flags

RUNNING = "running"
DECLARED_CAPTURE_TAGS = ("resting_hrv_check", "health_snapshot")

COEFFICIENTS: dict[str, tuple[float, float]] = {"male": (0.64, 1.92), "female": (0.86, 1.67)}
"""Banister's ``(k, c)`` per sex (spec/03 section 3.4.1)."""

MALE = "male"
HR_TRIMP = "hr_trimp"
SEX_DEFAULTED_FLAG = "sex_defaulted"
WRIST_HR_FLAG = "wrist_hr"
CHEST_STRAP = "chest_strap"
"""The one ``hr_source`` that escapes the wrist path (gates 7-8)."""

ANCHOR_FIELDS = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "sex")
EMPTY_VALUE: Mapping[str, None] = MappingProxyType(
    {"value": None, "source": None, "session_id": None, "anchor_version": None, "anchor_unavailable": None}
)
"""The five keys of an ``inputs.<field>`` block, all null: the block for a field with no value used.
Read-only; a writer copies it (``dict(EMPTY_VALUE)`` or ``{**EMPTY_VALUE, ...}``)."""

NO_THRESHOLD_PACE = "no_threshold_pace"
NO_RPE = "no_rpe"
SESSION_WIDE_REASONS = ("sport_not_running", "declared_capture")
"""Gates 1 and 2: the reasons rtss and srpe carry as well as hr_trimp."""

SERVED_REASONS = (
    "sport_not_running",
    "declared_capture",
    "no_hr",
    "missing_anchor",
    "order_conflict",
    "avg_hr_below_resting",
    "avg_hr_above_max",
    "wrist_hr_threshold_unknown",
    "wrist_hr_at_threshold",
    "not_representable",
    "no_threshold_hr",
    "threshold_order_conflict",
)
"""Every reason this module writes into a body, in the reference's order; each is a member of the
contract's closed ``SessionLoadReason`` enum (the route test holds that)."""


def _finite(*values) -> bool:
    """True when every value that is present converts to a finite float (gate 9)."""
    for value in values:
        if value is None:
            continue
        try:
            if not math.isfinite(float(value)):
                return False
        except OverflowError:
            return False
    return True


def _span(upper, lower) -> float | None:
    """``float(upper) - float(lower)`` when it is a positive finite float, else None (gate 9).

    Called only on values ``_finite`` passed and the exact-integer gates ordered, so the
    conversion cannot raise; but two adjacent integers above ``2**53`` round to one float (a span
    of 0.0) and two finite values far apart can differ by more than the largest float (inf).
    """
    span = float(upper) - float(lower)
    return span if span > 0.0 and math.isfinite(span) else None


def _value_block(inputs: Mapping[str, object], field_name: str) -> dict:
    """The ``inputs.<field>`` block as the caller built it, restricted to ``EMPTY_VALUE``'s keys.

    A block that is absent or not a mapping is a defect of the writer (``db._save_session_load``
    builds all four for every session), refused with a ``ValueError`` naming the field rather
    than read as no value and served as ``missing_anchor``.
    """
    if field_name not in inputs:
        raise ValueError(f"inputs.{field_name} is absent; every load input carries all four value blocks")
    block = inputs[field_name]
    if not isinstance(block, Mapping):
        # A ValueError, as every writer defect here and in load_chart._place is.
        raise ValueError(f"inputs.{field_name} is not a value block: {type(block).__name__} {block!r}")  # noqa: TRY004
    return {key: block.get(key) for key in EMPTY_VALUE}


def _hr_time_s(segment_rows: Iterable[Mapping[str, object]]) -> float:
    """The sum of ``dt`` over counted segments whose start HR is usable."""
    total = 0.0
    for row in segment_rows:
        if row.get("hr_excluded") or row.get("heart_rate") is None:
            continue
        total += float(row["dt"])  # type: ignore[arg-type]
    return total


def _served(features: Mapping[str, object], name: str) -> float | None:
    """F013's served value for one feature, or None when it is unavailable."""
    block = (features.get("features") or {}).get(name)  # type: ignore[union-attr]
    return block.get("value") if isinstance(block, Mapping) else None


def _trimp(duration_min: float, r: float, pair: tuple[float, float]) -> float:
    k, c = pair
    return duration_min * r * k * math.exp(c * r)


def _metric(value: float | None, reason: str | None) -> dict:
    return {"value": value, "unavailable": reason}


def compute_session_load(inputs: Mapping[str, object]) -> dict:
    """The load body of the F017 reference for one session, from the values the caller resolved."""
    sport = inputs.get("sport")
    features = inputs.get("features") or {}
    segment_rows = inputs.get("segment_rows") or []
    values = {field_name: _value_block(inputs, field_name) for field_name in ANCHOR_FIELDS}

    hr_time_s = _hr_time_s(segment_rows)  # type: ignore[arg-type]
    timer_time_s = inputs.get("timer_time_s")
    body_inputs = {
        "hr_time_s": hr_time_s,
        "recorded_time_s": _served(features, "duration_s"),  # type: ignore[arg-type]
        "timer_time_s": timer_time_s,
        "hr_time_fraction": hr_time_s / timer_time_s if timer_time_s else None,  # type: ignore[operator]
        "avg_hr_bpm": _served(features, "avg_hr_bpm"),  # type: ignore[arg-type]
        "hr_source": inputs.get("hr_source"),
        **values,
    }
    input_flags = unique_flags([*(features.get("flags") or []), *(inputs.get("quality_flags") or [])])  # type: ignore[union-attr]

    flags: list[str] = []
    hr_trimp = {
        "value": None,
        "unavailable": None,
        "unavailable_fields": [],
        "threshold_hour_reference": None,
        "coefficients": None,
    }
    load_reason: str | None = None

    # Gates 1-9, in the reference's order; exact integers only.
    resting = values["resting_hr_bpm"]["value"]
    max_hr = values["max_hr_bpm"]["value"]
    threshold = values["threshold_hr_bpm"]["value"]
    avg_hr = body_inputs["avg_hr_bpm"]
    wrist = sport == RUNNING and inputs.get("hr_source") != CHEST_STRAP
    if wrist:
        flags.append(WRIST_HR_FLAG)
    if sport != RUNNING:
        hr_trimp["unavailable"] = "sport_not_running"
    elif inputs.get("activity_tag") in DECLARED_CAPTURE_TAGS:
        hr_trimp["unavailable"] = "declared_capture"
    elif hr_time_s == 0.0:
        hr_trimp["unavailable"] = "no_hr"
    elif resting is None or max_hr is None:
        hr_trimp["unavailable"] = "missing_anchor"
        hr_trimp["unavailable_fields"] = [
            name for name in ("resting_hr_bpm", "max_hr_bpm") if values[name]["value"] is None
        ]
    elif resting >= max_hr:
        hr_trimp["unavailable"] = "order_conflict"
    elif avg_hr < resting:  # type: ignore[operator]
        hr_trimp["unavailable"] = "avg_hr_below_resting"
    elif avg_hr > max_hr:  # type: ignore[operator]
        hr_trimp["unavailable"] = "avg_hr_above_max"
    elif wrist and (threshold is None or not (resting < threshold < max_hr)):
        hr_trimp["unavailable"] = "wrist_hr_threshold_unknown"
    elif wrist and avg_hr >= threshold:  # type: ignore[operator]
        hr_trimp["unavailable"] = "wrist_hr_at_threshold"
    elif not _finite(resting, max_hr, threshold) or (span := _span(max_hr, resting)) is None:
        hr_trimp["unavailable"] = "not_representable"
    else:
        # The formula step: with gate 9's checks, the only place a value becomes a float.
        sex = values["sex"]["value"]
        if sex not in COEFFICIENTS:
            sex = MALE
            flags.append(SEX_DEFAULTED_FLAG)
        pair = COEFFICIENTS[sex]
        r = (float(avg_hr) - float(resting)) / span  # type: ignore[arg-type]
        hr_trimp["value"] = _trimp(hr_time_s / 60.0, r, pair)
        hr_trimp["coefficients"] = sex
        if threshold is None:
            load_reason = "no_threshold_hr"
        elif not (resting < threshold < max_hr):
            load_reason = "threshold_order_conflict"
        elif (threshold_span := _span(threshold, resting)) is None:
            load_reason = "not_representable"
        else:
            r_thr = threshold_span / span
            hr_trimp["threshold_hour_reference"] = _trimp(60.0, r_thr, pair)

    trimp_reason = hr_trimp["unavailable"]
    if trimp_reason is not None:
        session_load = {"value": None, "unavailable": trimp_reason, "driver": None}
    elif load_reason is not None:
        session_load = {"value": None, "unavailable": load_reason, "driver": None}
    else:
        reference = hr_trimp["threshold_hour_reference"]
        session_load = {
            "value": hr_trimp["value"] / reference * 100.0,  # type: ignore[operator]
            "unavailable": None,
            "driver": HR_TRIMP,
        }

    session_wide = trimp_reason if trimp_reason in SESSION_WIDE_REASONS else None
    return {
        "session_id": inputs.get("session_id"),
        "sport": sport,
        "session_load": session_load,
        "flags": flags,
        "input_flags": input_flags,
        "metrics": {
            "hr_trimp": hr_trimp,
            "rtss": _metric(None, session_wide or NO_THRESHOLD_PACE),
            "srpe": _metric(None, session_wide or NO_RPE),
        },
        "inputs": body_inputs,
    }
