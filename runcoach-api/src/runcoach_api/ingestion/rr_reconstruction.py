"""RR-interval reconstruction across chest-strap carriers (T024, spec §2.3.4).

Concatenates every ``hrv`` (#78) message's ``time`` array (the primary
carrier, seconds -> ms, discarding sentinel/invalid ``None`` slots),
and additionally checks ``event`` (#21) messages and per-record
developer fields as alternate carriers -- merging whatever is found,
in the file's own chronological message order, and recording which
carrier supplied each portion in ``RRInterval.rr_source``. The merged
series is then artefact-filtered: any interval outside 300-2000ms, or
differing from its local (surrounding) median by more than 20%, is
flagged ``is_artefact`` -- never silently dropped, matching the
flag-and-down-weight pattern ``quality_gates.py`` already uses for its
own gates.

Scope note: the event-message and developer-field RR carriers are
structurally implemented per spec, but -- like ``mapping.py``'s
``_GPS_DEGRADED_ACCURACY_THRESHOLD_M`` -- unverified against any real
fixture in this repo's corpus. None of the project's real chest-strap
FIT exports (``chest_strap_run.fit``, ``T024_chest_strap_HRV.fit``)
carry RR data via *any* carrier, hrv included; only
``Fr955-Stryd-running.fit`` does, and only via the ``hrv``-message
path. Only that path is exercised against real ``fitdecode`` output in
``test_rr_reconstruction.py``; the event/dev-field matching rules
below are covered with hand-built stand-in message objects (same
accepted exception the project's real-fixture testing rule already
carries for the GPS-degraded threshold). Revisit both matching rules
if a real fixture ever surfaces either carrier.
"""

from __future__ import annotations

import fitdecode

from runcoach_api.models import RRInterval

_MS_PER_S = 1000.0

_MIN_PLAUSIBLE_RR_MS = 300
_MAX_PLAUSIBLE_RR_MS = 2000
_LOCAL_MEDIAN_DEVIATION_FRACTION = 0.20

# Centered local-median window for the artefact check, matching the
# small-window style already used by quality_gates.py's altitude
# smoothing (_ALTITUDE_SMOOTHING_WINDOW).
_ARTEFACT_WINDOW = 11

# No confirmed device-specific developer-field name for RR data exists
# in this repo's fixture corpus (see module docstring) -- matched by
# a case-insensitive substring instead of an exact name.
_DEV_FIELD_RR_NAME_HINT = "rr"


def _hrv_candidates(msg) -> list[tuple[float, str]]:
    time_values = msg.get_value("time", fallback=None)
    if not time_values:
        return []
    return [(value * _MS_PER_S, "hrv") for value in time_values if value is not None]


def _event_candidates(msg) -> list[tuple[float, str]]:
    if msg.get_value("event", fallback=None) != "rr_interval":
        return []
    value = msg.get_value("data", fallback=None)
    if value is None:
        value = msg.get_value("data16", fallback=None)
    if value is None:
        return []
    return [(float(value), "event")]


def _developer_field_candidates(msg) -> list[tuple[float, str]]:
    candidates: list[tuple[float, str]] = []
    for field_data in msg.fields:
        if not isinstance(field_data.field, fitdecode.types.DevField):
            continue
        name = field_data.name or ""
        value = field_data.value
        if value is None or _DEV_FIELD_RR_NAME_HINT not in name.lower():
            continue
        candidates.append((float(value), "developer_field"))
    return candidates


def _collect_candidates(messages) -> list[tuple[float, str]]:
    """One linear pass over ``messages`` in their original (already
    chronological) file order, so candidates from different carriers
    interleave correctly without needing a separate position/timestamp
    field -- list order alone is the merge order.
    """
    candidates: list[tuple[float, str]] = []
    for msg in messages:
        if msg.name == "hrv":
            candidates.extend(_hrv_candidates(msg))
        elif msg.name == "event":
            candidates.extend(_event_candidates(msg))
        elif msg.name == "record":
            candidates.extend(_developer_field_candidates(msg))
    return candidates


def _local_median(values: list[float], index: int) -> float | None:
    half = _ARTEFACT_WINDOW // 2
    lo = max(0, index - half)
    hi = min(len(values), index + half + 1)
    window = [v for i, v in enumerate(values[lo:hi], start=lo) if i != index]
    if not window:
        return None
    window.sort()
    mid = len(window) // 2
    if len(window) % 2:
        return window[mid]
    return (window[mid - 1] + window[mid]) / 2


def _is_artefact(rr_ms: float, local_median: float | None) -> bool:
    if rr_ms < _MIN_PLAUSIBLE_RR_MS or rr_ms > _MAX_PLAUSIBLE_RR_MS:
        return True
    if not local_median:
        return False
    return abs(rr_ms - local_median) / local_median > _LOCAL_MEDIAN_DEVIATION_FRACTION


def reconstruct(messages: list[fitdecode.FitDataMessage]) -> list[RRInterval]:
    """Merge every RR carrier found in ``messages`` into one
    artefact-filtered, provenance-tagged series (spec §2.3.4).

    Returns ``[]`` when no carrier has any RR data -- a real,
    non-error outcome for a chest-strap-worn device whose FIT export
    doesn't include beat-to-beat data (this repo's own
    ``chest_strap_run.fit`` fixture is exactly such a case).
    """
    candidates = _collect_candidates(messages)
    if not candidates:
        return []

    values = [rr_ms for rr_ms, _carrier in candidates]

    return [
        RRInterval(
            seq=seq,
            rr_ms=rr_ms,
            rr_source=carrier,
            is_artefact=_is_artefact(rr_ms, _local_median(values, seq)),
        )
        for seq, (rr_ms, carrier) in enumerate(candidates)
    ]


def valid_fraction(rr_intervals: list[RRInterval]) -> float:
    """Fraction of ``rr_intervals`` not flagged ``is_artefact`` -- the
    ``rr_valid_fraction`` spec §2.2.3/§2.3.4 requires.
    """
    if not rr_intervals:
        return 0.0
    valid = sum(1 for rr in rr_intervals if not rr.is_artefact)
    return valid / len(rr_intervals)
