"""Canonical schema dataclasses shared by persistence (db.py) and the API.

Plain ``@dataclass`` shapes (not pydantic) for the vendor-neutral
canonical raw stream produced by the ingestion pipeline: a
``Session`` header, per-sample ``Record``s, reconstructed
``RRInterval`` beats, and parse-time ``Context`` metadata.

Only the fields named in T017's spec are defined -- no silent extra
fields, mirroring the no-default rigor in ``config.py``'s
``AppConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    session_id: str
    sport: str
    source_vendor: str
    start_time: str
    athlete_id: str | None = None
    activity_tag: str | None = None
    source_device: str | None = None
    # §2.2.3 descriptor, not a numeric period: "1hz" / "smart" /
    # "irregular", set by the recording-mode gate (§2.4.1). Matches the
    # TEXT column in db.py's sessions DDL.
    recording_interval: str | None = None
    hr_source: str | None = None
    # Surviving-beat fraction of the reconstructed RR series (§2.2.3,
    # §2.4.3). None when the session carries no RR stream at all --
    # distinct from 0.0, which means "had beats, none survived".
    rr_valid_fraction: float | None = None
    # --- resting-HRV capture (F004, §2.2.3) -------------------------------
    # Device-computed resting rMSSD in milliseconds. Numeric wrist tiers
    # only -- None for Tier 1 (chest-strap raw), where the value is
    # computed from the beats instead. Bypasses §2.4.3 artefact
    # filtering, there being no beats to filter, so the non-positive
    # check is the only validity gate it gets.
    rmssd_precomputed: float | None = None
    # The **resolved** resting rMSSD in milliseconds -- the field E003
    # reads, whichever tier produced the reading: the device value on
    # Tier 2, the system-computed value on Tier 1. ``rmssd_precomputed``
    # above keeps its narrower device-only meaning as the audit record,
    # so the two are never interchangeable: a successful Tier-1 reading
    # populates this and leaves that one None.
    #
    # Always > 0 when set (F004's non-positive gate) and None when no
    # reading was derived, so a consumer need not know the tier to know
    # what a value means. Added by the 2026-09-06 amendment with **no
    # writer** (T055); T065 populates it on both tiers. Pre-amendment
    # rows keep it None -- there is deliberately no backfill.
    resting_rmssd_ms: float | None = None
    # Which tier the reading came from: "chest_strap_raw" /
    # "health_snapshot" / "health_api_overnight". None on every
    # non-HRV session, which is most of them.
    hrv_source_tier: str | None = None
    # The **session-level** rr_source of §2.2.3 -- distinct from
    # ``RRInterval.rr_source`` below, which is per beat and lives in a
    # different table. §2.2.3 defines the enum at RR-stream level, but
    # F003 modelled it per beat, and a Tier-2 reading has no beats at
    # all -- so the same enum needs a session-level home too. The
    # per-beat column stays the Tier-1 carrier; this one is what E003
    # reads.
    rr_source: str | None = None
    # --- per-unit sensor identity (F007) ---------------------------------
    # The own unit serial of the ANT+ heart-rate sensor *connected* when
    # the session was recorded (usually a chest strap; a watch broadcasting
    # optical HR over ANT+ counts too), not the recording watch's
    # (``source_device``). It records the pairing, not the HR provenance:
    # ``hr_source`` reads chest_strap for any connected heart-rate sensor
    # with HR, so three accepted limits read so too: a strap paired but
    # not worn, an external optical sensor (an armband, or that
    # broadcasting watch: the device type does not tell optical from
    # ECG), and a strap that drops out mid-activity, which marks the
    # whole session. None = unknown (no ANT+
    # heart-rate entry, no valid serial, conflicting serials, or a pre-F007
    # session not yet deleted and re-uploaded) -- never "no sensor". Unread
    # (F007 AC6); mapping._resolve_hr_sensor_serial resolves it.
    hr_sensor_serial: int | None = None
    # --- profile settings the file carried (F016) ------------------------
    # The first user_profile / zones_target message's values, as
    # ingestion.profile_values maps them; None = the file had no value
    # (invalid, missing, 0 or negative for the five numeric fields, or a
    # non-finite body mass). Stored whatever the sport. Not in GET /sessions/{id}.
    sex: str | None = None
    body_mass_kg: float | None = None
    height_cm: int | None = None
    resting_hr_bpm: int | None = None
    max_hr_bpm: int | None = None
    threshold_hr_bpm: int | None = None
    garmin_activity_class: int | None = None
    quality_flags: list[str] = field(default_factory=list)
    summary: dict[str, Any] | None = None
    context: Context | None = None


@dataclass
class Record:
    t: float
    lat: float | None = None
    lon: float | None = None
    distance: float | None = None
    speed: float | None = None
    heart_rate: float | None = None
    cadence: float | None = None
    altitude: float | None = None
    power: float | None = None
    power_model: str | None = None
    vertical_oscillation: float | None = None
    ground_contact_time: float | None = None
    gct_balance: float | None = None
    step_length: float | None = None
    temperature: float | None = None
    gps_degraded: bool | None = None
    sample_quality: list[str] = field(default_factory=list)


@dataclass
class RRInterval:
    seq: int | None = None
    rr_ms: float | None = None
    # Tier enum per §2.2.3 (chest_strap_ecg / overnight_ppg /
    # health_snapshot_ppg / other) -- the tier §3 ranks by fidelity and
    # never mixes within one band (research/00 HRV-04, HRV-06, T-21).
    rr_source: str | None = None
    # Which FIT carrier supplied this beat (§2.3.4 step 3). Kept
    # separate so the tier enum above isn't overloaded.
    rr_carrier: str | None = None
    is_artefact: bool | None = None


@dataclass
class Context:
    """Environmental + provenance metadata for one ingested session (§2.2.4).

    ``env_*`` fields require an external weather source keyed to the
    session's time/location -- unavailable at ingestion time for the
    FIT-upload route, so they stay ``None`` here rather than being
    guessed from the device thermistor (`record.temperature`).
    """

    ingested_at: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    env_temperature_c: float | None = None
    env_humidity_pct: float | None = None
    env_wind_ms: float | None = None
    env_wind_dir: str | None = None
    subjective: dict[str, Any] | None = None
