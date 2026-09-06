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


# ---------------------------------------------------------------------------
# T055 -- the additive ``resting_rmssd_ms`` column, and the no-backfill rule
#
# Adversarial-probe enumeration for the schema-reconciliation path, per
# .claude/rules/learnings/adversarial-input-probes-are-a-task-deliverable.md.
# ``_reconcile_columns`` reads exactly two things: the expected column set the
# DDL declares, and ``PRAGMA table_info`` for each live table. The degenerate
# forms of the live table, each driven end to end below:
#
#   | probe (live sessions table)                    | expected outcome            |
#   |------------------------------------------------|-----------------------------|
#   | missing the column, T017 vintage               | added, NULL                 |
#   | missing the column, sprint-003 vintage,        | added, and stays NULL --    |
#   |   holding a *successful* pre-amendment reading |   nothing read forward      |
#   | already has the column                         | no-op, no duplicate, no raise|
#   | carries a column the DDL never declared        | stranger untouched, add ok  |
#   | absent entirely (fresh CREATE)                 | created carrying the column |
#
# Row 2 is the one that matters. It is the only coverage of the amendment's
# single irreversible data decision, and a row of nulls cannot stand in for it:
# the scenario's Given is a reading that *succeeded* under the old predicate.
#
# These rows were enumerated from the F004 acceptance scenario and the
# ``_reconcile_columns`` docstring **before** the DDL was edited, not read back
# off the implementation afterwards -- the distinction
# [[contract-tables-need-an-independent-oracle]] asks tables to state.
# ---------------------------------------------------------------------------

# The ``sessions`` DDL exactly as sprint-003 released it: the current DDL minus
# ``resting_rmssd_ms``. Copied verbatim rather than paraphrased, for the same
# reason ``_T017_DDL`` above is -- an invented "old" schema contains only the
# columns whoever wrote it remembered were new.
_PRE_AMENDMENT_SESSIONS_DDL = """
    CREATE TABLE sessions (
      session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
      sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
      source_device TEXT, recording_interval TEXT, hr_source TEXT,
      rr_valid_fraction REAL, quality_flags TEXT, summary TEXT, context TEXT,
      rmssd_precomputed REAL, hrv_source_tier TEXT, rr_source TEXT,
      UNIQUE (source_device, start_time)
    );
"""


def test_resting_rmssd_ms_mirrors_rmssd_precomputed_nullable_real():
    """The resolved column takes the device-audit column's exact shape."""
    sessions = db._expected_schema()["sessions"]

    assert "resting_rmssd_ms" in sessions
    assert sessions["resting_rmssd_ms"] == sessions["rmssd_precomputed"] == "REAL"

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        info = {row["name"]: row for row in conn.execute("PRAGMA table_info(sessions)")}
    finally:
        conn.close()

    assert info["resting_rmssd_ms"]["notnull"] == info["rmssd_precomputed"]["notnull"] == 0
    assert info["resting_rmssd_ms"]["pk"] == 0
    assert info["resting_rmssd_ms"]["dflt_value"] is None


def test_a_pre_amendment_reading_is_not_backfilled_into_resting_rmssd_ms():
    """F004 @must "Pre-amendment readings are not carried forward".

    The Given is a **successful** pre-amendment Tier-1 reading, not a row of
    nulls: ``activity_tag = 'resting_hrv_check'``, ``hrv_source_tier =
    'chest_strap_raw'``, ``rmssd_precomputed`` NULL (Tier 1 never wrote it),
    and the system-computed value sitting in
    ``context.provenance.computed_resting_rmssd_ms`` -- the exact shape
    cycle-1 produced. Adding the column must leave it NULL. Those rows came
    from the inference predicate this amendment exists to discredit, so
    copying them into the column E003 is told to read would launder them:
    afterwards a cool-down walk and a genuine supine capture are the same
    column, same tier, indistinguishable.
    """
    import json

    context = {
        "ingested_at": "2026-09-05T06:12:00+00:00",
        "provenance": {
            "raw_sport_value": 1,
            "computed_resting_rmssd_ms": 41.52,
        },
        "env_temperature_c": None,
        "env_humidity_pct": None,
        "env_wind_ms": None,
        "env_wind_dir": None,
        "subjective": None,
    }

    conn = db.get_connection()
    try:
        conn.executescript(_PRE_AMENDMENT_SESSIONS_DDL)
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, start_time, sport, activity_tag, source_vendor,
                source_device, rr_valid_fraction, quality_flags, summary, context,
                rmssd_precomputed, hrv_source_tier, rr_source
            ) VALUES (
                'pre-amendment-1', '2026-09-05T06:10:00+00:00', 'running',
                'resting_hrv_check', 'garmin', 'FR945-LTE', 1.0, '[]',
                '{"duration_s": 150.797}', ?, NULL, 'chest_strap_raw',
                'chest_strap_ecg'
            )
            """,
            (json.dumps(context),),
        )
        conn.commit()

        assert "resting_rmssd_ms" not in _columns(conn, "sessions")

        db.init_schema(conn)

        assert "resting_rmssd_ms" in _columns(conn, "sessions")

        row = conn.execute(
            "SELECT resting_rmssd_ms FROM sessions WHERE session_id = 'pre-amendment-1'"
        ).fetchone()
        detail = db.get_session_detail(conn, "pre-amendment-1")
    finally:
        conn.close()

    # The reading is still a reading -- the row is neither deleted nor rewritten...
    assert detail is not None
    assert detail["activity_tag"] == "resting_hrv_check"
    assert detail["hrv_source_tier"] == "chest_strap_raw"
    assert detail["rmssd_precomputed"] is None
    # ...and the computed value is still sitting in provenance, unread.
    assert detail["context"]["provenance"]["computed_resting_rmssd_ms"] == 41.52

    # ...but nothing carried it forward. This is the assertion the task exists for.
    assert row["resting_rmssd_ms"] is None
    assert detail["resting_rmssd_ms"] is None


def test_reconciling_a_database_that_already_has_the_column_is_a_no_op():
    """Probe: the column is already there. A blind ``ALTER TABLE ADD COLUMN``
    would raise "duplicate column name"; it must not be issued at all."""
    conn = db.get_connection()
    try:
        db.init_schema(conn)

        db.init_schema(conn)  # must not raise

        cols = [row["name"] for row in conn.execute("PRAGMA table_info(sessions)")]
    finally:
        conn.close()

    assert cols.count("resting_rmssd_ms") == 1


def test_reconciliation_leaves_an_unrelated_extra_column_alone():
    """Probe: the live table carries a column the DDL does not declare.

    ``_reconcile_columns`` is additive only -- it must neither drop the
    stranger nor let its presence stop the genuinely-missing column landing.
    """
    conn = db.get_connection()
    try:
        conn.executescript(_PRE_AMENDMENT_SESSIONS_DDL)
        conn.execute("ALTER TABLE sessions ADD COLUMN athlete_nickname TEXT")
        conn.execute(
            """
            INSERT INTO sessions (session_id, start_time, sport, source_vendor,
                                  source_device, athlete_nickname)
            VALUES ('stranger-1', '2026-01-02T00:00:00+00:00', 'running', 'garmin',
                    'dev-stranger', 'the athlete')
            """
        )
        conn.commit()

        db.init_schema(conn)

        cols = _columns(conn, "sessions")
        row = conn.execute(
            "SELECT athlete_nickname, resting_rmssd_ms FROM sessions"
            " WHERE session_id = 'stranger-1'"
        ).fetchone()
    finally:
        conn.close()

    assert "athlete_nickname" in cols
    assert "resting_rmssd_ms" in cols
    assert row["athlete_nickname"] == "the athlete"
    assert row["resting_rmssd_ms"] is None


def test_a_t017_era_database_also_gains_resting_rmssd_ms():
    """Probe: the oldest vintage in the wild clears every column at once."""
    conn = db.get_connection()
    try:
        conn.executescript(_T017_DDL)
        conn.commit()

        db.init_schema(conn)

        cols = _columns(conn, "sessions")
    finally:
        conn.close()

    assert "resting_rmssd_ms" in cols
