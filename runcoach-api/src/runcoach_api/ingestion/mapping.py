"""FIT record/session messages -> canonical schema (spec §2.3.3).

Only native ``record``/``session`` fields are mapped here -- developer
fields (running dynamics frequently arrive that way, per §2.3.5) are
out of scope (a later task's responsibility). RR reconstruction and
data-quality gating are likewise untouched stubs; this module computes
nothing, it only maps and converts raw measured fields (the
raw-over-derived principle).

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


def _build_source_device(messages: list[fitdecode.FitDataMessage]) -> str | None:
    file_id = _first_named(messages, "file_id")
    device_info = _first_named(messages, "device_info")

    product = None
    if file_id is not None:
        product = file_id.get_value("garmin_product", fallback=None)
    if product is None and device_info is not None:
        product = device_info.get_value("garmin_product", fallback=None)

    firmware = device_info.get_value("software_version", fallback=None) if device_info else None

    if product is None:
        return None
    if firmware is None:
        return str(product)
    return f"{product} fw{firmware}"


def _build_summary(session_msg) -> dict:
    if session_msg is None:
        return {}

    def val(name):
        return session_msg.get_value(name, fallback=None)

    summary = {
        "distance_m": val("total_distance"),
        "duration_s": val("total_timer_time"),
        "avg_heart_rate": val("avg_heart_rate"),
        "max_heart_rate": val("max_heart_rate"),
        "avg_speed": val("enhanced_avg_speed") or val("avg_speed"),
        "max_speed": val("enhanced_max_speed") or val("max_speed"),
        "total_ascent_m": val("total_ascent"),
        "total_descent_m": val("total_descent"),
        "calories": val("total_calories"),
    }
    return {k: v for k, v in summary.items() if v is not None}


def _build_context() -> Context:
    return Context(
        ingested_at=datetime.now(timezone.utc).isoformat(),
        provenance={
            # §2.7.2 -- direct FIT file upload via POST /sessions, no
            # Garmin Connect Developer Program partnership involved.
            "access_route": "direct_fit_file_upload",
            "adapter": "garmin_fit",
            "adapter_version": __version__,
        },
        # No external weather source is available at ingestion time on
        # this route -- left null rather than approximated from the
        # device thermistor (record.temperature is not ambient, §2.2.4).
        env_temperature_c=None,
        env_humidity_pct=None,
        env_wind_ms=None,
        env_wind_dir=None,
        subjective=None,
    )


def _build_record(msg, start_dt: datetime) -> Record | None:
    ts = msg.get_value("timestamp", fallback=None)
    if ts is None:
        return None

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

    return Record(
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
        vertical_oscillation=msg.get_value("vertical_oscillation", fallback=None),
        ground_contact_time=msg.get_value("stance_time", fallback=None),
        gct_balance=msg.get_value("stance_time_percent", fallback=None),
        step_length=msg.get_value("step_length", fallback=None),
        temperature=msg.get_value("temperature", fallback=None),
        sample_quality=[],
    )


def to_canonical(messages: list[fitdecode.FitDataMessage]) -> tuple[Session, list[Record]]:
    """Map decoded FIT data messages into a ``(Session, [Record, ...])`` pair.

    Native ``record``/``session`` fields only -- developer fields,
    RR reconstruction, and quality gating are all out of this
    function's scope (handled elsewhere in the pipeline, or later
    tasks).
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

    session = Session(
        session_id=str(uuid.uuid4()),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time.isoformat(),
        source_device=_build_source_device(messages),
        summary=_build_summary(session_msg),
        context=_build_context(),
    )

    records: list[Record] = []
    for record_msg in _messages_named(messages, "record"):
        record = _build_record(record_msg, start_time)
        if record is not None:
            records.append(record)

    return session, records
