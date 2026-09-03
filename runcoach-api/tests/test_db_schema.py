"""Tests for runcoach_api.db: connection factory + schema DDL.

Covers T017's walking-skeleton requirements: get_connection() creates
the data dir and opens the SQLite file, and init_schema() creates all
four canonical-schema tables idempotently.
"""

from __future__ import annotations

from runcoach_api import db


def test_get_connection_creates_data_dir_and_db_file(isolated_data_dir):
    assert not isolated_data_dir.exists()

    conn = db.get_connection()
    conn.close()

    assert isolated_data_dir.exists()
    assert (isolated_data_dir / db.DB_FILENAME).exists()


def test_get_connection_enables_foreign_keys():
    conn = db.get_connection()
    try:
        cur = conn.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_init_schema_creates_all_four_tables():
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        table_names = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    assert {"sessions", "records", "rr_intervals", "quarantine_sidecar"} <= table_names


def test_init_schema_is_idempotent():
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.init_schema(conn)  # must not raise
    finally:
        conn.close()


def test_sessions_unique_constraint_on_device_and_start_time():
    import sqlite3

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        conn.execute(
            """
            INSERT INTO sessions (session_id, start_time, sport, source_vendor, source_device)
            VALUES ('s1', '2026-01-01T00:00:00Z', 'run', 'garmin', 'dev-1')
            """
        )
        conn.commit()

        with_raises = False
        try:
            conn.execute(
                """
                INSERT INTO sessions (session_id, start_time, sport, source_vendor, source_device)
                VALUES ('s2', '2026-01-01T00:00:00Z', 'run', 'garmin', 'dev-1')
                """
            )
            conn.commit()
        except sqlite3.IntegrityError:
            with_raises = True

        assert with_raises
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema reconciliation for databases created before a column was added
# ---------------------------------------------------------------------------


def test_init_schema_adds_columns_missing_from_a_preexisting_database():
    """Regression: ``CREATE TABLE IF NOT EXISTS`` is a no-op against an
    existing table, so a database created before ``gps_degraded`` (or
    ``rr_carrier`` / ``rr_valid_fraction``) existed kept the old layout
    and every INSERT naming the new column died with
    ``sqlite3.OperationalError: table records has no column named
    gps_degraded`` -- an HTTP 500 on a valid upload.

    The whole test suite missed this because conftest.py builds a fresh
    database per test, where the CREATE path always includes every
    column. This test reproduces the real-world shape instead: an
    already-created table that predates the column.
    """
    conn = db.get_connection()
    try:
        # A database as it looked before the columns were added.
        conn.executescript(
            """
            CREATE TABLE sessions (
              session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
              sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
              source_device TEXT, recording_interval TEXT, hr_source TEXT,
              quality_flags TEXT, summary TEXT, context TEXT,
              UNIQUE (source_device, start_time)
            );
            CREATE TABLE records (
              session_id TEXT NOT NULL, t REAL NOT NULL, lat REAL, lon REAL,
              distance REAL, speed REAL, heart_rate REAL, cadence REAL, altitude REAL,
              power REAL, power_model TEXT, vertical_oscillation REAL,
              ground_contact_time REAL, gct_balance REAL, step_length REAL,
              temperature REAL, sample_quality TEXT
            );
            CREATE TABLE rr_intervals (
              session_id TEXT NOT NULL, seq INTEGER NOT NULL, rr_ms REAL NOT NULL,
              rr_source TEXT, is_artefact INTEGER DEFAULT 0
            );
            CREATE TABLE quarantine_sidecar (
              session_id TEXT NOT NULL, field_name TEXT NOT NULL, value TEXT
            );
            """
        )
        conn.commit()

        def columns(table):
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

        assert "gps_degraded" not in columns("records")

        db.init_schema(conn)

        assert "gps_degraded" in columns("records")
        assert "rr_carrier" in columns("rr_intervals")
        assert "rr_valid_fraction" in columns("sessions")
    finally:
        conn.close()


def test_init_schema_reconciliation_is_idempotent():
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.init_schema(conn)  # must not raise "duplicate column name"

        cols = [row["name"] for row in conn.execute("PRAGMA table_info(records)")]
        assert cols.count("gps_degraded") == 1
    finally:
        conn.close()
