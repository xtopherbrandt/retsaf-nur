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
import functools
import json
import sqlite3
from pathlib import Path

from runcoach_api import config as config_module
from runcoach_api.ingestion.exceptions import DuplicateSessionError
from runcoach_api.models import Record, RRInterval, Session

DB_FILENAME = "runcoach.db"


def _json_dump(value):
    """``json.dumps(value)``, passing ``None`` through unchanged."""
    return None if value is None else json.dumps(value)


def _json_load(value, default=None):
    """``json.loads(value)``, returning ``default`` when ``value`` is ``None``."""
    return default if value is None else json.loads(value)


def _bool_to_int(value):
    """Python ``bool``/``None`` -> SQLite ``INTEGER``, preserving ``None``."""
    return None if value is None else int(bool(value))


def _int_to_bool(value):
    """SQLite ``INTEGER``/``None`` -> Python ``bool``, preserving ``None``."""
    return None if value is None else bool(value)


# lru_cache(maxsize=1) below caches the parsed config across calls within a
# process (avoiding a re-read/re-parse of the TOML file on every request).
# Tests get isolation via the autouse `isolated_data_dir` fixture
# (tests/conftest.py), which monkeypatches `config_module.load_config`
# per-test *and* clears this cache before each test -- so a stale
# lru_cache'd config from a previous test's monkeypatch is never seen.
@functools.lru_cache(maxsize=1)
def _load_config_cached() -> config_module.AppConfig:
    return config_module.load_config()


def get_connection() -> sqlite3.Connection:
    """Open (creating if needed) the SQLite database under AppConfig.data_dir."""
    app_config = _load_config_cached()
    data_dir: Path = app_config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# The single source of truth for the canonical schema. Both table
# creation and the column reconciliation below are derived from this
# one string, so they cannot drift apart.
_SCHEMA_DDL = """
    CREATE TABLE IF NOT EXISTS sessions (
      session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
      sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
      source_device TEXT, recording_interval TEXT, hr_source TEXT,
      rr_valid_fraction REAL, quality_flags TEXT, summary TEXT, context TEXT,
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
      rr_ms REAL NOT NULL, rr_source TEXT, rr_carrier TEXT, is_artefact INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS quarantine_sidecar (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), field_name TEXT NOT NULL,
      value TEXT
    );
"""


def _expected_schema() -> dict[str, dict[str, str]]:
    """``{table: {column: decl_type}}`` as ``_SCHEMA_DDL`` defines it,
    read back from a throwaway in-memory database.

    Deriving this from the DDL itself (rather than hand-listing the
    columns added over time) is the point: a hand-maintained list only
    ever contains the columns whoever edited it remembered, and the
    first version of this function shipped missing ``sessions.context``
    for exactly that reason.
    """
    probe = sqlite3.connect(":memory:")
    try:
        probe.executescript(_SCHEMA_DDL)
        tables = [
            row[0]
            for row in probe.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        return {
            table: {row[1]: row[2] for row in probe.execute(f"PRAGMA table_info({table})")}
            for table in tables
        }
    finally:
        probe.close()


def _reconcile_columns(conn: sqlite3.Connection) -> None:
    """Additively add any column present in ``_SCHEMA_DDL`` but missing
    from an already-created table.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op against an existing
    table, so a database created before a column was added keeps the
    old layout and every INSERT naming that column fails with "table X
    has no column named Y" -- a 500 on a perfectly valid upload. The
    test suite structurally cannot catch that, because conftest.py
    builds a fresh database per test where the CREATE path always
    includes every column.

    This is not the migration framework F003 deferred to F004: it adds
    nullable columns only. A rename or retype still needs a real
    migration, and will surface here as an error rather than being
    silently papered over.
    """
    for table, expected in _expected_schema().items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table was just created with every column
        for column, decl_type in expected.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl_type}")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the canonical-schema tables if they don't already exist,
    then additively reconcile any column added after a database was
    first created (see ``_reconcile_columns``)."""
    conn.executescript(_SCHEMA_DDL)
    _reconcile_columns(conn)
    conn.commit()


def _insert_session(conn: sqlite3.Connection, session: Session) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, athlete_id, start_time, sport, activity_tag,
            source_vendor, source_device, recording_interval, hr_source,
            rr_valid_fraction, quality_flags, summary, context
        ) VALUES (
            :session_id, :athlete_id, :start_time, :sport, :activity_tag,
            :source_vendor, :source_device, :recording_interval, :hr_source,
            :rr_valid_fraction, :quality_flags, :summary, :context
        )
        """,
        {
            "session_id": session.session_id,
            "athlete_id": session.athlete_id,
            "start_time": session.start_time,
            "sport": session.sport,
            "activity_tag": session.activity_tag,
            "source_vendor": session.source_vendor,
            "source_device": session.source_device,
            "recording_interval": session.recording_interval,
            "hr_source": session.hr_source,
            "rr_valid_fraction": session.rr_valid_fraction,
            "quality_flags": _json_dump(session.quality_flags),
            "summary": _json_dump(session.summary),
            "context": _json_dump(dataclasses.asdict(session.context))
            if session.context
            else None,
        },
    )


def _insert_records(conn: sqlite3.Connection, session_id: str, records: list[Record]) -> None:
    conn.executemany(
        """
        INSERT INTO records (
            session_id, t, lat, lon, distance, speed, heart_rate, cadence,
            altitude, power, power_model, vertical_oscillation,
            ground_contact_time, gct_balance, step_length, temperature,
            gps_degraded, sample_quality
        ) VALUES (
            :session_id, :t, :lat, :lon, :distance, :speed, :heart_rate, :cadence,
            :altitude, :power, :power_model, :vertical_oscillation,
            :ground_contact_time, :gct_balance, :step_length, :temperature,
            :gps_degraded, :sample_quality
        )
        """,
        [
            {
                "session_id": session_id,
                "t": r.t,
                "lat": r.lat,
                "lon": r.lon,
                "distance": r.distance,
                "speed": r.speed,
                "heart_rate": r.heart_rate,
                "cadence": r.cadence,
                "altitude": r.altitude,
                "power": r.power,
                "power_model": r.power_model,
                "vertical_oscillation": r.vertical_oscillation,
                "ground_contact_time": r.ground_contact_time,
                "gct_balance": r.gct_balance,
                "step_length": r.step_length,
                "temperature": r.temperature,
                "gps_degraded": _bool_to_int(r.gps_degraded),
                "sample_quality": _json_dump(r.sample_quality),
            }
            for r in records
        ],
    )


def _insert_rr_intervals(
    conn: sqlite3.Connection, session_id: str, rr_intervals: list[RRInterval]
) -> None:
    conn.executemany(
        """
        INSERT INTO rr_intervals (
            session_id, seq, rr_ms, rr_source, rr_carrier, is_artefact
        )
        VALUES (:session_id, :seq, :rr_ms, :rr_source, :rr_carrier, :is_artefact)
        """,
        [
            {
                "session_id": session_id,
                "seq": rr.seq,
                "rr_ms": rr.rr_ms,
                "rr_source": rr.rr_source,
                "rr_carrier": rr.rr_carrier,
                "is_artefact": _bool_to_int(rr.is_artefact),
            }
            for rr in rr_intervals
        ],
    )


def _insert_quarantine_sidecar(
    conn: sqlite3.Connection, session_id: str, quarantine_values: dict
) -> None:
    conn.executemany(
        """
        INSERT INTO quarantine_sidecar (session_id, field_name, value)
        VALUES (:session_id, :field_name, :value)
        """,
        [
            {"session_id": session_id, "field_name": field_name, "value": _json_dump(value)}
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
               rr_valid_fraction, quality_flags, summary, context
        FROM sessions WHERE session_id = ?
        """,
        (session_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None

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
            "t": r["t"],
            "lat": r["lat"],
            "lon": r["lon"],
            "distance": r["distance"],
            "speed": r["speed"],
            "heart_rate": r["heart_rate"],
            "cadence": r["cadence"],
            "altitude": r["altitude"],
            "power": r["power"],
            "power_model": r["power_model"],
            "vertical_oscillation": r["vertical_oscillation"],
            "ground_contact_time": r["ground_contact_time"],
            "gct_balance": r["gct_balance"],
            "step_length": r["step_length"],
            "temperature": r["temperature"],
            "gps_degraded": _int_to_bool(r["gps_degraded"]),
            "sample_quality": _json_load(r["sample_quality"], default=[]),
        }
        for r in records_cur.fetchall()
    ]

    rr_cur = conn.execute(
        """
        SELECT seq, rr_ms, rr_source, rr_carrier, is_artefact
        FROM rr_intervals WHERE session_id = ? ORDER BY seq
        """,
        (session_id,),
    )
    rr_intervals = [
        {
            "seq": r["seq"],
            "rr_ms": r["rr_ms"],
            "rr_source": r["rr_source"],
            "rr_carrier": r["rr_carrier"],
            "is_artefact": _int_to_bool(r["is_artefact"]),
        }
        for r in rr_cur.fetchall()
    ]

    return {
        "session_id": row["session_id"],
        "athlete_id": row["athlete_id"],
        "start_time": row["start_time"],
        "sport": row["sport"],
        "activity_tag": row["activity_tag"],
        "source_vendor": row["source_vendor"],
        "source_device": row["source_device"],
        "recording_interval": row["recording_interval"],
        "hr_source": row["hr_source"],
        "rr_valid_fraction": row["rr_valid_fraction"],
        "quality_flags": _json_load(row["quality_flags"], default=[]),
        "summary": _json_load(row["summary"]),
        "context": _json_load(row["context"]),
        "records": records,
        "rr_intervals": rr_intervals,
    }
