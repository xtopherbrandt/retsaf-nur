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
