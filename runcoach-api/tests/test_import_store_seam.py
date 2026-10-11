"""F020: the store seam every archive-import file goes through.

``pipeline.ingest_fit_bytes`` and ``db.persist`` gain keyword-only
parameters that all default to the one-file upload's behaviour:

- ``after_write(conn)`` runs inside ``persist``'s transaction after the load
  is saved, so a fault in it rolls the whole session back;
- ``keep(session)`` runs after classification and before any connection is
  opened, so a file it refuses touches no store;
- ``data_dir``, ``profile_names``, ``zone`` and ``timeout`` let a caller that
  captured its settings once pass them in, instead of the pipeline reading
  the cached config per file.

The schema gains ``sessions.import_id`` and the ``imports`` and
``import_files`` tables (F020 reference section 7). The report table's
session column is ``stored_session_id``, so ``_child_tables`` never lists it
and deleting a session leaves its report row.

Fixtures cited: ``strap_run_hrv.fit`` (a run with RR beats, run proof per the
fixtures README) as the session whose rows the rollback must remove, and
``strap_hrv_capture.fit`` (an HRV capture on the "HRV Snapshot" profile) as the
file whose Tier-1 route depends on the profile names handed in.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.ingestion import pipeline
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
RUN_WITH_BEATS = "strap_run_hrv.fit"
CAPTURE = "strap_hrv_capture.fit"
CAPTURE_PROFILE = "HRV Snapshot"

# The tables a stored session writes to, each of which the rollback must undo.
SESSION_WRITE_TABLES = (
    "sessions",
    "records",
    "rr_intervals",
    "quarantine_sidecar",
    "anchor_versions",
    "session_loads",
)


class _AfterWriteFault(RuntimeError):
    pass


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _table_counts(data_dir: Path) -> dict[str, int]:
    """Row count of every table in the store, enumerated from ``sqlite_master``."""
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
    finally:
        conn.close()


def _initialised_store(data_dir: Path) -> None:
    """Create the schema and run the startup writes, so the counts taken
    afterwards move only with what the ingest itself writes."""
    conn = db.get_connection(data_dir)
    try:
        db.init_schema(conn)
    finally:
        conn.close()


def test_failing_after_write_rolls_back_the_session(isolated_data_dir: Path) -> None:
    """A raising ``after_write`` leaves every table as it was, and the same
    file ingested without the fault writes to each of the six tables, so the
    unchanged counts are a rollback and not a file that writes nothing."""
    _initialised_store(isolated_data_dir)
    before = _table_counts(isolated_data_dir)
    seen: dict[str, int] = {}

    def failing_after_write(conn: sqlite3.Connection) -> None:
        # Called after the load save, inside the transaction: the rows are visible here.
        seen["sessions"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        seen["session_loads"] = conn.execute("SELECT COUNT(*) FROM session_loads").fetchone()[0]
        raise _AfterWriteFault("import_files write failed")

    with pytest.raises(_AfterWriteFault):
        pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS), after_write=failing_after_write)

    assert seen == {"sessions": 1, "session_loads": 1}, seen
    assert _table_counts(isolated_data_dir) == before

    result = pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS))
    assert not result.skipped
    after = _table_counts(isolated_data_dir)
    grew = {table for table in SESSION_WRITE_TABLES if after[table] > before[table]}
    assert grew == set(SESSION_WRITE_TABLES), f"the fixture did not write to {set(SESSION_WRITE_TABLES) - grew}"


def test_after_write_commits_with_the_session(isolated_data_dir: Path) -> None:
    """A write made by ``after_write`` lands in the same commit as the session."""

    def tag_session(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE sessions SET import_id = 'imp-seam'")

    result = pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS), after_write=tag_session)

    conn = db.get_connection()
    try:
        row = conn.execute("SELECT import_id FROM sessions WHERE session_id = ?", (result.session_id,)).fetchone()
    finally:
        conn.close()
    assert row["import_id"] == "imp-seam"


def test_a_skipped_file_writes_no_row_in_any_table(isolated_data_dir: Path) -> None:
    """``keep`` returning False returns a skipped result and leaves the row
    count of every table unchanged; ``keep`` saw the classified session."""
    _initialised_store(isolated_data_dir)
    before = _table_counts(isolated_data_dir)
    kept: list = []

    def refuse(session) -> bool:
        kept.append(session)
        return False

    result = pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS), keep=refuse)

    assert result.skipped is True
    assert len(kept) == 1 and kept[0].sport == "running"
    assert result.session_id == kept[0].session_id
    assert _table_counts(isolated_data_dir) == before


def test_a_skipped_file_opens_no_connection(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal comes before ``get_connection``: no store is created at all."""

    def no_connection(*args, **kwargs):
        raise AssertionError("a skipped file opened a connection")

    monkeypatch.setattr(pipeline.db, "get_connection", no_connection)
    result = pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS), keep=lambda session: False)
    assert result.skipped is True


def test_keep_returning_true_stores_the_session(isolated_data_dir: Path) -> None:
    result = pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS), keep=lambda session: True)
    assert result.skipped is False
    assert _table_counts(isolated_data_dir)["sessions"] == 1


def test_session_detail_serves_import_id_null_for_a_one_file_upload(post_fit) -> None:
    """``GET /sessions/{id}`` carries ``import_id``, null for ``POST /sessions``."""
    with TestClient(app) as client:
        post = post_fit(client, RUN_WITH_BEATS)
        assert post.status_code == 201, post.text
        detail = client.get(f"/sessions/{post.json()['session_id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert "import_id" in body
    assert body["import_id"] is None


def test_session_detail_serves_a_stored_import_id(post_fit) -> None:
    """The key reads the column, not a constant null."""
    with TestClient(app) as client:
        post = post_fit(client, RUN_WITH_BEATS)
        assert post.status_code == 201, post.text
        session_id = post.json()["session_id"]
        conn = db.get_connection()
        try:
            with conn:
                conn.execute("UPDATE sessions SET import_id = 'imp-1' WHERE session_id = ?", (session_id,))
        finally:
            conn.close()
        detail = client.get(f"/sessions/{session_id}")
    assert detail.json()["import_id"] == "imp-1"


def test_a_captured_data_dir_is_used_not_the_cached_config(isolated_data_dir: Path, tmp_path: Path) -> None:
    """With ``data_dir`` given, the write lands there and the cached config's
    data dir gets no store."""
    captured = tmp_path / "captured"
    assert captured != isolated_data_dir
    assert db._load_config_cached().data_dir == isolated_data_dir

    result = pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS), data_dir=captured)

    assert not (isolated_data_dir / db.DB_FILENAME).exists()
    conn = sqlite3.connect(captured / db.DB_FILENAME)
    try:
        ids = [row[0] for row in conn.execute("SELECT session_id FROM sessions")]
    finally:
        conn.close()
    assert ids == [result.session_id]


def test_get_connection_with_no_argument_uses_the_config_data_dir(isolated_data_dir: Path) -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
    finally:
        conn.close()
    assert (isolated_data_dir / db.DB_FILENAME).exists()


def test_profile_names_given_replace_the_config_read(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``data_dir`` and ``profile_names`` both given the pipeline reads
    no config, and the capture routes on the names handed in (the cached
    config declares none, so the config's list would leave it untagged)."""
    assert db._load_config_cached().resting_hrv_profile_names == []

    def no_config():
        raise AssertionError("the pipeline read the cached config")

    monkeypatch.setattr(pipeline.db, "_load_config_cached", no_config)
    captured = tmp_path / "captured"

    result = pipeline.ingest_fit_bytes(_raw(CAPTURE), data_dir=captured, profile_names=[CAPTURE_PROFILE])

    conn = sqlite3.connect(captured / db.DB_FILENAME)
    try:
        tag = conn.execute("SELECT activity_tag FROM sessions WHERE session_id = ?", (result.session_id,)).fetchone()
    finally:
        conn.close()
    assert tag == ("resting_hrv_check",)


def test_timeout_reaches_get_connection(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    real = db.get_connection

    def recording(*args, **kwargs):
        seen.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline.db, "get_connection", recording)
    pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS), timeout=0.25)
    assert seen == [((), {"timeout": 0.25})]


def test_a_locked_store_fails_within_the_given_timeout(isolated_data_dir: Path) -> None:
    """``timeout`` is the connection's busy timeout: a store held by another
    writer fails the ingest with "database is locked" and writes nothing."""
    _initialised_store(isolated_data_dir)
    holder = sqlite3.connect(isolated_data_dir / db.DB_FILENAME)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS), timeout=0.1)
    finally:
        holder.rollback()
        holder.close()
    assert _table_counts(isolated_data_dir)["sessions"] == 0


def test_zone_is_forwarded_to_the_load_save_only_when_given(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``persist`` hands ``zone`` to ``_save_session_load`` as a keyword when
    given, and calls it with today's signature when not."""
    calls: list[dict] = []
    real = db._save_session_load

    def recording(conn, session_id, **kwargs):
        calls.append(kwargs)
        return real(conn, session_id, **kwargs)

    monkeypatch.setattr(db, "_save_session_load", recording)
    zone = ZoneInfo("Europe/London")

    pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS))
    pipeline.ingest_fit_bytes(_raw(CAPTURE), zone=zone)

    assert set(calls[0]) == {"resolved"}
    assert set(calls[1]) == {"resolved", "zone"}
    assert calls[1]["zone"] is zone


def test_the_import_tables_have_the_reference_columns() -> None:
    """F020 reference section 7: one job row, one report row per file, and
    the nullable session column."""
    schema = db._expected_schema()
    assert schema["sessions"]["import_id"] == "TEXT"
    assert set(schema["imports"]) == {
        "import_id",
        "archive_name",
        "archive_bytes",
        "status",
        "error",
        "created_at",
        "started_at",
        "finished_at",
        "fit_files_scanned",
        "activities_total",
        "activities_processed",
        "counts",
    }
    assert set(schema["import_files"]) == {
        "import_id",
        "seq",
        "entry",
        "time_created",
        "outcome",
        "stored_session_id",
        "sport",
        "activity_tag",
        "quality_flags",
        "status",
        "detail",
    }


def test_import_files_is_not_a_child_table(isolated_data_dir: Path) -> None:
    """The report row names its session as ``stored_session_id`` with no
    foreign key, so deleting the session leaves the row."""
    assert "import_files" in db._expected_schema()
    assert "import_files" not in db._child_tables()
    assert "imports" not in db._child_tables()

    result = pipeline.ingest_fit_bytes(_raw(RUN_WITH_BEATS))
    conn = db.get_connection()
    try:
        fks = list(conn.execute("PRAGMA foreign_key_list(import_files)"))
        assert fks == []
        with conn:
            conn.execute(
                "INSERT INTO import_files (import_id, seq, entry, outcome, stored_session_id)"
                " VALUES ('imp-1', 1, 'a.fit', 'stored', ?)",
                (result.session_id,),
            )
        assert db.delete_session(conn, result.session_id) is True
        rows = [tuple(row) for row in conn.execute("SELECT import_id, seq, stored_session_id FROM import_files")]
    finally:
        conn.close()
    assert rows == [("imp-1", 1, result.session_id)]
