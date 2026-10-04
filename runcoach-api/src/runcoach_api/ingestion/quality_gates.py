"""Recording-mode / resampling quality gate (T023).

Inspects the ``t`` (timestamp-offset-in-seconds) spacing across a
session's ``Record`` stream. "Smart recording" devices only emit a
sample when something changes, so consecutive samples aren't reliably
1s apart. This gate:

- Classifies ``session.recording_interval`` as the spec 2.2.3
  descriptor ``1hz`` / ``smart`` / ``irregular``, using spec 2.4.1
  step 1's *predominance* rule (T031) -- not a single stray gap.
- Flags ``"smart_recording"`` on ``session.quality_flags`` (once) when
  that verdict isn't ``1hz``.
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
import itertools

from runcoach_api.models import Record

_GAP_TOLERANCE = 1e-6
_MAX_INTERPOLATION_GAP_S = 5

# T031 / spec 2.4.1 step 1: "Uniform ~1s spacing -> 1hz. *Predominantly*
# larger, irregular gaps -> smart/irregular." The predominance rule needs
# a numeric cut-off the spec does not fix, so this is a spec-introduced
# implementation default (recorded in F003's Decision Log), mirroring how
# 2.4.1 frames its own 5s gap threshold: tunable, not an open scientific
# question.
#
# A stream is 1hz when at least this fraction of its consecutive
# timestamp deltas are ~1s. 0.95 sits in a very wide empty band: the real
# Garmin 1Hz corpus tops out at 0.102% non-1s deltas (2 of 1960 in
# wrist_ppg_run.fit -- one 81s auto-pause, one 11s dropout), while a
# genuinely smart-recorded stream only emits a sample when a value
# changes and so is nowhere near 95% 1s-spaced. Isolated auto-pauses and
# dropouts are already handled per-sample as ``interpolation_gap``; the
# session-level verdict must not be decided by them.
_UNIFORM_1HZ_MIN_FRACTION = 0.95

_RECORDING_INTERVAL_1HZ = "1hz"
_RECORDING_INTERVAL_SMART = "smart"
_RECORDING_INTERVAL_IRREGULAR = "irregular"

# T028: wrist-PPG cadence-lock detection thresholds.
_CADENCE_LOCK_HR_TOLERANCE_BPM = 3
_CADENCE_LOCK_MIN_CONSECUTIVE = 30

# T029: centered moving-average window (samples) for barometric
# altitude smoothing.
#
# S1 (sprint-002 review): spec §2.4.4 says altitude is smoothed "before
# grade is taken" but fixes no window size, and neither `research/02`
# Section 3.4 nor `research/00` names one either (checked directly -- both
# describe barometric drift as a phenomenon, not a smoothing-window
# formula). Accepted as an explicit spec-introduced implementation
# default, tunable -- not a citable formula constant -- matching the
# treatment `_UNIFORM_1HZ_MIN_FRACTION` gives its own uncited
# predominance cut-off above. 3 samples (~3s post-resampling) is a
# light touch that knocks down single-sample barometric noise spikes;
# the grade itself is taken downstream over spec/03 section 3.3.1's
# +/-25 m window of reconstructed distance, not over this window. See
# F003's Decision Log, 2026-09-03 "S1 altitude smoothing window" entry.
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


def _interpolated_gps_degraded(before: Record, after: Record) -> bool | None:
    """OR of the two real neighbours' ``gps_degraded`` values, ignoring
    whichever side never reported ``gps_accuracy`` at all (``None``).

    Sprint-002 re-review, Stage 0 code-review finding: excluding
    ``gps_degraded`` from linear interpolation (see
    ``_INTERPOLATABLE_FIELDS``) stops it being averaged into a
    meaningless fraction, but a synthetic sample straddling a real
    GPS-degraded stretch is itself inside that stretch -- leaving it
    unset read as "not degraded" to any downstream consumer. OR is the
    conservative direction (flag rather than hide, matching this
    module's other quality gates): a sample is only left ``None`` when
    *neither* neighbour ever reported ``gps_accuracy``.
    """
    known = [v for v in (before.gps_degraded, after.gps_degraded) if v is not None]
    return any(known) if known else None


def _interpolated_record(before: Record, after: Record, t: float) -> Record:
    fraction = (t - before.t) / (after.t - before.t)
    values: dict[str, object] = {
        "t": t,
        "sample_quality": ["interpolated"],
        "gps_degraded": _interpolated_gps_degraded(before, after),
    }
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

    Callers must scope this to wrist-only sessions (spec §2.4.2 step 3:
    "On a wrist-only session, where reported HR stays within ±3 bpm of
    step rate...") -- see ``apply()``'s ``hr_source`` gate. A
    chest-strap session's HR happening to sit on the same number as
    cadence is not this artefact and must not be masked by it (T034
    item 1).
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


def _classify_recording_interval(deltas: list[float]) -> str:
    """Apply spec 2.4.1 step 1's *predominance* rule to the consecutive
    ``t`` deltas of a record stream.

    - At least ``_UNIFORM_1HZ_MIN_FRACTION`` of deltas ~1s -> ``1hz``.
      Isolated auto-pauses/dropouts don't demote the session.
    - Otherwise the stream is non-uniform. If most of its non-1s gaps are
      still inside the ``_MAX_INTERPOLATION_GAP_S`` interpolation ceiling
      it looks like smart recording (the device skipping unchanged
      samples) -> ``smart``; if they predominantly exceed that ceiling the
      stream is fragmented beyond what resampling can honestly fill ->
      ``irregular``.
    """
    uniform = sum(1 for d in deltas if abs(d - 1) <= _GAP_TOLERANCE)
    if uniform >= _UNIFORM_1HZ_MIN_FRACTION * len(deltas):
        return _RECORDING_INTERVAL_1HZ

    non_uniform_gaps = [d for d in deltas if abs(d - 1) > _GAP_TOLERANCE]
    unfillable = sum(1 for d in non_uniform_gaps if d > _MAX_INTERPOLATION_GAP_S)
    if unfillable * 2 > len(non_uniform_gaps):
        return _RECORDING_INTERVAL_IRREGULAR
    return _RECORDING_INTERVAL_SMART


def _resample_and_flag_smart_recording(session, records: list[Record]) -> list[Record]:
    """Detect smart-recording (non-uniform ``t`` spacing), classify the
    session's ``recording_interval`` and resample onto a uniform 1s grid.

    Sets ``session.recording_interval`` to the spec 2.2.3 descriptor
    (``1hz`` / ``smart`` / ``irregular``) via the predominance rule in
    ``_classify_recording_interval``, and flags ``"smart_recording"`` on
    ``session.quality_flags`` (once) when the verdict isn't ``1hz`` -- a
    single stray gap in an otherwise 1Hz file no longer down-weights the
    whole session.

    Resampling itself is unconditional and unchanged by the verdict: gaps
    of <=5s are linearly interpolated onto 1s-spaced synthetic records
    tagged ``"interpolated"``; gaps >5s are left unfilled (never
    fabricating data across a real recording outage) and the record
    immediately after the gap is tagged ``"interpolation_gap"`` instead.
    """
    ordered = sorted(records, key=lambda r: r.t)

    session.recording_interval = _classify_recording_interval(
        [after.t - before.t for before, after in itertools.pairwise(ordered)]
    )

    resampled: list[Record] = [ordered[0]]
    for before, after in itertools.pairwise(ordered):
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

    if (
        session.recording_interval != _RECORDING_INTERVAL_1HZ
        and "smart_recording" not in session.quality_flags
    ):
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

    T034 item 4: the raw measured value is overwritten in place with
    no record that smoothing happened, so any sample this actually
    averaged (i.e. its window included more than just itself) is
    tagged ``"altitude_smoothed"`` in ``sample_quality`` -- making the
    transformation visible downstream (e.g. E003's GAP, which consumes
    this stream) rather than indistinguishable from a raw measurement.
    A window that resolves to a single contributing value (no non-None
    neighbor) is a true no-op and stays untagged.

    Does not flag suspected barometric drift (spec §2.4.4's other
    altitude-gate clause) -- deferred, not implemented; see F003's
    Decision Log, 2026-09-03 "T034 item 5" entry for why (§2.4.4 names
    the drift phenomenon but fixes no detection rule/threshold to
    implement against, and no fixture in this repo's corpus exhibits
    real multi-hour barometric drift to validate one against).
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
        if len(window_values) > 1 and "altitude_smoothed" not in record.sample_quality:
            record.sample_quality.append("altitude_smoothed")


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
        # No consecutive delta exists, so no spacing can be observed.
        # Report the non-permissive verdict rather than claiming 1hz:
        # 2.4.1's output gates whether uniform-sampling metrics may run,
        # and they cannot meaningfully run on a 0/1-sample stream.
        # Resampling and the cadence-lock run-length check both need a
        # delta, so they're skipped below -- but gps_degraded and
        # altitude smoothing (T029) each operate on a single record
        # already (a 0/1-sample stream is a documented no-op for both,
        # see their own docstrings), so unlike this sprint-002
        # re-review found them wrongly skipped alongside the interval
        # verdict when a session has fewer than 2 records.
        session.recording_interval = _RECORDING_INTERVAL_IRREGULAR
    else:
        records[:] = _resample_and_flag_smart_recording(session, records)

        # T028: independent cadence-lock sub-check, run over the final
        # (post-resampling) ~1Hz record stream. Scoped to wrist-only
        # sessions per spec §2.4.2 step 3 -- a chest-strap session
        # (gold-standard HR) whose reported HR happens to sit on the
        # same number as cadence is not the wrist-PPG artefact this
        # gate exists to catch (T034 item 1). ``hr_source`` is already
        # resolved by this point: mapping.py's chest-strap RR
        # detection runs before quality_gates.apply(), and the default
        # above only fills in "wrist_ppg" when it wasn't already set.
        if session.hr_source == "wrist_ppg":
            _flag_cadence_lock_runs(records)

    # T029: independent GPS-degraded flag + altitude-smoothing
    # sub-check, run over the same final record stream -- unlike the
    # branch above, both handle a 0/1-record stream correctly on their
    # own, so they run regardless of how many records there are.
    _flag_gps_degraded(records)
    _smooth_altitude(records)
