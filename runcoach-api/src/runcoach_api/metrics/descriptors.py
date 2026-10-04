"""The grade-free session descriptors over the time base (F013 AC7, spec/03 section 3.2).

HR, cadence, power with its model, ascent and descent, the three environment
features and the gps-degraded fraction. Each is a small pure function over a
``segments.TimeBase`` and returns a ``segments.Feature``: a value, or an
``unavailable`` reason, never both. A value is never imputed (spec/03 section
3.2), so when nothing qualifies the reason stands in the value's place.

**The means share one convention.** Every average is the time-weighted mean
of a value read at the **start record** of each counted segment, weighted by
that segment's ``dt``; breaks and dt = 0 segments carry no time and contribute
nothing. The gate-aware screens are applied per start record:

- HR is excluded when absent (the finite screen already made ``heart_rate <=
  0`` absent) or when the start record carries the gate's ``cadence_lock``
  tag, through ``hr_excluded`` so the session transform's per-segment stream
  reads the same rule. No present HR on any counted segment gives
  ``no_heart_rate``; present HR that is all locked gives ``cadence_lock``.
- Cadence is excluded when absent or 0 (a stopped sample); nothing qualifying
  gives ``no_cadence``.
- Power counts whenever it is present, **including a record whose
  ``power_model`` is None**: the ingestion gate interpolates ``power`` across
  a 1-5 s gap but never its model, so an interpolated record carries power
  with no model. Only the distinct non-null models are compared, and more
  than one gives ``mixed_power_models`` (ratified 2026-10-04). If every model
  is None the mean is reported with model None.
- ``gps_degraded`` samples are **not** excluded from any mean: spec/02
  section 2.4.4 trusts pace once it is averaged. They are reported as a
  contributed-distance fraction instead, None when D = 0.

**Ascent and descent** are a 1 m hysteresis over the present altitude
samples in ``t`` order (reference section 7). The threshold is a heuristic
default ratified in F013's Decision Log; the vendor ``total_ascent`` was
calibration evidence for it and is never an input. Raw over derived:
nothing here reads the session summary, the sidecar or any vendor
total.

**Pure, by design.** No ``config``, ``db`` or ``fastapi`` import, no clock, no
connection; the environment features take the already-decoded ``context``
mapping and copy its three ``env_*`` values verbatim, or report
``not_recorded`` when a value is null or missing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from runcoach_api.metrics.segments import Feature, ScreenedRecord, Segment, TimeBase

HYSTERESIS_M = 1.0
"""Heuristic default, F013 Decision Log (2026-10-03): a move under 1 m is barometric noise, not relief.

The vendor ``total_ascent`` is calibration evidence for this value, never an input."""

CADENCE_LOCK_TAG = "cadence_lock"
GPS_DEGRADED_TAG = "gps_degraded"
# The gate's exact ``sample_quality`` strings (``ingestion/quality_gates.py``), restated so the
# descriptors import nothing from ingestion.

ENV_FEATURES = ("env_temperature_c", "env_humidity_pct", "env_wind_ms")


def hr_excluded(record: ScreenedRecord) -> bool:
    """True when the record's HR cannot be used: absent, or the record carries ``cadence_lock``.

    The one home of the rule; ``avg_hr`` and the session transform's per-segment
    stream both read it.
    """
    return record.heart_rate is None or CADENCE_LOCK_TAG in record.sample_quality


def _counted_starts(tb: TimeBase) -> Iterator[tuple[Segment, ScreenedRecord]]:
    """Each counted segment with its start record: the one walk every descriptor below takes."""
    for seg in tb.segments:
        if seg.counted:
            yield seg, tb.records[seg.k]


def _time_weighted_mean(
    tb: TimeBase, value_of: Callable[[ScreenedRecord], float | None]
) -> float | None:
    """The dt-weighted mean of ``value_of(start record)`` over counted segments; None if none qualify."""
    weighted = 0.0
    time = 0.0
    for seg, start in _counted_starts(tb):
        value = value_of(start)
        if value is None:
            continue
        weighted += value * seg.dt
        time += seg.dt
    return weighted / time if time > 0.0 else None


def avg_hr(tb: TimeBase) -> Feature:
    """Time-weighted mean HR over counted segments whose start record passes ``hr_excluded``."""
    mean = _time_weighted_mean(tb, lambda r: None if hr_excluded(r) else r.heart_rate)
    if mean is not None:
        return Feature(mean, None)
    any_present = any(start.heart_rate is not None for _, start in _counted_starts(tb))
    # Precedence: HR that was never there is `no_heart_rate`; HR that was there and all of it
    # locked is the gate's own flag.
    return Feature(None, CADENCE_LOCK_TAG if any_present else "no_heart_rate")


def avg_cadence(tb: TimeBase) -> Feature:
    """Time-weighted mean cadence over counted segments with cadence present and > 0."""
    mean = _time_weighted_mean(
        tb, lambda r: r.cadence if r.cadence is not None and r.cadence > 0.0 else None
    )
    return Feature(mean, None) if mean is not None else Feature(None, "no_cadence")


def avg_power(tb: TimeBase) -> tuple[Feature, str | None]:
    """Time-weighted mean power over counted segments with power present, and its one model.

    A record with power and a None model counts toward the mean; only non-null
    models are compared, and more than one distinct model gives
    ``mixed_power_models`` with no model.
    """
    mean = _time_weighted_mean(tb, lambda r: r.power)
    if mean is None:
        return Feature(None, "no_power"), None
    models = {
        start.power_model
        for _, start in _counted_starts(tb)
        if start.power is not None and start.power_model is not None
    }
    if len(models) > 1:
        return Feature(None, "mixed_power_models"), None
    return Feature(mean, None), next(iter(models), None)


def ascent_descent(tb: TimeBase) -> tuple[Feature, Feature]:
    """Total ascent and descent by ``HYSTERESIS_M`` hysteresis over the present altitude samples.

    The reference altitude moves only when a sample is at least ``HYSTERESIS_M``
    away from it, so sub-threshold barometric wobble adds to neither total.
    Fewer than two present samples give ``no_altitude`` on both.
    """
    alts = [r.altitude for r in tb.records if r.altitude is not None]
    if len(alts) < 2:
        return Feature(None, "no_altitude"), Feature(None, "no_altitude")
    ascent = 0.0
    descent = 0.0
    ref = alts[0]
    for a in alts[1:]:
        if a - ref >= HYSTERESIS_M:
            ascent += a - ref
            ref = a
        elif ref - a >= HYSTERESIS_M:
            descent += ref - a
            ref = a
    return Feature(ascent, None), Feature(descent, None)


def env_features(context: Mapping[str, object]) -> dict[str, Feature]:
    """The three ``env_*`` features, verbatim from the session context, or ``not_recorded``."""
    out: dict[str, Feature] = {}
    for name in ENV_FEATURES:
        value = context.get(name)
        if value is None:
            out[name] = Feature(None, "not_recorded")
        else:
            out[name] = Feature(value, None)  # type: ignore[arg-type]
    return out


def gps_degraded_fraction(tb: TimeBase) -> float | None:
    """Contributed distance of counted segments whose start record is ``gps_degraded``, over D.

    None when D = 0 (reference section 5). Nothing is excluded on this tag; the
    fraction is the report.
    """
    if tb.D == 0.0:
        return None
    degraded = sum(
        seg.contributed_m
        for seg, start in _counted_starts(tb)
        if GPS_DEGRADED_TAG in start.sample_quality
    )
    return degraded / tb.D
