"""The archive import with HRV_STATUS files (``file_id.type`` 68): F028 AC7 at the job and the route.

An archive is built in ``tmp_path`` holding the real night fixtures under
``fixtures/nights/`` (provenance rows in ``fixtures/README.md``: real nights
from the wellness FIT, run proof "no"), the real run ``sample_run`` and a
synthetic ``monitoring_b`` file. The nights are ``night_recent`` (2026-10-10),
the 2024-01-01 re-summary pair (``night_resummary_earlier`` at 06:52 local,
``night_resummary_later`` at 07:30, one series start) and
``night_midnight_no_average`` (no summary average, so no night).

The import reads files in ``time_created`` order, so the pair's later file
comes second and replaces the earlier one. Report rows, in order:
the midnight file ``no_night``, the earlier summary ``stored``, the later one
``replaced``, the run ``stored``, the recent night ``stored``. Imported again,
nothing is written: the nights and the run are ``already_stored`` and the
midnight file is ``no_night`` again.

The session ids and start times are the literals ``test_overnight_upload.py``
read from a decode of each fixture.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.imports import job
from runcoach_api.main import app

SUPPORT = Path(__file__).parent / "support"
FIXTURES = Path(__file__).parent / "fixtures"
NIGHTS = FIXTURES / "nights"


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


IA = _load_support("import_archive")
fit_patch = _load_support("fit_patch")

UTC_ZONE = ZoneInfo("UTC")
PROFILES = ["HRV Snapshot"]
DRAIN_TIMEOUT_S = 60.0
FILE_TYPE_MONITORING_B = 32
HRV_VALUE = 371

INNER = "DI_CONNECT/Wellness-Folder/Part1.zip"
RECENT = "night_a.fit"
EARLIER = "night_b_first_summary.fit"
LATER = "night_b_second_summary.fit"
MIDNIGHT = "midnight.fit"
RUN = "run.fit"
MONITORING = "monitoring.fit"

# Read from a decode of each fixture (as test_overnight_upload.py does).
RECENT_ID = "fe1b9839829c6200cc65ec6d239cac15"
EARLIER_ID = "d9271616992e11dae23c1a4fe1a47980"
LATER_ID = "c4ea4e8e303cee715d151d723910777a"
# file_id.time_created of each file, as the import reports it.
STAMPS = {
    MIDNIGHT: "2023-03-07T08:03:31+00:00",
    EARLIER: "2024-01-01T14:52:53+00:00",
    LATER: "2024-01-01T15:30:53+00:00",
    RECENT: "2026-10-10T11:33:29+00:00",
}
NIGHT_TAG = "overnight_hrv"


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _inner(name: str) -> str:
    return f"{INNER}!{name}"


def _night(name: str) -> bytes:
    return (NIGHTS / f"{name}.fit").read_bytes()


def _archive(directory: Path, *, recent: bytes | None = None) -> Path:
    inner = IA._zip_bytes(
        [
            (RECENT, _night("night_recent") if recent is None else recent),
            (LATER, _night("night_resummary_later")),
            (EARLIER, _night("night_resummary_earlier")),
            (MIDNIGHT, _night("night_midnight_no_average")),
            (RUN, (FIXTURES / "sample_run.fit").read_bytes()),
            (MONITORING, IA.minimal_fit(FILE_TYPE_MONITORING_B)),
        ]
    )
    path = directory / "export.zip"
    path.write_bytes(IA._zip_bytes([(INNER, inner)]))
    return path


def _connect(data_dir: Path) -> sqlite3.Connection:
    conn = db.get_connection(data_dir)
    db.init_schema(conn)
    return conn


def _import(data_dir: Path, archive: Path) -> dict:
    conn = _connect(data_dir)
    try:
        import_id = job.create_job(conn, archive.name, archive.stat().st_size)
    finally:
        conn.close()
    target = job.archive_path(data_dir, import_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, target)
    job.run_job(import_id, data_dir, PROFILES, UTC_ZONE)
    conn = db.get_connection(data_dir)
    try:
        report = job.read_import(conn, import_id)
    finally:
        conn.close()
    assert report is not None
    assert report["status"] == "completed", report["error"]
    return report


def _rows(report: dict) -> list[tuple]:
    return [(r["entry"], r["outcome"], r["session_id"], r["sport"], r["activity_tag"]) for r in report["files"]]


def _table_counts(data_dir: Path) -> dict[str, int]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables
            if t not in ("imports", "import_files")
        }
    finally:
        conn.close()


def _sessions(data_dir: Path) -> dict[str, tuple]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        return {
            row[0]: row[1:]
            for row in conn.execute("SELECT session_id, import_id, sport, activity_tag, name FROM sessions")
        }
    finally:
        conn.close()


def _run_id(report: dict) -> str:
    (row,) = [r for r in report["files"] if r["entry"] == _inner(RUN)]
    return row["session_id"]


def test_an_archive_of_nights_reports_stored_replaced_and_no_night(isolated_data_dir: Path, tmp_path: Path) -> None:
    report = _import(isolated_data_dir, _archive(tmp_path))
    rows = _rows(report)
    print("[slice compared] rows:", rows)
    print("[slice compared] counts:", report["counts"])
    run_id = _run_id(report)
    assert rows == [
        (_inner(MIDNIGHT), "no_night", None, "other", NIGHT_TAG),
        (_inner(EARLIER), "stored", EARLIER_ID, "other", NIGHT_TAG),
        (_inner(LATER), "replaced", LATER_ID, "other", NIGHT_TAG),
        (_inner(RUN), "stored", run_id, "running", None),
        (_inner(RECENT), "stored", RECENT_ID, "other", NIGHT_TAG),
    ]
    by_entry = {r["entry"]: r for r in report["files"]}
    for name, stamp in STAMPS.items():
        assert by_entry[_inner(name)]["time_created"] == stamp, name
    assert by_entry[_inner(MIDNIGHT)]["quality_flags"] is None
    for name in (EARLIER, LATER, RECENT):
        assert by_entry[_inner(name)]["quality_flags"] == [], name
    counts = report["counts"]
    assert (counts["stored"], counts["replaced"], counts["no_night"], counts["already_stored"], counts["failed"]) == (
        3,
        1,
        1,
        0,
        0,
    )
    assert counts["skipped_sport"] == {}
    # The re-summarised night stands once, under the later summary's id.
    sessions = _sessions(isolated_data_dir)
    assert set(sessions) == {LATER_ID, RECENT_ID, run_id}
    for night in (LATER_ID, RECENT_ID):
        assert sessions[night][1:] == ("other", NIGHT_TAG, "Overnight HRV"), night


def test_non_activity_no_longer_counts_hrv_status(isolated_data_dir: Path, tmp_path: Path) -> None:
    report = _import(isolated_data_dir, _archive(tmp_path))
    print("[slice compared] non_activity:", report["counts"]["non_activity"])
    assert report["counts"]["non_activity"] == {"monitoring_b": 1}
    assert report["counts"]["not_fit"] == {}
    assert report["progress"]["fit_files_scanned"] == 6


def test_nights_carry_the_import_id_and_count_in_progress(isolated_data_dir: Path, tmp_path: Path) -> None:
    report = _import(isolated_data_dir, _archive(tmp_path))
    sessions = _sessions(isolated_data_dir)
    print("[slice compared] progress:", report["progress"], "sessions:", sessions)
    assert {sid: row[0] for sid, row in sessions.items()} == {
        LATER_ID: report["import_id"],
        RECENT_ID: report["import_id"],
        _run_id(report): report["import_id"],
    }
    # Every night file, the no-night one included, is a row of the total and of the progress.
    assert report["progress"]["activities_total"] == 5
    assert report["progress"]["activities_processed"] == 5
    assert [r["seq"] for r in report["files"]] == [1, 2, 3, 4, 5]


def test_a_second_import_stores_nothing(isolated_data_dir: Path, tmp_path: Path) -> None:
    first = _import(isolated_data_dir, _archive(tmp_path))
    run_id = _run_id(first)
    tables = _table_counts(isolated_data_dir)
    second = _import(isolated_data_dir, _archive(tmp_path))
    rows = _rows(second)
    print("[slice compared] rows:", rows, "counts:", second["counts"])
    assert [(entry, outcome, sid) for entry, outcome, sid, _, _ in rows] == [
        (_inner(MIDNIGHT), "no_night", None),
        # The earlier summary of a night already stored under its later one.
        (_inner(EARLIER), "already_stored", LATER_ID),
        (_inner(LATER), "already_stored", LATER_ID),
        (_inner(RUN), "already_stored", run_id),
        (_inner(RECENT), "already_stored", RECENT_ID),
    ]
    counts = second["counts"]
    assert (counts["stored"], counts["replaced"], counts["no_night"], counts["already_stored"]) == (0, 0, 1, 4)
    assert _table_counts(isolated_data_dir) == tables
    # The sessions keep the first import's id.
    assert {row[0] for row in _sessions(isolated_data_dir).values()} == {first["import_id"]}


def test_a_faulty_night_is_a_failed_row_and_writes_nothing(isolated_data_dir: Path, tmp_path: Path) -> None:
    faulty = fit_patch.duplicate_timestamp(_night("night_recent"), HRV_VALUE, 10)
    report = _import(isolated_data_dir, _archive(tmp_path, recent=faulty))
    (row,) = [r for r in report["files"] if r["entry"] == _inner(RECENT)]
    print("[slice compared] row:", row)
    assert (row["outcome"], row["status"], row["session_id"]) == ("failed", 400, None)
    assert row["detail"] == "overnight HRV file refused: two hrv_value messages at 2026-10-10T05:01:00+00:00"
    assert report["counts"]["failed"] == 1 and report["counts"]["stored"] == 2
    assert RECENT_ID not in _sessions(isolated_data_dir)


def test_an_old_jobs_counts_serve_without_the_new_keys(isolated_data_dir: Path) -> None:
    """A job stored before ``no_night`` and ``replaced`` existed serves them as 0."""
    old_counts = {
        "stored": 2,
        "already_stored": 1,
        "failed": 0,
        "skipped_sport": {"cycling": 1},
        "non_activity": {"68": 3, "monitoring_b": 1},
        "not_fit": {},
        "unreadable_archives": [],
    }
    conn = _connect(isolated_data_dir)
    try:
        with conn:
            conn.execute(
                "INSERT INTO imports (import_id, archive_name, archive_bytes, status, created_at, started_at,"
                " finished_at, fit_files_scanned, activities_total, activities_processed, counts)"
                " VALUES ('old', 'old.zip', 10, 'completed', '2026-10-01T00:00:00+00:00',"
                " '2026-10-01T00:00:01+00:00', '2026-10-01T00:00:02+00:00', 7, 4, 4, ?)",
                (json.dumps(old_counts),),
            )
    finally:
        conn.close()
    with TestClient(app) as client:
        one = client.get("/imports/old")
        listed = client.get("/imports")
    assert one.status_code == 200, one.text
    assert listed.status_code == 200, listed.text
    print("[slice compared] served counts:", one.json()["counts"])
    expected = {**old_counts, "no_night": 0, "replaced": 0}
    assert one.json()["counts"] == expected
    (item,) = listed.json()["items"]
    assert item["counts"] == expected


def test_a_failed_report_row_rolls_the_night_back(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recent night's report-row write raises inside ``after_write``; the night rolls back.

    Held constant: the archive (the five rows of the module docstring, the recent night last),
    every other file's write, and the store. Only the recent night's ``_write_stored_row`` call
    raises, so the earlier nights and the run before it are stored as usual.
    """
    real = job._write_stored_row

    def refuse_recent(conn: sqlite3.Connection, row: job.FileRow, import_id: str, counts: dict) -> None:
        if row.session_id == RECENT_ID:
            raise RuntimeError("report row refused")
        real(conn, row, import_id, counts)

    monkeypatch.setattr(job, "_write_stored_row", refuse_recent)
    report = _import(isolated_data_dir, _archive(tmp_path))
    rows = _rows(report)
    print("[slice compared] rows:", rows, "counts:", report["counts"])
    run_id = _run_id(report)
    (recent,) = [r for r in report["files"] if r["entry"] == _inner(RECENT)]
    assert (recent["outcome"], recent["status"], recent["session_id"]) == ("failed", None, None)
    assert recent["detail"] == "RuntimeError: report row refused"
    counts = report["counts"]
    assert (counts["stored"], counts["replaced"], counts["no_night"], counts["failed"]) == (2, 1, 1, 1)
    assert report["progress"]["activities_processed"] == 5
    # The night is gone with its series and sidecar; the files before it stand.
    sessions = _sessions(isolated_data_dir)
    print("[slice compared] sessions:", sessions)
    assert set(sessions) == {LATER_ID, run_id}
    assert sessions[run_id][0] == report["import_id"]
    conn = sqlite3.connect(isolated_data_dir / db.DB_FILENAME)
    try:
        children = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (RECENT_ID,)).fetchone()[0]
            for table in ("overnight_hrv_values", "quarantine_sidecar")
        }
    finally:
        conn.close()
    print("[slice compared] recent night child rows:", children)
    assert children == {"overnight_hrv_values": 0, "quarantine_sidecar": 0}
