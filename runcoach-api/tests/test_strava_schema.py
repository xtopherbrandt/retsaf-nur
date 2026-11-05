"""The store and the import report can hold Strava rows, outcomes and links (F030 reference sections 3-4).

The storage half: ``strava_activities`` (one row per ``Activity ID``, every cell kept as JSON text,
the link ``linked_session_id`` unique where not null), the import columns ``imports.source``,
``imports.load_changes`` and ``import_files.strava_activity_id``, landed on a store created before
them by ``_reconcile_columns``, and the ``sessions (start_time)`` index ``sessions_at_second`` reads.
The report half: ``ImportOutcome`` holds every Strava outcome, ``ImportCounts`` gains ``strava``
(every key 0 for a job stored before it), the ``files[]`` item gains ``strava_activity_id`` and
``Import`` gains ``source`` (a stored null is served ``garmin``) and ``load_changes``.

Every store is the autouse ``isolated_data_dir`` or a database under ``tmp_path``; no test reads the
athlete's store.
"""

from __future__ import annotations

import json
import sqlite3
import typing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from runcoach_api import db
from runcoach_api.imports import strava_types
from runcoach_api.main import app
from runcoach_api.schemas import Import, ImportOutcome

STRAVA_COLUMNS = {
    "activity_id",
    "start_time",
    "activity_type",
    "name",
    "description",
    "private_note",
    "filename",
    "columns",
    "linked_session_id",
    "match",
    "outcome",
    "warnings",
    "deleted_in_strava",
    "import_id",
    "updated_at",
}

# The import tables as F020 created them, before the Strava columns existed.
PRE_STRAVA_IMPORTS = """
    CREATE TABLE imports (
      import_id TEXT PRIMARY KEY, archive_name TEXT, archive_bytes INTEGER,
      status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
      started_at TEXT, finished_at TEXT, fit_files_scanned INTEGER,
      activities_total INTEGER, activities_processed INTEGER, counts TEXT
    );
    CREATE TABLE import_files (
      import_id TEXT NOT NULL, seq INTEGER NOT NULL, entry TEXT,
      time_created TEXT, outcome TEXT, stored_session_id TEXT, sport TEXT,
      activity_tag TEXT, quality_flags TEXT, status INTEGER, detail TEXT,
      PRIMARY KEY (import_id, seq)
    );
"""

# A Garmin job's counts as F020 stored them: no ``strava`` key.
GARMIN_COUNTS = {
    "stored": 2,
    "replaced": 0,
    "already_stored": 1,
    "no_night": 0,
    "failed": 0,
    "skipped_sport": {"cycling": 1},
    "non_activity": {},
    "not_fit": {"gpx": 1},
    "unreadable_archives": [],
}

STRAVA_COUNT_KEYS = set(strava_types.STRAVA_OUTCOMES) | {
    "distance_differs",
    "excluded_from_load",
    "restored_to_load",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(conn: sqlite3.Connection) -> dict[str, str]:
    return {row[0]: row[1] for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}


def _pre_strava_store(path: Path) -> sqlite3.Connection:
    """A store as it stood before the Strava schema: no ``strava_activities``, the F020 import
    tables, no ``sessions (start_time)`` index, and one finished Garmin job with one report row."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA_DDL)
    conn.executescript(
        "DROP TABLE strava_activities; DROP TABLE imports; DROP TABLE import_files;"
        "DROP INDEX IF EXISTS sessions_start_time;" + PRE_STRAVA_IMPORTS
    )
    conn.execute(
        "INSERT INTO imports VALUES ('old-job', 'export.zip', 10, 'completed', NULL,"
        " '2026-10-11T00:00:00+00:00', '2026-10-11T00:00:01+00:00', '2026-10-11T00:00:02+00:00', 3, 3, 3, ?)",
        (json.dumps(GARMIN_COUNTS),),
    )
    conn.execute(
        "INSERT INTO import_files VALUES ('old-job', 1, 'a.fit', '2026-10-10T00:00:00+00:00', 'stored', 's1',"
        " 'running', NULL, '[]', NULL, NULL)"
    )
    conn.commit()
    assert "strava_activities" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "source" not in _columns(conn, "imports")
    return conn


def _strava_row(activity_id: int, **overrides) -> dict:
    row = {
        "activity_id": activity_id,
        "start_time": "2022-04-18T14:26:14+00:00",
        "activity_type": "Run",
        "name": "Morning Run",
        "description": "Bailed due to calf problems",
        "private_note": "left calf, again",
        "filename": "activities/7012345678.fit.gz",
        "columns": {"Activity ID": str(activity_id), "Distance": "13.71", "Distance (2)": "13711.6"},
        "linked_session_id": None,
        "match": None,
        "outcome": "not_running",
        "warnings": [],
        "deleted_in_strava": False,
        "import_id": "job-1",
        "updated_at": "2026-11-04T10:00:00+00:00",
    }
    row.update(overrides)
    return row


def _insert_session(conn: sqlite3.Connection, session_id: str, start_time: str, sport: str, distance_m, device: str) -> None:
    summary = json.dumps({"distance_m": distance_m}) if distance_m is not None else json.dumps({})
    conn.execute(
        "INSERT INTO sessions (session_id, start_time, sport, source_vendor, source_device, summary)"
        " VALUES (?, ?, ?, 'garmin', ?, ?)",
        (session_id, start_time, sport, device, summary),
    )


def test_strava_tables_are_reconciled_onto_a_pre_strava_store(tmp_path: Path) -> None:
    conn = _pre_strava_store(tmp_path / "store.db")
    try:
        db.init_schema(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "strava_activities" in tables
        assert _columns(conn, "strava_activities") == STRAVA_COLUMNS
        assert {"source", "load_changes"} <= _columns(conn, "imports")
        assert "strava_activity_id" in _columns(conn, "import_files")
        indexes = _indexes(conn)
        print("  indexes:", sorted(indexes))
        assert "strava_one_row_per_session" in indexes
        assert "WHERE linked_session_id IS NOT NULL" in " ".join(indexes["strava_one_row_per_session"].split())
        assert "sessions_start_time" in indexes
        # Nothing is backfilled: the old job keeps a null source and its counts as written.
        old = conn.execute("SELECT source, load_changes, counts FROM imports WHERE import_id = 'old-job'").fetchone()
        assert (old["source"], old["load_changes"]) == (None, None)
        assert json.loads(old["counts"]) == GARMIN_COUNTS
        file_row = conn.execute("SELECT strava_activity_id, stored_session_id FROM import_files").fetchone()
        assert (file_row["strava_activity_id"], file_row["stored_session_id"]) == (None, "s1")
        # A second start is a no-op: the same columns, the same indexes.
        before = (_columns(conn, "strava_activities"), _indexes(conn))
        db.init_schema(conn)
        assert (_columns(conn, "strava_activities"), _indexes(conn)) == before
    finally:
        conn.close()


def test_a_second_row_linking_one_session_fails_the_unique_index(isolated_data_dir: Path) -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        with conn:
            db.write_strava_row(conn, _strava_row(1, linked_session_id="s1", match="csv_start", outcome="attached"))
            # Rows with no link never collide.
            db.write_strava_row(conn, _strava_row(2))
            db.write_strava_row(conn, _strava_row(3))
        with pytest.raises(sqlite3.IntegrityError, match="linked_session_id"):
            with conn:
                db.write_strava_row(conn, _strava_row(4, linked_session_id="s1", match="csv_start", outcome="attached"))
        assert db.read_strava_row(conn, 4) is None
        assert [r["activity_id"] for r in db.read_strava_rows(conn)] == [1, 2, 3]
        # The link can move once the first row lets it go.
        with conn:
            db.write_strava_row(conn, _strava_row(1))
            db.write_strava_row(conn, _strava_row(4, linked_session_id="s1", match="csv_start", outcome="attached"))
        assert db.read_strava_row(conn, 4)["linked_session_id"] == "s1"
        assert db.read_strava_row(conn, 1)["linked_session_id"] is None
    finally:
        conn.close()


def test_an_old_import_row_has_no_source_and_is_served_garmin(isolated_data_dir: Path) -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        with conn:
            for import_id, source, created in (
                ("old-job", None, "2026-10-11T00:00:00+00:00"),
                ("strava-job", "strava", "2026-11-04T00:00:00+00:00"),
            ):
                conn.execute(
                    "INSERT INTO imports (import_id, archive_name, archive_bytes, status, created_at,"
                    " fit_files_scanned, activities_total, activities_processed, counts, source)"
                    " VALUES (?, 'export.zip', 10, 'completed', ?, 1, 1, 1, ?, ?)",
                    (import_id, created, json.dumps(GARMIN_COUNTS), source),
                )
                conn.execute(
                    "INSERT INTO import_files (import_id, seq, entry, outcome, stored_session_id)"
                    " VALUES (?, 1, 'a.fit', 'stored', 's1')",
                    (import_id,),
                )
            conn.execute(
                "UPDATE import_files SET strava_activity_id = 7012345678901 WHERE import_id = 'strava-job'"
            )
            conn.execute(
                "UPDATE imports SET load_changes = ? WHERE import_id = 'strava-job'",
                (json.dumps([{"session_id": "s1", "local_date": "2026-11-03", "strava_type": "Alpine Ski",
                              "old_load": 41.5, "change": "excluded"}]),),
            )
    finally:
        conn.close()
    with TestClient(app) as client:
        old = client.get("/imports/old-job")
        assert old.status_code == 200, old.text
        body = old.json()
        print("  old job:", body["source"], body["load_changes"], body["counts"]["strava"])
        assert body["source"] == "garmin"
        assert body["load_changes"] == []
        assert body["counts"]["strava"] == dict.fromkeys(STRAVA_COUNT_KEYS, 0)
        assert body["files"][0]["strava_activity_id"] is None
        # The stored value is served, not a constant: a Strava job reads strava.
        new = client.get("/imports/strava-job").json()
        assert new["source"] == "strava"
        assert new["files"][0]["strava_activity_id"] == 7012345678901
        # An entry stored before ``new_load`` existed serves it null.
        assert new["load_changes"] == [
            {"session_id": "s1", "local_date": "2026-11-03", "strava_type": "Alpine Ski", "old_load": 41.5,
             "new_load": None, "change": "excluded"}
        ]
        listed = {item["import_id"]: item["source"] for item in client.get("/imports").json()["items"]}
        assert listed == {"old-job": "garmin", "strava-job": "strava"}


def test_the_link_column_is_not_a_child_table_reference(isolated_data_dir: Path) -> None:
    schema = db._expected_schema()
    assert "linked_session_id" in schema["strava_activities"]
    assert "session_id" not in schema["strava_activities"]
    assert "strava_activities" not in db._child_tables()
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        assert not conn.execute("PRAGMA foreign_key_list(strava_activities)").fetchall()
        with conn:
            _insert_session(conn, "s1", "2022-04-18T14:26:14+00:00", "running", 13711.6, "945")
            db.write_strava_row(conn, _strava_row(1, linked_session_id="s1", match="csv_start", outcome="attached"))
        assert db.delete_session(conn, "s1") is True
        # The row outlives the session it names (section 4: no foreign key); step 0 clears the link.
        kept = db.read_strava_row(conn, 1)
        assert kept is not None and kept["linked_session_id"] == "s1"
    finally:
        conn.close()


def test_write_and_read_a_strava_row_round_trips_every_column(isolated_data_dir: Path) -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        cells = {
            "Activity ID": "7012345678901",
            "Activity Date": "Apr 18, 2022, 2:26:14 PM",
            "Activity Name": "Calf ✗ — bailed",
            "Activity Description": "line one\nline two\r\nline three",
            "Distance": "13.71",
            "Elapsed Time": "4,100",
            "Distance (2)": "13711.6",
            "Commute": "",
            "Relative Effort": "57",
        }
        written = _strava_row(
            7012345678901,
            name="Calf ✗ — bailed",
            description="line one\nline two\r\nline three",
            private_note="",
            columns=cells,
            linked_session_id="s9",
            match="file_start",
            outcome="attached_by_file_start",
            warnings=["distance_differs"],
            deleted_in_strava=True,
        )
        with conn:
            db.write_strava_row(conn, written)
        read = db.read_strava_row(conn, 7012345678901)
        print("  read:", read)
        assert read == written
        assert list(read["columns"]) == list(cells)  # header order kept
        assert isinstance(read["deleted_in_strava"], bool)
        # The JSON text is the fixed encoding AC7's byte-equality reads.
        raw = conn.execute("SELECT columns, warnings FROM strava_activities").fetchone()
        assert raw["columns"] == json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
        assert raw["warnings"] == '["distance_differs"]'
        # Writing the same id again replaces every column in place.
        replaced = _strava_row(7012345678901, description="new text", columns={"Activity ID": "7012345678901"})
        with conn:
            db.write_strava_row(conn, replaced)
        assert db.read_strava_row(conn, 7012345678901) == replaced
        assert db.read_strava_rows(conn) == [replaced]
        assert db.read_strava_row(conn, 1) is None
    finally:
        conn.close()


def test_write_strava_row_does_not_commit(isolated_data_dir: Path) -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.write_strava_row(conn, _strava_row(5))
        conn.rollback()
        assert db.read_strava_rows(conn) == []
    finally:
        conn.close()


def test_sessions_start_time_has_a_lookup_index(isolated_data_dir: Path) -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        with conn:
            # The 2024-07-05 shape: two sessions on one second, one more on the next.
            _insert_session(conn, "fr945", "2024-07-05T10:00:00+00:00", "running", 13712.0, "945")
            _insert_session(conn, "d3476", "2024-07-05T10:00:00+00:00", "running", 3090.0, "3476")
            _insert_session(conn, "ride", "2024-07-05T10:00:01+00:00", "other", None, "945")
        got = db.sessions_at_second(conn, "2024-07-05T10:00:00+00:00")
        print("  at second:", got)
        assert sorted(got) == [("d3476", "running", 3090.0), ("fr945", "running", 13712.0)]
        assert db.sessions_at_second(conn, "2024-07-05T10:00:01+00:00") == [("ride", "other", None)]
        assert db.sessions_at_second(conn, "2024-07-05T09:59:59+00:00") == []
        plan = " ".join(
            row[3]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN " + db.SESSIONS_AT_SECOND_SQL, ("2024-07-05T10:00:00+00:00",)
            )
        )
        print("  plan:", plan)
        assert "USING INDEX sessions_start_time" in plan
    finally:
        conn.close()


def test_an_import_report_validates_with_strava_outcomes_and_counts() -> None:
    outcomes = set(typing.get_args(ImportOutcome))
    assert set(strava_types.STRAVA_OUTCOMES) <= outcomes
    assert {"stored", "replaced", "already_stored", "no_night", "skipped_sport", "failed"} <= outcomes
    files = [
        {
            "seq": seq,
            "entry": f"activities/{seq}.fit.gz",
            "time_created": "2022-04-18T14:26:14+00:00",
            "outcome": outcome,
            "session_id": None,
            "sport": "Run",
            "activity_tag": None,
            "quality_flags": None,
            "status": None,
            "detail": None,
            "strava_activity_id": 7012345678900 + seq,
        }
        for seq, outcome in enumerate(strava_types.STRAVA_OUTCOMES, start=1)
    ]
    strava_counts = {key: n for n, key in enumerate(sorted(STRAVA_COUNT_KEYS), start=1)}
    body = {
        "import_id": "job-1",
        "archive_name": "export_1347737.zip",
        "archive_bytes": 515,
        "status": "completed",
        "error": None,
        "created_at": "2026-11-04T00:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "progress": {"fit_files_scanned": 0, "activities_total": 3, "activities_processed": 3},
        "counts": {**GARMIN_COUNTS, "strava": strava_counts},
        "files": files,
        "source": "strava",
        "load_changes": [
            {"session_id": "s1", "local_date": "2026-11-03", "strava_type": "Alpine Ski", "old_load": None,
             "new_load": 33.01, "change": "restored"}
        ],
    }
    report = Import.model_validate(body).model_dump()
    assert report["source"] == "strava"
    assert [f["outcome"] for f in report["files"]] == list(strava_types.STRAVA_OUTCOMES)
    assert report["files"][0]["strava_activity_id"] == 7012345678901
    assert report["counts"]["strava"] == strava_counts
    assert report["load_changes"][0]["change"] == "restored"
    assert report["load_changes"][0]["new_load"] == 33.01
    # A job stored before Strava: every strava key 0, source garmin, no load changes.
    old = {k: v for k, v in body.items() if k not in {"source", "load_changes", "files"}}
    old["counts"] = GARMIN_COUNTS
    served = Import.model_validate({**old, "source": None}).model_dump()
    assert served["source"] == "garmin"
    assert served["load_changes"] == []
    assert served["counts"]["strava"] == dict.fromkeys(STRAVA_COUNT_KEYS, 0)
    assert Import.model_validate(old).model_dump()["source"] == "garmin"
    # A partial strava block defaults the missing keys.
    partial = Import.model_validate({**old, "counts": {**GARMIN_COUNTS, "strava": {"attached": 4}}}).model_dump()
    assert partial["counts"]["strava"] == {**dict.fromkeys(STRAVA_COUNT_KEYS, 0), "attached": 4}
    # Unknown values are refused.
    for bad in ({**old, "source": "polar"}, {**body, "files": [{**files[0], "outcome": "linked"}]}):
        with pytest.raises(ValidationError):
            Import.model_validate(bad)
    with pytest.raises(ValidationError):
        Import.model_validate({**body, "load_changes": [{**body["load_changes"][0], "change": "moved"}]})


def test_the_shared_types_carry_what_the_rules_read() -> None:
    row = strava_types.CsvRow(
        line=2,
        activity_id=7012345678901,
        start_time="2022-04-18T14:26:14+00:00",
        activity_type="Run",
        name="Morning Run",
        filename="activities/1.fit.gz",
        distance_m=13711.6,
        columns={"Activity Description": "calf", "Activity Private Note": "", "Distance (2)": "13711.6"},
    )
    assert (row.description, row.private_note) == ("calf", "")
    bare = strava_types.CsvRow(
        line=3, activity_id=1, start_time="2022-04-18T14:26:14+00:00", activity_type="Ride", name="",
        filename="", distance_m=None, columns={},
    )
    assert (bare.description, bare.private_note) == (None, None)
    err = strava_types.FileError("file_not_in_archive")
    assert (err.reason, err.status, err.detail) == ("file_not_in_archive", None, "")
    assert strava_types.FileError("too_large", status=413).status == 413
    decision = strava_types.Decision(outcome=strava_types.ATTACHED, session_id="s1", match=strava_types.MATCH_CSV_START)
    assert (decision.warnings, decision.detail, decision.store) == ((), None, False)
    assert len(set(strava_types.STRAVA_OUTCOMES)) == len(strava_types.STRAVA_OUTCOMES)
    assert {strava_types.MATCH_CSV_START, strava_types.MATCH_FILE_START, strava_types.MATCH_STORED} == {
        "csv_start", "file_start", "stored"
    }
