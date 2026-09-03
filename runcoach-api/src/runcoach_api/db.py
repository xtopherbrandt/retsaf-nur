"""SQLite connection factory, schema DDL, and persistence for ingestion.

``AppConfig.data_dir`` (``config.py``) is the single source of truth
for where the SQLite file lives -- no separate config field is added
here. ``get_connection`` creates the directory if needed and returns a
connection with foreign keys enabled; ``init_schema`` is idempotent
DDL for the four canonical-schema tables; ``persist`` writes one
ingested session (session header, records, RR intervals, and
quarantined sidecar values) in a single transaction.

TEXT-column serialization convention: ``sessions.quality_flags``,
``sessions.summary``, ``sessions.context``, and
``records.sample_quality`` are stored as ``json.dumps(...)`` and read
back via ``json.loads(...)``.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

from runcoach_api import config as config_module
from runcoach_api.ingestion.exceptions import DuplicateSessionError
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
          quality_flags TEXT, summary TEXT, context TEXT,
          UNIQUE (source_device, start_time)
        );
        CREATE TABLE IF NOT EXISTS records (
          session_id TEXT NOT NULL REFERENCES sessions(session_id), t REAL NOT NULL,
          lat REAL, lon REAL, distance REAL, speed REAL, heart_rate REAL, cadence REAL,
          altitude REAL, power REAL, power_model TEXT, vertical_oscillation REAL,
          ground_contact_time REAL, gct_balance REAL, step_length REAL, temperature REAL,
          gps_degraded INTEGER, sample_quality TEXT
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


def _insert_session(conn: sqlite3.Connection, session: Session) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, athlete_id, start_time, sport, activity_tag,
            source_vendor, source_device, recording_interval, hr_source,
            quality_flags, summary, context
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            json.dumps(dataclasses.asdict(session.context)) if session.context else None,
        ),
    )


def _insert_records(conn: sqlite3.Connection, session_id: str, records: list[Record]) -> None:
    conn.executemany(
        """
        INSERT INTO records (
            session_id, t, lat, lon, distance, speed, heart_rate, cadence,
            altitude, power, power_model, vertical_oscillation,
            ground_contact_time, gct_balance, step_length, temperature,
            gps_degraded, sample_quality
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                session_id,
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
                None if r.gps_degraded is None else int(bool(r.gps_degraded)),
                json.dumps(r.sample_quality),
            )
            for r in records
        ],
    )


def _insert_rr_intervals(
    conn: sqlite3.Connection, session_id: str, rr_intervals: list[RRInterval]
) -> None:
    conn.executemany(
        """
        INSERT INTO rr_intervals (session_id, seq, rr_ms, rr_source, is_artefact)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                session_id,
                rr.seq,
                rr.rr_ms,
                rr.rr_source,
                int(bool(rr.is_artefact)),
            )
            for rr in rr_intervals
        ],
    )


def _insert_quarantine_sidecar(
    conn: sqlite3.Connection, session_id: str, quarantine_values: dict
) -> None:
    conn.executemany(
        """
        INSERT INTO quarantine_sidecar (session_id, field_name, value)
        VALUES (?, ?, ?)
        """,
        [
            (session_id, field_name, json.dumps(value))
            for field_name, value in quarantine_values.items()
        ],
    )


def persist(
    conn: sqlite3.Connection,
    session: Session,
    records: list[Record],
    rr_intervals: list[RRInterval],
    quarantine_values: dict,
) -> None:
    """Write one ingested session's rows in a single transaction.

    Relies on the ``UNIQUE (source_device, start_time)`` constraint on
    ``sessions`` as the sole dedup mechanism -- no application-level
    dedup-check-then-insert logic lives here (that would reintroduce a
    TOCTOU race under concurrent uploads). The insert is attempted
    directly; a resulting ``sqlite3.IntegrityError`` is caught here and
    translated into ``DuplicateSessionError``, carrying the
    ``session_id`` of the row that already occupies this
    ``(source_device, start_time)`` slot (looked up *after* the
    failure, never before it).

    The four inserts are split into private helpers (rather than
    inlined) so a mid-transaction failure can be simulated in tests by
    monkeypatching one of them -- ``sqlite3.Connection`` itself can't
    be monkeypatched (it's an immutable C type), so this is the seam
    that makes the chaos test possible without touching real DB
    internals.
    """
    try:
        with conn:
            _insert_session(conn, session)
            _insert_records(conn, session.session_id, records)
            _insert_rr_intervals(conn, session.session_id, rr_intervals)
            _insert_quarantine_sidecar(conn, session.session_id, quarantine_values)
    except sqlite3.IntegrityError as exc:
        cur = conn.execute(
            "SELECT session_id FROM sessions WHERE source_device = ? AND start_time = ?",
            (session.source_device, session.start_time),
        )
        row = cur.fetchone()
        if row is None:
            # Not actually the dedup constraint -- e.g. a NOT NULL
            # violation (sessions.sport) blocked the insert before
            # anything committed. Fabricating a DuplicateSessionError
            # here would misattribute a real validation failure as a
            # 409 duplicate-upload response, so let the original
            # IntegrityError propagate instead.
            raise
        raise DuplicateSessionError(row[0]) from exc


def get_session_detail(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Read one ingested session back in the canonical session/record/RR/context shape.

    Returns ``None`` when ``session_id`` doesn't exist so the caller
    (the ``GET /sessions/{id}`` route) can turn that into a 404. Does
    **not** include the quarantine sidecar -- that table is unrelated
    to the canonical schema (§2.3.6 keeps it deliberately separate).
    """
    cur = conn.execute(
        """
        SELECT session_id, athlete_id, start_time, sport, activity_tag,
               source_vendor, source_device, recording_interval, hr_source,
               quality_flags, summary, context
        FROM sessions WHERE session_id = ?
        """,
        (session_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None

    (
        session_id_,
        athlete_id,
        start_time,
        sport,
        activity_tag,
        source_vendor,
        source_device,
        recording_interval,
        hr_source,
        quality_flags,
        summary,
        context,
    ) = row

    records_cur = conn.execute(
        """
        SELECT t, lat, lon, distance, speed, heart_rate, cadence, altitude,
               power, power_model, vertical_oscillation, ground_contact_time,
               gct_balance, step_length, temperature, gps_degraded, sample_quality
        FROM records WHERE session_id = ? ORDER BY t
        """,
        (session_id,),
    )
    records = [
        {
            "t": r[0],
            "lat": r[1],
            "lon": r[2],
            "distance": r[3],
            "speed": r[4],
            "heart_rate": r[5],
            "cadence": r[6],
            "altitude": r[7],
            "power": r[8],
            "power_model": r[9],
            "vertical_oscillation": r[10],
            "ground_contact_time": r[11],
            "gct_balance": r[12],
            "step_length": r[13],
            "temperature": r[14],
            "gps_degraded": bool(r[15]) if r[15] is not None else None,
            "sample_quality": json.loads(r[16]) if r[16] is not None else [],
        }
        for r in records_cur.fetchall()
    ]

    rr_cur = conn.execute(
        """
        SELECT seq, rr_ms, rr_source, is_artefact
        FROM rr_intervals WHERE session_id = ? ORDER BY seq
        """,
        (session_id,),
    )
    rr_intervals = [
        {"seq": r[0], "rr_ms": r[1], "rr_source": r[2], "is_artefact": bool(r[3])}
        for r in rr_cur.fetchall()
    ]

    return {
        "session_id": session_id_,
        "athlete_id": athlete_id,
        "start_time": start_time,
        "sport": sport,
        "activity_tag": activity_tag,
        "source_vendor": source_vendor,
        "source_device": source_device,
        "recording_interval": recording_interval,
        "hr_source": hr_source,
        "quality_flags": json.loads(quality_flags) if quality_flags is not None else [],
        "summary": json.loads(summary) if summary is not None else None,
        "context": json.loads(context) if context is not None else None,
        "records": records,
        "rr_intervals": rr_intervals,
    }
