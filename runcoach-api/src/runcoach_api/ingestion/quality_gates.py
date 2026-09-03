"""Recording-mode / resampling quality gate (T023).

Inspects the ``t`` (timestamp-offset-in-seconds) spacing across a
session's ``Record`` stream. "Smart recording" devices only emit a
sample when something changes, so consecutive samples aren't reliably
1s apart. This gate:

- Flags ``"smart_recording"`` on ``session.quality_flags`` (once) when
  any consecutive gap isn't exactly 1s.
- Linearly interpolates onto a uniform 1s grid for gaps of <=5s,
  inserting synthetic ``Record``s tagged ``"interpolated"`` in their
  ``sample_quality``.
- Leaves gaps >5s unfilled (never fabricates data across a real
  recording outage) and tags the record immediately after the gap with
  ``"interpolation_gap"``.

Computes no derived training metric (load, decoupling, etc.) -- this
only flags and interpolates raw samples, per T023's scope.
"""

from __future__ import annotations

import dataclasses

from runcoach_api.models import Record

_GAP_TOLERANCE = 1e-6
_MAX_INTERPOLATION_GAP_S = 5

# T028: wrist-PPG cadence-lock detection thresholds.
_CADENCE_LOCK_HR_TOLERANCE_BPM = 3
_CADENCE_LOCK_MIN_CONSECUTIVE = 30

# T029: centered moving-average window (samples) for barometric
# altitude smoothing.
_ALTITUDE_SMOOTHING_WINDOW = 3

# Numeric per-sample fields eligible for linear interpolation across a
# gap: every Record field except the time axis itself and the
# non-numeric/annotation fields. ``gps_degraded`` is a boolean flag,
# not a continuous measurement, so it's excluded (like power_model)
# rather than interpolated into a meaningless fractional value.
_INTERPOLATABLE_FIELDS = tuple(
    f.name
    for f in dataclasses.fields(Record)
    if f.name not in {"t", "sample_quality", "power_model", "gps_degraded"}
)


def _interpolated_record(before: Record, after: Record, t: float) -> Record:
    fraction = (t - before.t) / (after.t - before.t)
    values: dict[str, object] = {"t": t, "sample_quality": ["interpolated"]}
    for name in _INTERPOLATABLE_FIELDS:
        before_value = getattr(before, name)
        after_value = getattr(after, name)
        if before_value is None or after_value is None:
            # Never fabricate a value for a field neither/either endpoint
            # actually measured.
            values[name] = None
        else:
            values[name] = before_value + (after_value - before_value) * fraction
    return Record(**values)


def _cadence_lock_candidate(record: Record) -> bool:
    """True if this sample's heart_rate is implausibly close to cadence.

    Direct comparison (no ``cadence * 2`` or other transform) -- this
    codebase's field-mapping conventions already store ``cadence`` as
    the canonical value the HR sensor could plausibly lock onto.
    """
    return (
        record.heart_rate is not None
        and record.cadence is not None
        and abs(record.heart_rate - record.cadence) <= _CADENCE_LOCK_HR_TOLERANCE_BPM
    )


def _tag_cadence_lock_span(records: list[Record], start: int, end: int) -> None:
    if end - start < _CADENCE_LOCK_MIN_CONSECUTIVE:
        return
    for record in records[start:end]:
        if "cadence_lock" not in record.sample_quality:
            record.sample_quality.append("cadence_lock")


def _flag_cadence_lock_runs(records: list[Record]) -> None:
    """Flag ``"cadence_lock"`` on any span of 30+ consecutive records
    (by index, time-ordered, ~1 per second post-resampling) where
    ``heart_rate`` stays within ``_CADENCE_LOCK_HR_TOLERANCE_BPM`` of
    ``cadence`` -- a known wrist-PPG artefact where the sensor locks
    onto cadence instead of true heart rate.
    """
    run_start: int | None = None
    for i, record in enumerate(records):
        if _cadence_lock_candidate(record):
            if run_start is None:
                run_start = i
            continue
        if run_start is not None:
            _tag_cadence_lock_span(records, run_start, i)
            run_start = None
    if run_start is not None:
        _tag_cadence_lock_span(records, run_start, len(records))


def _flag_gps_degraded(records: list[Record]) -> None:
    """Flag ``"gps_degraded"`` on any record whose ``gps_degraded``
    field is ``True``.

    The flag is expected to already be set on ``Record`` by the time
    ``apply()`` runs (e.g. populated during FIT mapping from a
    ``gps_accuracy``-like field) -- this only translates it into the
    ``sample_quality`` marker; no GPS-accuracy heuristic is invented
    here.
    """
    for record in records:
        if record.gps_degraded is True and "gps_degraded" not in record.sample_quality:
            record.sample_quality.append("gps_degraded")


def _resample_and_flag_smart_recording(session, records: list[Record]) -> list[Record]:
    """Detect smart-recording (non-uniform ``t`` spacing) and resample onto a
    uniform 1s grid.

    Flags ``"smart_recording"`` on ``session.quality_flags`` (once) as soon
    as any consecutive gap isn't exactly 1s -- detected inline during the
    single resampling pass below rather than via a separate upfront scan.
    Gaps of <=5s are linearly interpolated onto 1s-spaced synthetic
    records tagged ``"interpolated"``; gaps >5s are left unfilled (never
    fabricating data across a real recording outage) and the record
    immediately after the gap is tagged ``"interpolation_gap"`` instead.
    """
    ordered = sorted(records, key=lambda r: r.t)

    resampled: list[Record] = [ordered[0]]
    non_uniform = False
    for before, after in zip(ordered, ordered[1:]):
        delta = after.t - before.t

        if abs(delta - 1) <= _GAP_TOLERANCE:
            resampled.append(after)
            continue

        non_uniform = True

        if delta > _MAX_INTERPOLATION_GAP_S:
            # Don't fabricate data across a real gap -- just flag the
            # sample that follows it.
            if "interpolation_gap" not in after.sample_quality:
                after.sample_quality.append("interpolation_gap")
            resampled.append(after)
            continue

        # 1 < delta <= 5: fill the gap with 1s-spaced interpolated points.
        steps = int(round(delta))
        for step in range(1, steps):
            resampled.append(_interpolated_record(before, after, before.t + step))
        resampled.append(after)

    if non_uniform and "smart_recording" not in session.quality_flags:
        session.quality_flags.append("smart_recording")

    return resampled


def _smooth_altitude(records: list[Record]) -> None:
    """Smooth ``record.altitude`` in place via a small centered moving
    average (``_ALTITUDE_SMOOTHING_WINDOW`` samples), pulling
    barometric-altimeter noise/spikes toward their neighbors before
    the values are considered final for storage.

    Missing (``None``) altitude samples are skipped and don't
    contribute to neighboring windows. A stream too short to extend a
    window (handled by ``apply()``'s existing <2-record early return)
    is a no-op.
    """
    half = _ALTITUDE_SMOOTHING_WINDOW // 2
    n = len(records)
    original = [r.altitude for r in records]
    for i, record in enumerate(records):
        if original[i] is None:
            continue
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window_values = [v for v in original[lo:hi] if v is not None]
        record.altitude = sum(window_values) / len(window_values)


def apply(session, records) -> None:
    """Apply the quality gates in place.

    Mutates ``session.quality_flags``/``session.hr_source`` and the
    ``records`` list object itself (via ``records[:] = ...``, never a
    local rebind) so the caller's list reflects any inserted
    interpolated samples or added ``sample_quality`` flags.
    """
    # T028: default HR-source inference. Only set when not already
    # known -- a chest-strap RR stream (T024, set in mapping.py)
    # takes precedence and must never be clobbered here.
    if not session.hr_source:
        session.hr_source = "wrist_ppg"

    if len(records) < 2:
        return

    records[:] = _resample_and_flag_smart_recording(session, records)

    # T028: independent cadence-lock sub-check, run over the final
    # (post-resampling) ~1Hz record stream.
    _flag_cadence_lock_runs(records)

    # T029: independent GPS-degraded flag + altitude-smoothing
    # sub-check, run over the same final record stream.
    _flag_gps_degraded(records)
    _smooth_altitude(records)
