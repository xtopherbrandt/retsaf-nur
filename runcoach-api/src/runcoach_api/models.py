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
    recording_interval: float | None = None
    hr_source: str | None = None
    quality_flags: list[str] = field(default_factory=list)
    summary: dict[str, Any] | None = None


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
    sample_quality: list[str] = field(default_factory=list)


@dataclass
class RRInterval:
    seq: int | None = None
    rr_ms: float | None = None
    rr_source: str | None = None
    is_artefact: bool | None = None


@dataclass
class Context:
    device_info: dict[str, Any] = field(default_factory=dict)
