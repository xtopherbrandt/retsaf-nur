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

# The verbatim schema as shipped at T017 (commit 12a8b5d) -- the oldest
# database vintage that can exist in the wild. Copied from that commit
# rather than hand-written, because a hand-written "old" schema only
# reflects which columns the author remembers being new: the first
# version of this test invented one that still had `context`, and so it
# passed while a real T017-era database still 500'd on every upload.
_T017_DDL = """
    CREATE TABLE sessions (
      session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
      sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
      source_device TEXT, recording_interval TEXT, hr_source TEXT,
      quality_flags TEXT, summary TEXT,
      UNIQUE (source_device, start_time)
    );
    CREATE TABLE records (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), t REAL NOT NULL,
      lat REAL, lon REAL, distance REAL, speed REAL, heart_rate REAL, cadence REAL,
      altitude REAL, power REAL, power_model TEXT, vertical_oscillation REAL,
      ground_contact_time REAL, gct_balance REAL, step_length REAL, temperature REAL,
      sample_quality TEXT
    );
    CREATE TABLE rr_intervals (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), seq INTEGER NOT NULL,
      rr_ms REAL NOT NULL, rr_source TEXT, is_artefact INTEGER DEFAULT 0
    );
    CREATE TABLE quarantine_sidecar (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), field_name TEXT NOT NULL,
      value TEXT
    );
"""


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_init_schema_adds_every_column_missing_from_a_t017_era_database():
    conn = db.get_connection()
    try:
        conn.executescript(_T017_DDL)
        conn.commit()

        assert "context" not in _columns(conn, "sessions")
        assert "gps_degraded" not in _columns(conn, "records")

        db.init_schema(conn)

        # Every column the current DDL declares must now be present --
        # asserted against the DDL itself, not a remembered list.
        for table, expected in db._expected_schema().items():
            assert expected.keys() <= _columns(conn, table), table
    finally:
        conn.close()


def test_real_ingest_succeeds_against_a_t017_era_database(isolated_data_dir):
    """The assertion the first version of this test was missing.

    Checking PRAGMA table_info proves the columns exist; it does not
    prove an ingest works. This runs the actual pipeline end to end
    against a stale database, which is what was really broken.
    """
    from pathlib import Path

    from runcoach_api.ingestion.pipeline import ingest_fit_bytes

    conn = db.get_connection()
    try:
        conn.executescript(_T017_DDL)
        conn.commit()
    finally:
        conn.close()

    conn = db.get_connection()
    try:
        db.init_schema(conn)
    finally:
        conn.close()

    fixture = Path(__file__).parent / "fixtures" / "sample_run.fit"
    result = ingest_fit_bytes(fixture.read_bytes())

    assert result.session_id

    conn = db.get_connection()
    try:
        detail = db.get_session_detail(conn, result.session_id)
    finally:
        conn.close()
    assert detail is not None
    assert len(detail["records"]) > 0


def test_init_schema_reconciliation_is_idempotent():
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.init_schema(conn)  # must not raise "duplicate column name"

        cols = [row["name"] for row in conn.execute("PRAGMA table_info(records)")]
        assert cols.count("gps_degraded") == 1
    finally:
        conn.close()
