"""RR-interval reconstruction across chest-strap carriers (T024, spec §2.3.4).

Concatenates every ``hrv`` (#78) message's ``time`` array (the primary
carrier, seconds -> ms, discarding sentinel/invalid ``None`` slots) and
additionally checks per-record developer fields as an alternate
carrier, merging what is found in the file's own chronological message
order. The merged series is then artefact-filtered per spec §2.4.3:
any interval outside 300-2000ms, or differing from its local
(surrounding) median by more than 20%, is flagged ``is_artefact`` --
never silently dropped, matching the flag-and-down-weight pattern
``quality_gates.py`` already uses.

``rr_source`` vs ``rr_carrier``. Spec §2.2.3 defines ``rr_source`` as a
fixed **tier** enum (``chest_strap_ecg`` / ``overnight_ppg`` /
``health_snapshot_ppg`` / ``other``), and §2.3.4 step 4 says to set
``rr_source = chest_strap_ecg`` when a raw RR stream is present in an
activity file -- that is the value §3's tier weighting reads. §2.3.4
step 3 separately asks which *carrier* supplied the series; that is
recorded in ``rr_carrier``, a distinct field, so the tier enum is not
overloaded with non-enum values (a prior revision of this module wrote
``"hrv"`` into ``rr_source``, which no downstream tier check would
ever match).

**The ``event`` (#21) carrier is deliberately NOT implemented.**
``research/02`` §2.3 notes that "some devices write RR/HRV or button
events here", and spec §2.3.4 step 3 asks parsers to check it -- but
neither document specifies the field or event type that carries the
beats, and the FIT profile has no ``rr_interval`` event value at all
(``fitdecode``'s ``FIELD_TYPES['event'].enum`` holds 46 values, none of
them RR-related; an unrecognised value renders as a raw int, never a
name). A prior revision of this module matched on
``event == "rr_interval"``: that branch could never fire on any real
file, and the test covering it fabricated the enum value to make it
pass -- exactly the green-test/dead-feature failure
``.claude/rules/project-testing.md`` exists to prevent. Rather than
ship a placeholder that reads as working, the carrier is left
unimplemented and recorded as a known gap in F003's decision log; it
needs a real device reference (or a fixture) documenting the actual
mechanism before it can be written honestly.

The developer-field carrier is implemented but has no real fixture in
this repo's corpus to verify against (no fixture carries an
RR-bearing developer field), so it is matched conservatively --
word-boundary name match plus a millisecond-unit check where the
field declares units. This is the same accepted
fixture-dependency caveat as ``mapping.py``'s
``_GPS_DEGRADED_ACCURACY_THRESHOLD_M``.
"""

from __future__ import annotations

import re

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

# Spec §2.2.3 tier enum value for a raw in-activity RR stream (§2.3.4
# step 4). Not the carrier -- see module docstring.
RR_SOURCE_CHEST_STRAP = "chest_strap_ecg"

# Developer-field carrier matching. "rr" must stand alone as a token:
# "RR", "RR Interval", "rr_interval", "rr_ms", "R-R Interval" match,
# while "Corrected Power", "Horizontal Error" and "Terrain" do not.
#
# A bare "rr" substring previously matched all of the latter, and a
# matched field would be injected into the RR stream as milliseconds
# *and* flip hr_source to chest_strap, un-gating HR metrics on a
# wrist-only session (§2.4.2). The first fix for that used \brr\b,
# which over-corrected: "_" is a word character to \b, so every
# snake_case spelling (rr_interval, rr_ms) was silently rejected --
# and snake_case is at least as likely as "RR Interval" for a
# developer field. Letter-boundary lookarounds accept both, and the
# optional separator also accepts the "R-R Interval" spelling (which
# has no literal "rr" substring at all).
#
# The lookarounds are what keep the rejections: in "Corrected",
# "Error" and "Terrain" the doubled r is flanked by letters, so none
# of them match.
_DEV_FIELD_RR_NAME = re.compile(r"(?<![A-Za-z])r[-_]?r(?![A-Za-z])", re.IGNORECASE)

# Accepted unit spellings for a developer field declaring milliseconds.
# A field declaring anything else (W, m, bpm, ...) is not a beat
# interval regardless of its name.
_MS_UNITS = {"ms", "milliseconds", "millisecond"}


def _hrv_candidates(msg) -> list[tuple[float, str]]:
    time_values = msg.get_value("time", fallback=None)
    if time_values is None:
        return []
    # fitdecode returns a scalar (not a tuple) when an array field
    # carries exactly one element -- see reader.py's
    # "elif len(raw_value) > 1: tuple(...) else: base_type.parse(...)".
    # An hrv message defined with a single time slot is legal FIT, and
    # iterating the resulting float would raise TypeError out of
    # to_canonical() as an unhandled 500.
    if not isinstance(time_values, (tuple, list)):
        time_values = (time_values,)
    return [
        (value * _MS_PER_S, "hrv")
        for value in time_values
        if isinstance(value, (int, float))
    ]


def _is_ms_developer_field(field_data) -> bool:
    if not isinstance(field_data.field, fitdecode.types.DevField):
        return False
    if not _DEV_FIELD_RR_NAME.search(field_data.name or ""):
        return False
    units = getattr(field_data.field, "units", None)
    # Units are optional in a field_description; when declared they
    # must say milliseconds, when absent the name match stands alone.
    return units is None or str(units).strip().lower() in _MS_UNITS


def _developer_field_candidates(msg) -> list[tuple[float, str]]:
    candidates: list[tuple[float, str]] = []
    for field_data in msg.fields:
        if not _is_ms_developer_field(field_data):
            continue
        value = field_data.value
        # Developer fields can legally carry strings and arrays; a
        # blind float() on those raises out of the pipeline as a 500.
        if not isinstance(value, (int, float)):
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
    # No usable neighbourhood (single-beat series, or an all-zero
    # window) -- the absolute band above is the only check that applies.
    if local_median is None or local_median <= 0:
        return False
    return abs(rr_ms - local_median) / local_median > _LOCAL_MEDIAN_DEVIATION_FRACTION


def reconstruct(messages: list[fitdecode.FitDataMessage]) -> list[RRInterval]:
    """Merge every implemented RR carrier in ``messages`` into one
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
            rr_source=RR_SOURCE_CHEST_STRAP,
            rr_carrier=carrier,
            is_artefact=_is_artefact(rr_ms, _local_median(values, seq)),
        )
        for seq, (rr_ms, carrier) in enumerate(candidates)
    ]


def valid_fraction(rr_intervals: list[RRInterval]) -> float:
    """Fraction of ``rr_intervals`` not flagged ``is_artefact`` -- the
    ``rr_valid_fraction`` quality weight of spec §2.2.3/§2.4.3.

    Spec §2.4.3 carries a low-valid-fraction series at reduced
    confidence rather than presenting it as clean; §3's raw-RR tier
    drops a capture retaining under 80% of beats. Computing it here
    and persisting it on the session (see ``pipeline.py``) is what
    gives those consumers something to read.
    """
    if not rr_intervals:
        return 0.0
    valid = sum(1 for rr in rr_intervals if not rr.is_artefact)
    return valid / len(rr_intervals)
