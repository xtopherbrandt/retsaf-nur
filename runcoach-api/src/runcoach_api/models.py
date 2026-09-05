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
    # health_snapshot_ppg / other) -- what §3's tier weighting reads.
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
