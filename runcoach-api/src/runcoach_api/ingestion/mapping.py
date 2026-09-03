"""FIT record/session messages -> canonical schema (spec §2.3.3).

Native ``record``/``session`` fields are mapped here, plus (T022)
record-level *developer* fields -- a mechanism where a third-party
sensor/app (e.g. a Stryd pod) defines custom field names via
``field_description`` messages and tags ``record`` messages with data
under those custom definitions instead of standard FIT profile
fields. RR reconstruction and data-quality gating are likewise
untouched stubs; this module computes nothing, it only maps and
converts raw measured fields (the raw-over-derived principle).

Developer-field note (verified against the real fixture,
``tests/fixtures/dev_fields_run.fit``, via a one-off ``fitdecode``
inspection before writing this): ``fitdecode`` already resolves
developer field names onto ``FieldData.name`` from the file's own
``field_description`` messages (e.g. ``"Ground Time"``, ``"Vertical
Oscillation"``) -- no manual ``developer_data_id``/
``field_description`` lookup table is needed; ``get_value()`` accepts
those resolved names the same way it accepts native field names.
Precedence: a native field value always wins over a developer field
for the same canonical slot (developer fields fill gaps only) --
there was no existing precedence comment from the native-field task to
follow, so this defers to the FIT-profile-standard path as the
established source. Unit note: this fixture's native
``vertical_oscillation`` is reported by fitdecode in millimeters,
while the Stryd developer field ``"Vertical Oscillation"`` is in
centimeters (per its own ``field_description`` units) -- converted
x10 to millimeters here so native- and developer-sourced values are
comparable. ``"Ground Time"`` (developer field, Milliseconds) already
shares its unit with native ``stance_time`` -- no conversion needed.

Conversion-vs-passthrough note (verified against the real fixture,
``tests/fixtures/sample_run.fit``, via a one-off ``fitdecode``
inspection before writing this): ``fitdecode``'s ``DefaultDataProcessor``
already applies the FIT profile's scale/offset for ``distance``,
``enhanced_speed``/``speed``, ``enhanced_altitude``/``altitude``,
``vertical_oscillation``, ``stance_time``, ``stance_time_percent``, and
``step_length`` -- those come back from ``get_value()`` already in
canonical units and are passed through unchanged. Two conversions are
*not* done by the processor and must be applied here:

- ``position_lat`` / ``position_long`` come back as raw semicircle
  integers (``value == raw_value``) -- converted with
  ``degrees = semicircles * (180 / 2**31)``.
- ``cadence`` / ``fractional_cadence`` come back as raw rpm ints with
  no ×2 folding -- combined with
  ``spm = (cadence + fractional_cadence) * 2``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import fitdecode

from runcoach_api import __version__
from runcoach_api.models import Context, Record, Session

SEMICIRCLE_TO_DEGREES = 180 / 2**31

# T029 gps_degraded wiring (code-review fix): FIT's record.gps_accuracy
# (field 31) is reported in meters (verified via fitdecode's own
# profile field metadata, not a fixture -- no real fixture in this
# repo's test corpus, checked via a one-off fitdecode inspection,
# populates gps_accuracy on any record). >10m horizontal accuracy is
# the commonly-used "moderate/degraded" threshold for consumer GPS
# accuracy grading; this is unverified against real degraded-GPS data
# and should be revisited once a fixture with the field populated
# exists.
_GPS_DEGRADED_ACCURACY_THRESHOLD_M = 10

# Developer field name -> (Record attribute, multiplier to convert the
# developer field's unit into the canonical unit already established
# by the native-field path). Only fields with a canonical slot appear
# here; anything else encountered on a record is preserved in
# provenance instead of being dropped or guessed into a slot.
DEV_FIELD_CANONICAL_MAP: dict[str, tuple[str, float]] = {
    "Vertical Oscillation": ("vertical_oscillation", 10.0),  # cm -> mm
    "Ground Time": ("ground_contact_time", 1.0),  # ms, already matches
}


def _messages_named(messages: list[fitdecode.FitDataMessage], name: str) -> list:
    return [m for m in messages if m.name == name]


def _first_named(messages: list[fitdecode.FitDataMessage], name: str):
    for m in messages:
        if m.name == name:
            return m
    return None


def _semicircles_to_degrees(value: float | None) -> float | None:
    if value is None:
        return None
    return value * SEMICIRCLE_TO_DEGREES


# Sentinel used when no source device can be determined (e.g.
# non-Garmin devices, or a file missing file_id/device_info). SQLite's
# UNIQUE(source_device, start_time) constraint treats every NULL as
# distinct from every other NULL, which would silently defeat dedup
# for two such uploads sharing a start_time -- a non-NULL sentinel
# string keeps the existing db-constraint-based dedup working.
_UNKNOWN_SOURCE_DEVICE = "unknown"


def _build_source_device(messages: list[fitdecode.FitDataMessage]) -> str:
    file_id = _first_named(messages, "file_id")
    device_info = _first_named(messages, "device_info")

    product = None
    if file_id is not None:
        product = file_id.get_value("garmin_product", fallback=None)
    if product is None and device_info is not None:
        product = device_info.get_value("garmin_product", fallback=None)

    firmware = device_info.get_value("software_version", fallback=None) if device_info else None

    if product is None:
        return _UNKNOWN_SOURCE_DEVICE
    if firmware is None:
        return str(product)
    return f"{product} fw{firmware}"


def _build_summary(session_msg) -> dict:
    if session_msg is None:
        return {}

    def val(name):
        return session_msg.get_value(name, fallback=None)

    avg_speed = val("enhanced_avg_speed")
    if avg_speed is None:
        avg_speed = val("avg_speed")

    max_speed = val("enhanced_max_speed")
    if max_speed is None:
        max_speed = val("max_speed")

    summary = {
        "distance_m": val("total_distance"),
        "duration_s": val("total_timer_time"),
        "avg_heart_rate": val("avg_heart_rate"),
        "max_heart_rate": val("max_heart_rate"),
        "avg_speed": avg_speed,
        "max_speed": max_speed,
        "total_ascent_m": val("total_ascent"),
        "total_descent_m": val("total_descent"),
        "calories": val("total_calories"),
    }
    return {k: v for k, v in summary.items() if v is not None}


def _build_context(unresolved_developer_fields: dict | None = None) -> Context:
    provenance = {
        # §2.7.2 -- direct FIT file upload via POST /sessions, no
        # Garmin Connect Developer Program partnership involved.
        "access_route": "direct_fit_file_upload",
        "adapter": "garmin_fit",
        "adapter_version": __version__,
    }
    if unresolved_developer_fields:
        # T022 -- developer fields with no canonical Record slot (e.g.
        # a Stryd pod's "Power"/"Form Power") are preserved here rather
        # than silently dropped or guessed into a slot. One example
        # value per field name is enough for provenance purposes.
        provenance["unresolved_developer_fields"] = unresolved_developer_fields
    return Context(
        ingested_at=datetime.now(timezone.utc).isoformat(),
        provenance=provenance,
        # No external weather source is available at ingestion time on
        # this route -- left null rather than approximated from the
        # device thermistor (record.temperature is not ambient, §2.2.4).
        env_temperature_c=None,
        env_humidity_pct=None,
        env_wind_ms=None,
        env_wind_dir=None,
        subjective=None,
    )


def _resolve_developer_fields(msg) -> tuple[dict, dict]:
    """Split a record message's developer fields into resolved
    canonical-slot values and unresolved (no canonical slot) values.

    Returns ``(resolved, unresolved)``, both keyed by ``Record``
    attribute name / raw developer field name respectively.
    """
    resolved: dict = {}
    unresolved: dict = {}
    for field_data in msg.fields:
        if not isinstance(field_data.field, fitdecode.types.DevField):
            continue
        name = field_data.name
        value = field_data.value
        if value is None:
            continue
        mapping_entry = DEV_FIELD_CANONICAL_MAP.get(name)
        if mapping_entry is None:
            unresolved[name] = value
            continue
        attr, multiplier = mapping_entry
        resolved[attr] = value * multiplier
    return resolved, unresolved


def _build_record(msg, start_dt: datetime) -> tuple[Record | None, dict]:
    ts = msg.get_value("timestamp", fallback=None)
    if ts is None:
        return None, {}

    lat = _semicircles_to_degrees(msg.get_value("position_lat", fallback=None))
    lon = _semicircles_to_degrees(msg.get_value("position_long", fallback=None))

    speed = msg.get_value("enhanced_speed", fallback=None)
    if speed is None:
        speed = msg.get_value("speed", fallback=None)

    altitude = msg.get_value("enhanced_altitude", fallback=None)
    if altitude is None:
        altitude = msg.get_value("altitude", fallback=None)

    raw_cadence = msg.get_value("cadence", fallback=None)
    fractional_cadence = msg.get_value("fractional_cadence", fallback=None)
    cadence = None
    if raw_cadence is not None:
        cadence = (raw_cadence + (fractional_cadence or 0)) * 2

    power = msg.get_value("power", fallback=None)
    power_model = "garmin_native" if power is not None else None

    # Developer-field running dynamics (T022): native wins when both are
    # present for the same slot -- developer fields fill gaps only.
    dev_resolved, dev_unresolved = _resolve_developer_fields(msg)

    vertical_oscillation = msg.get_value("vertical_oscillation", fallback=None)
    if vertical_oscillation is None:
        vertical_oscillation = dev_resolved.get("vertical_oscillation")

    ground_contact_time = msg.get_value("stance_time", fallback=None)
    if ground_contact_time is None:
        ground_contact_time = dev_resolved.get("ground_contact_time")

    gps_accuracy = msg.get_value("gps_accuracy", fallback=None)
    gps_degraded = None
    if gps_accuracy is not None:
        gps_degraded = gps_accuracy > _GPS_DEGRADED_ACCURACY_THRESHOLD_M

    record = Record(
        t=(ts - start_dt).total_seconds(),
        lat=lat,
        lon=lon,
        distance=msg.get_value("distance", fallback=None),
        speed=speed,
        heart_rate=msg.get_value("heart_rate", fallback=None),
        cadence=cadence,
        altitude=altitude,
        power=power,
        power_model=power_model,
        vertical_oscillation=vertical_oscillation,
        ground_contact_time=ground_contact_time,
        gct_balance=msg.get_value("stance_time_percent", fallback=None),
        step_length=msg.get_value("step_length", fallback=None),
        temperature=msg.get_value("temperature", fallback=None),
        gps_degraded=gps_degraded,
        sample_quality=[],
    )
    return record, dev_unresolved


def to_canonical(messages: list[fitdecode.FitDataMessage]) -> tuple[Session, list[Record]]:
    """Map decoded FIT data messages into a ``(Session, [Record, ...])`` pair.

    Native ``record``/``session`` fields plus (T022) record-level
    developer-field running dynamics. RR reconstruction and quality
    gating are out of this function's scope (handled elsewhere in the
    pipeline).
    """
    session_msg = _first_named(messages, "session")
    sport_msg = _first_named(messages, "sport")

    sport = None
    if session_msg is not None:
        sport = session_msg.get_value("sport", fallback=None)
    if sport is None and sport_msg is not None:
        sport = sport_msg.get_value("sport", fallback=None)

    start_time = session_msg.get_value("start_time", fallback=None) if session_msg else None
    if start_time is None:
        # Fall back to the earliest record timestamp -- still a native
        # field, no fabrication.
        record_msgs = _messages_named(messages, "record")
        timestamps = [m.get_value("timestamp", fallback=None) for m in record_msgs]
        timestamps = [t for t in timestamps if t is not None]
        start_time = min(timestamps) if timestamps else datetime.now(timezone.utc)

    records: list[Record] = []
    unresolved_developer_fields: dict = {}
    for record_msg in _messages_named(messages, "record"):
        record, dev_unresolved = _build_record(record_msg, start_time)
        if record is not None:
            records.append(record)
        for name, value in dev_unresolved.items():
            unresolved_developer_fields.setdefault(name, value)

    session = Session(
        session_id=str(uuid.uuid4()),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time.isoformat(),
        source_device=_build_source_device(messages),
        summary=_build_summary(session_msg),
        context=_build_context(unresolved_developer_fields),
    )

    return session, records
