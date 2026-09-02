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

# Numeric per-sample fields eligible for linear interpolation across a
# gap: every Record field except the time axis itself and the
# non-numeric/annotation fields.
_INTERPOLATABLE_FIELDS = tuple(
    f.name for f in dataclasses.fields(Record) if f.name not in {"t", "sample_quality", "power_model"}
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


def apply(session, records) -> None:
    """Apply the recording-mode/resampling quality gate in place.

    Mutates ``session.quality_flags`` and the ``records`` list object
    itself (via ``records[:] = ...``, never a local rebind) so the
    caller's list reflects any inserted interpolated samples.
    """
    if len(records) < 2:
        return

    ordered = sorted(records, key=lambda r: r.t)

    non_uniform = any(abs((b.t - a.t) - 1) > _GAP_TOLERANCE for a, b in zip(ordered, ordered[1:]))
    if non_uniform and "smart_recording" not in session.quality_flags:
        session.quality_flags.append("smart_recording")

    resampled: list[Record] = [ordered[0]]
    for before, after in zip(ordered, ordered[1:]):
        delta = after.t - before.t

        if abs(delta - 1) <= _GAP_TOLERANCE:
            resampled.append(after)
            continue

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

    records[:] = resampled
