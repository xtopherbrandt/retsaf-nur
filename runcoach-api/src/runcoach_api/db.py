"""SQLite connection factory, schema DDL, and persistence for ingestion.

``AppConfig.data_dir`` (``config.py``) is the single source of truth
for where the SQLite file lives -- no separate config field is added
here. ``get_connection`` creates the directory if needed and returns a
connection with foreign keys enabled; ``init_schema`` is idempotent
DDL for the four canonical-schema tables; ``persist`` writes one
ingested session (session header, records, RR intervals, and
quarantined sidecar values) in a single transaction.

TEXT-column serialization convention: ``sessions.quality_flags``,
``sessions.summary``, and ``records.sample_quality`` are stored as
``json.dumps(...)`` and read back via ``json.loads(...)``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runcoach_api import config as config_module
from runcoach_api.models import Record, RRInterval, Session

DB_FILENAME = "runcoach.db"


def get_connection() -> sqlite3.Connection:
    """Open (creating if needed) the SQLite database under AppConfig.data_dir."""
    app_config = config_module.load_config()
    data_dir: Path = app_config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / DB_FILENAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the canonical-schema tables if they don't already exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
          sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
          source_device TEXT, recording_interval TEXT, hr_source TEXT,
          quality_flags TEXT, summary TEXT,
          UNIQUE (source_device, start_time)
        );
        CREATE TABLE IF NOT EXISTS records (
          session_id TEXT NOT NULL REFERENCES sessions(session_id), t REAL NOT NULL,
          lat REAL, lon REAL, distance REAL, speed REAL, heart_rate REAL, cadence REAL,
          altitude REAL, power REAL, power_model TEXT, vertical_oscillation REAL,
          ground_contact_time REAL, gct_balance REAL, step_length REAL, temperature REAL,
          sample_quality TEXT
        );
        CREATE TABLE IF NOT EXISTS rr_intervals (
          session_id TEXT NOT NULL REFERENCES sessions(session_id), seq INTEGER NOT NULL,
          rr_ms REAL NOT NULL, rr_source TEXT, is_artefact INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS quarantine_sidecar (
          session_id TEXT NOT NULL REFERENCES sessions(session_id), field_name TEXT NOT NULL,
          value TEXT
        );
        """
    )
    conn.commit()


def persist(
    conn: sqlite3.Connection,
    session: Session,
    records: list[Record],
    rr_intervals: list[RRInterval],
    quarantine_values: dict,
) -> None:
    """Write one ingested session's rows in a single transaction.

    Relies on the ``UNIQUE (source_device, start_time)`` constraint on
    ``sessions`` as the sole dedup mechanism -- callers translate the
    resulting ``sqlite3.IntegrityError`` into ``DuplicateSessionError``;
    no application-level dedup-check-then-insert logic lives here.
    """
    with conn:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, athlete_id, start_time, sport, activity_tag,
                source_vendor, source_device, recording_interval, hr_source,
                quality_flags, summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.athlete_id,
                session.start_time,
                session.sport,
                session.activity_tag,
                session.source_vendor,
                session.source_device,
                session.recording_interval,
                session.hr_source,
                json.dumps(session.quality_flags),
                json.dumps(session.summary),
            ),
        )

        conn.executemany(
            """
            INSERT INTO records (
                session_id, t, lat, lon, distance, speed, heart_rate, cadence,
                altitude, power, power_model, vertical_oscillation,
                ground_contact_time, gct_balance, step_length, temperature,
                sample_quality
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    session.session_id,
                    r.t,
                    r.lat,
                    r.lon,
                    r.distance,
                    r.speed,
                    r.heart_rate,
                    r.cadence,
                    r.altitude,
                    r.power,
                    r.power_model,
                    r.vertical_oscillation,
                    r.ground_contact_time,
                    r.gct_balance,
                    r.step_length,
                    r.temperature,
                    json.dumps(r.sample_quality),
                )
                for r in records
            ],
        )

        conn.executemany(
            """
            INSERT INTO rr_intervals (session_id, seq, rr_ms, rr_source, is_artefact)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    session.session_id,
                    rr.seq,
                    rr.rr_ms,
                    rr.rr_source,
                    int(bool(rr.is_artefact)),
                )
                for rr in rr_intervals
            ],
        )

        conn.executemany(
            """
            INSERT INTO quarantine_sidecar (session_id, field_name, value)
            VALUES (?, ?, ?)
            """,
            [
                (session.session_id, field_name, json.dumps(value))
                for field_name, value in quarantine_values.items()
            ],
        )
