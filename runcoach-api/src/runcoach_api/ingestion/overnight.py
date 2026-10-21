"""An HRV_STATUS file (``file_id.type`` 68) mapped to one overnight night, or to no night.

The watch writes one HRV_STATUS file per night: one ``hrv_status_summary``
message (fitdecode's name for message 370) with Garmin's whole-night rMSSD
average, and one ``hrv_value`` (371) every five minutes from sleep start to
wake. ``map_night`` turns the file's bytes into the one ``sessions`` row the
HRV trend reads as its ``health_api_overnight`` tier, with the series and the
quarantined sidecar beside it; it writes nothing. A night has no records, no
session message and no sport, so it does not go through
``mapping.to_canonical``; the helpers it shares with activities
(``_group_by_name``, ``_build_source_device``, ``derive_session_id``) are
imported from there.

**Raw over derived.** The only number stored as the night's rMSSD is the
file's own ``last_night_average``, the one name in ``OVERNIGHT_INPUT_FIELDS``.
Garmin's status label, its weekly average and its three baseline bounds are
on the same message and are quarantined vendor-derived values: they reach the sidecar
through ``quarantine.extract`` and are never spelled here, which is what the
boundary test over this module's source reconciles. ``timestamp`` and
``last_night_5_min_high`` are read and stored but decide nothing; ``value``
is each ``hrv_value``'s own reading, stored in the series.

**The outcomes.**

- ``NoNight`` -- no ``hrv_status_summary``, or a ``last_night_average`` that
  is absent (the invalid sentinel, which fitdecode gives as ``None``), 0,
  negative or non-finite. Nothing of such a file is stored; the midnight
  files and lone-morning files the export holds are of this kind.
- ``Night`` -- a finite positive average. ``start_time`` is the summary's
  timestamp (near the wake time); the series is one ``(t, rmssd_ms)`` per
  ``hrv_value``, in file order, with the invalid sentinel (and a non-finite
  value) kept as ``None`` and counted in ``hrv_values`` but not in
  ``hrv_values_valid``; a value with no timestamp is dropped and counted
  nowhere. ``duration_s`` is the span from the first to the last value's
  timestamp (0.0 with no series), not the sleep time.
- ``OvernightSeriesError`` (a 400) -- two values at one timestamp, or a
  second summary message. ``MissingCanonicalFieldError`` (a 400) -- a summary
  with no timestamp, so no ``start_time``. ``FitParseFailure`` (a 400) --
  the decoder's own refusals: a series truncated mid-message, a
  ``date_time`` out of range.

Timestamps are ``isoformat()`` of the aware UTC datetimes fitdecode decodes
(``+00:00``, never ``Z``), the spelling ``db.read_hrv_rows`` requires.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

import fitdecode

from runcoach_api.ingestion import fit_parser, quarantine
from runcoach_api.ingestion.exceptions import MissingCanonicalFieldError, OvernightSeriesError
from runcoach_api.ingestion.mapping import _build_source_device, _group_by_name, derive_session_id
from runcoach_api.models import Context, Session

#: The one ``hrv_status_summary`` field stored as the night's rMSSD. The
#: boundary test reconciles this against the names this module's source reads
#: and against ``quarantine._VENDOR_DERIVED_FIELDS["hrv_status_summary"]``.
OVERNIGHT_INPUT_FIELDS: tuple[str, ...] = ("last_night_average",)

#: Names this module reads that decide nothing about the value: the summary's
#: ``timestamp`` (the row's ``start_time``) and ``last_night_5_min_high``
#: (stored in the summary), and each ``hrv_value``'s ``timestamp`` and
#: ``value`` (the series). Declared so the reconciliation has a bucket for
#: every read; not an escape hatch for a quarantined name, which the boundary
#: test refuses in either bucket.
OVERNIGHT_NON_INPUT_READS: tuple[str, ...] = ("timestamp", "last_night_5_min_high", "value")

#: ``file_id.type`` of an HRV_STATUS file, as ``context.provenance.raw_file_type``
#: records it (fitdecode's profile names no type 68).
HRV_STATUS_FILE_TYPE = 68

NO_NIGHT = "no_night"

_SUMMARY_MESSAGE = "hrv_status_summary"
_VALUE_MESSAGE = "hrv_value"


@dataclass(frozen=True)
class NoNight:
    """The file holds no night: nothing is stored and the upload is skipped."""

    reason: str = NO_NIGHT


@dataclass(frozen=True)
class Night:
    """One night: the ``sessions`` row, its 5-minute series and its quarantined sidecar.

    ``values`` is ``[(t, rmssd_ms | None), ...]`` in file order, one per
    stored ``hrv_value``; ``sidecar`` is ``{field_name: str(value)}``, the
    shape ``db.persist`` takes for ``quarantine_values``.
    """

    session: Session
    values: list[tuple[str, float | None]]
    sidecar: dict[str, str]


def map_night(fit_bytes: bytes) -> Night | NoNight:
    """The night in ``fit_bytes``, or ``NoNight`` when the file holds none.

    Raises:
        NotAFitFileError, FitParseFailure, TooManyRecordsError: from
            ``fit_parser.decode``, as for any upload.
        OvernightSeriesError: two ``hrv_value`` messages at one timestamp,
            or more than one ``hrv_status_summary`` message.
        MissingCanonicalFieldError: the summary carries no timestamp.
    """
    messages = fit_parser.decode(fit_bytes)
    summaries = [m for m in messages if m.name == _SUMMARY_MESSAGE]
    if len(summaries) > 1:
        raise OvernightSeriesError(
            f"{len(summaries)} {_SUMMARY_MESSAGE} messages; a night has exactly one"
        )
    if not summaries:
        return NoNight()
    summary = summaries[0]

    average = summary.get_value("last_night_average", fallback=None)
    if not _finite_positive(average):
        return NoNight()

    start = summary.get_value("timestamp", fallback=None)
    if not isinstance(start, datetime):
        raise MissingCanonicalFieldError(
            "start_time", f"the {_SUMMARY_MESSAGE} message carries no timestamp"
        )
    start_time = start.isoformat()

    values = _series(m for m in messages if m.name == _VALUE_MESSAGE)
    stamps = [t for t, _ in values]
    five_min_high = summary.get_value("last_night_5_min_high", fallback=None)

    by_name = _group_by_name(messages)
    source_device = _build_source_device(by_name)
    row = Session(
        session_id=derive_session_id(source_device, start_time),
        sport="other",
        source_vendor="garmin",
        start_time=start_time,
        activity_tag="overnight_hrv",
        source_device=source_device,
        hr_source="wrist_ppg",
        rmssd_precomputed=float(average),
        resting_rmssd_ms=float(average),
        hrv_source_tier="health_api_overnight",
        rr_source="overnight_ppg",
        name="Overnight HRV",
        quality_flags=[],
        summary={
            "duration_s": _span_seconds(stamps),
            "series_start": min(stamps) if stamps else None,
            "last_night_5_min_high": five_min_high,
            "hrv_values": len(values),
            "hrv_values_valid": sum(1 for _, v in values if v is not None),
        },
        context=Context(
            ingested_at=datetime.now(timezone.utc).isoformat(),
            provenance={"raw_file_type": HRV_STATUS_FILE_TYPE},
        ),
    )
    return Night(session=row, values=values, sidecar=quarantine.extract(messages))


def _finite_positive(value: object) -> bool:
    """True for a real number above 0: not ``None``, 0, -0.0, negative, inf or NaN."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _series(messages: Iterable[fitdecode.FitDataMessage]) -> list[tuple[str, float | None]]:
    """``(t, rmssd_ms | None)`` per ``hrv_value`` with a timestamp, in file order.

    A message with no timestamp is dropped. A value that is ``None`` (the
    invalid sentinel) or not finite is kept as ``None``. Two messages at one
    timestamp refuse the file: the series' key is the timestamp.
    """
    values: list[tuple[str, float | None]] = []
    seen: set[str] = set()
    for message in messages:
        stamp = message.get_value("timestamp", fallback=None)
        if not isinstance(stamp, datetime):
            continue
        t = stamp.isoformat()
        if t in seen:
            raise OvernightSeriesError(f"two {_VALUE_MESSAGE} messages at {t}")
        seen.add(t)
        values.append((t, _series_value(message.get_value("value", fallback=None))))
    return values


def _series_value(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _span_seconds(stamps: list[str]) -> float:
    """Seconds from the earliest to the latest value timestamp; 0.0 with no series."""
    if not stamps:
        return 0.0
    first = datetime.fromisoformat(min(stamps))
    last = datetime.fromisoformat(max(stamps))
    return (last - first).total_seconds()


__all__ = [
    "HRV_STATUS_FILE_TYPE",
    "NO_NIGHT",
    "Night",
    "NoNight",
    "OVERNIGHT_INPUT_FIELDS",
    "OVERNIGHT_NON_INPUT_READS",
    "map_night",
]
