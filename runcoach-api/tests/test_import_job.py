"""``imports.job``: run an import job over a scanned archive (F020 reference sections 3-5).

Driven directly, below the HTTP routes: a test creates the job row with
``create_job``, places the section 9 archive (built by
``tests/support/import_archive.py`` from corpus fixtures, in ``tmp_path``) at
``archive_path``, and runs ``run_job`` synchronously, or ``start_worker`` and
``wait_idle`` for the thread. The store is the test's ``tmp_path`` one.

Fixtures cited by the provenance table's kind: ``sample_run`` and
``strap_run_hrv`` (real runs; ``sample_run`` carries max HR 188, F016 AC1),
``strap_hrv_capture`` (an HRV capture on the "HRV Snapshot" profile, tagged
``resting_hrv_check`` only when that profile is declared),
``sample_health_snapshot`` (a health snapshot, raw sport 60) and
``strap_hrv_sample_run`` (an HRV capture on the Run profile, kept as a run).

- **AC2** screening counts with no report row; **AC3** keep or skip, the
  cycling copy writing nothing in any table; **AC4** oldest first; **AC5** a
  second run reports ``already_stored``; **AC6** one bad file fails one row (a
  truncated FIT, an untyped exception, a locked store, a failing report write,
  a corrupt bzip2 or LZMA entry); **AC8** startup recovery from each active
  status, and temp-dir cleanup.
- Every test drains the worker registry with ``job.wait_idle`` before teardown
  (the autouse ``_drain_workers``), so no thread outlives its store.
"""

from __future__ import annotations

import importlib.util
import io
import shutil
import sqlite3
import struct
import threading
import time
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.imports import job
from runcoach_api.ingestion import exceptions, pipeline
from runcoach_api.main import app

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
import_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(import_archive)

IA = import_archive
UTC_ZONE = ZoneInfo("UTC")
CAPTURE_PROFILE = "HRV Snapshot"
DRAIN_TIMEOUT_S = 60.0
# The lock test lowers the job's store timeout to this; sqlite3's default is 5 s.
LOWERED_TIMEOUT_S = 0.2
# The locked attempt must finish well inside sqlite3's 5 s default: a store
# connection that ignored the lowered timeout would wait the full 5 s.
LOCKED_ATTEMPT_BOUND_S = 2.0

SAMPLE_RUN = "sample_run.fit"
STRAP_RUN = "strap_run_hrv.fit"
CAPTURE = "strap_hrv_capture.fit"
SNAPSHOT = "sample_health_snapshot.fit"
SAMPLE_CAPTURE_RUN = "strap_hrv_sample_run.fit"
STORED_NAMES = {IA.NO_HR_RUN, SNAPSHOT, SAMPLE_RUN, SAMPLE_CAPTURE_RUN, STRAP_RUN, CAPTURE}
# The tables a stored session writes rows to (the import adds nothing else).
SESSION_TABLES = ("sessions", "records", "rr_intervals", "quarantine_sidecar")


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _inner(name: str) -> str:
    return f"{IA.INNER_ZIP}!{name}"


def _entry_bytes(**kwargs) -> dict[str, bytes]:
    return dict(IA.inner_entries(**kwargs))


def _connect(data_dir: Path) -> sqlite3.Connection:
    conn = db.get_connection(data_dir)
    db.init_schema(conn)
    return conn


def _create(data_dir: Path, tmp_path: Path, archive: Path | None = None, **archive_kwargs) -> str:
    """A ``queued`` job whose archive sits at ``job.archive_path``."""
    if archive is None:
        source = tmp_path / "source"
        source.mkdir(exist_ok=True)
        archive = IA.build_archive(source, **archive_kwargs)
    conn = _connect(data_dir)
    try:
        import_id = job.create_job(conn, archive.name, archive.stat().st_size)
    finally:
        conn.close()
    target = job.archive_path(data_dir, import_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, target)
    return import_id


def _run(data_dir: Path, import_id: str, profile_names=(CAPTURE_PROFILE,)) -> dict:
    job.run_job(import_id, data_dir, list(profile_names), UTC_ZONE)
    return _report(data_dir, import_id)


def _report(data_dir: Path, import_id: str) -> dict:
    conn = db.get_connection(data_dir)
    try:
        report = job.read_import(conn, import_id)
    finally:
        conn.close()
    assert report is not None
    return report


def _rows(report: dict) -> dict[str, dict]:
    rows = {row["entry"]: row for row in report["files"]}
    assert len(rows) == len(report["files"]), "two report rows share an entry label"
    return rows


def _table_counts(data_dir: Path) -> dict[str, int]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()


def _session_import_ids(data_dir: Path) -> dict[str, str | None]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        return dict(conn.execute("SELECT session_id, import_id FROM sessions").fetchall())
    finally:
        conn.close()


def _wrap_ingest(monkeypatch: pytest.MonkeyPatch, around) -> None:
    """Route the job's ingest calls through ``around(real, raw, kwargs)``."""
    real = pipeline.ingest_fit_bytes

    def wrapped(raw, **kwargs):
        return around(real, raw, kwargs)

    monkeypatch.setattr(job.pipeline, "ingest_fit_bytes", wrapped)


# --- AC3: keep or skip ---------------------------------------------------------------------------


def test_the_cycling_copy_is_skipped_and_every_table_count_is_unchanged(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run patched to cycling is ``skipped_sport`` with sport ``cycling``,
    and the row count of every table, the anchor version log included, is the
    same before and after its ingest; the run stored just before it moved
    those counts, so the comparison can see a write."""
    entries = _entry_bytes()
    cycling = entries[IA.CYCLING_RUN]
    seen: dict[str, object] = {}

    def around(real, raw, kwargs):
        before = _table_counts(isolated_data_dir)
        result = real(raw, **kwargs)
        after = _table_counts(isolated_data_dir)
        if raw == cycling:
            seen["cycling"] = (before, after, result.skipped)
        elif raw == entries[SAMPLE_RUN]:
            seen["sample_run"] = (before, after)
        return result

    _wrap_ingest(monkeypatch, around)
    import_id = _create(isolated_data_dir, tmp_path)
    report = _run(isolated_data_dir, import_id)

    assert report["status"] == "completed", report["error"]
    before, after, skipped = seen["cycling"]
    assert skipped is True
    assert "anchor_versions" in before
    assert after == before
    run_before, run_after = seen["sample_run"]
    assert run_after["sessions"] == run_before["sessions"] + 1
    assert run_after["anchor_versions"] > run_before["anchor_versions"]

    row = _rows(report)[_inner(IA.CYCLING_RUN)]
    assert row["outcome"] == "skipped_sport"
    assert row["sport"] == "cycling"
    assert row["session_id"] is None
    assert report["counts"]["skipped_sport"] == {"cycling": 1}


def test_the_two_captures_are_kept_with_their_activity_tag(isolated_data_dir: Path, tmp_path: Path) -> None:
    """Both captures map to canonical ``other`` and are kept by the
    ``activity_tag`` arm; the capture recorded on the Run profile is a run."""
    import_id = _create(isolated_data_dir, tmp_path)
    rows = _rows(_run(isolated_data_dir, import_id))

    capture, snapshot = rows[_inner(CAPTURE)], rows[_inner(SNAPSHOT)]
    assert (capture["outcome"], capture["sport"], capture["activity_tag"]) == (
        "stored",
        "other",
        "resting_hrv_check",
    )
    assert (snapshot["outcome"], snapshot["sport"], snapshot["activity_tag"]) == (
        "stored",
        "other",
        "health_snapshot",
    )
    sample_capture = rows[_inner(SAMPLE_CAPTURE_RUN)]
    assert (sample_capture["outcome"], sample_capture["sport"]) == ("stored", "running")


def test_an_undeclared_capture_profile_skips_the_capture_with_its_sport(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """Negative class (c): with no profile declared the HRV Snapshot capture
    is not recognised, so it is skipped under its FIT sport with a null tag."""
    import_id = _create(isolated_data_dir, tmp_path)
    rows = _rows(_run(isolated_data_dir, import_id, profile_names=()))
    capture = rows[_inner(CAPTURE)]
    assert (capture["outcome"], capture["sport"], capture["activity_tag"]) == (
        "skipped_sport",
        "generic",
        None,
    )


def test_stored_rows_and_their_sessions_carry_the_import_id(isolated_data_dir: Path, tmp_path: Path) -> None:
    import_id = _create(isolated_data_dir, tmp_path)
    report = _run(isolated_data_dir, import_id)
    rows = _rows(report)

    stored = {entry: row for entry, row in rows.items() if row["outcome"] == "stored"}
    assert set(stored) == {_inner(name) for name in STORED_NAMES}
    tagged = _session_import_ids(isolated_data_dir)
    assert {row["session_id"] for row in stored.values()} == set(tagged)
    assert set(tagged.values()) == {import_id}
    assert all(isinstance(row["quality_flags"], list) for row in stored.values())
    assert report["counts"]["stored"] == len(STORED_NAMES)
    assert report["progress"]["activities_total"] == report["progress"]["activities_processed"] == len(rows)


# --- AC4: oldest first ---------------------------------------------------------------------------


def test_the_oldest_no_hr_run_borrows_no_newer_anchor(isolated_data_dir: Path, tmp_path: Path) -> None:
    """The archive lists its activities newest first; ``seq`` follows
    ``time_created`` ascending, and the no-HR run (the oldest) is stored with
    ``max_hr_bpm`` unavailable, ``anchor_unavailable`` missing."""
    entries = _entry_bytes(newest_first=True)
    listed = [name for name in entries if name.endswith(".fit")]
    assert listed.index(IA.NO_HR_RUN) > listed.index(SAMPLE_RUN), (
        "the archive must not list the no-HR run first"
    )

    import_id = _create(isolated_data_dir, tmp_path, newest_first=True)
    report = _run(isolated_data_dir, import_id)
    files = report["files"]

    rows = _rows(report)
    conn = db.get_connection(isolated_data_dir)
    try:
        no_hr = db.read_session_load_body(conn, rows[_inner(IA.NO_HR_RUN)]["session_id"])
        newer = db.read_session_load_body(conn, rows[_inner(SAMPLE_RUN)]["session_id"])
    finally:
        conn.close()
    # Not vacuous: a newer run in the archive carries a max HR to borrow.
    assert newer["inputs"]["max_hr_bpm"]["value"] == 188
    assert no_hr["inputs"]["max_hr_bpm"]["value"] is None
    assert no_hr["inputs"]["max_hr_bpm"]["anchor_unavailable"] == "missing"

    dated = [row["time_created"] for row in files if row["time_created"] is not None]
    assert dated == sorted(dated)
    assert [row["seq"] for row in files] == list(range(1, len(files) + 1))
    assert files[0]["entry"] == _inner(IA.NO_HR_RUN)


# --- AC5: running it again -----------------------------------------------------------------------


def test_a_second_run_reports_every_stored_file_as_already_stored(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    first_id = _create(isolated_data_dir, tmp_path)
    first = _run(isolated_data_dir, first_id)
    counts_after_first = _table_counts(isolated_data_dir)

    second_id = _create(isolated_data_dir, tmp_path)
    second = _run(isolated_data_dir, second_id)

    assert second_id != first_id
    assert second["status"] == "completed", second["error"]
    first_rows, second_rows = _rows(first), _rows(second)
    stored = {entry: row["session_id"] for entry, row in first_rows.items() if row["outcome"] == "stored"}
    assert len(stored) == len(STORED_NAMES)
    for entry, session_id in stored.items():
        assert (second_rows[entry]["outcome"], second_rows[entry]["session_id"]) == (
            "already_stored",
            session_id,
        )
    assert second["counts"]["already_stored"] == len(stored)
    assert second["counts"]["stored"] == 0
    after = _table_counts(isolated_data_dir)
    for table in (*SESSION_TABLES, "session_loads", "anchor_versions"):
        assert after[table] == counts_after_first[table], table
    # The sessions keep the first import's id.
    assert set(_session_import_ids(isolated_data_dir).values()) == {first_id}


# --- AC6: one bad file fails one row -------------------------------------------------------------


def test_a_truncated_fit_row_carries_the_post_sessions_status_and_detail(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    truncated = _entry_bytes()[IA.TRUNCATED]
    with TestClient(app) as client:
        response = post_fit_bytes(client, IA.TRUNCATED, truncated)
    assert response.status_code == 400

    import_id = _create(isolated_data_dir, tmp_path)
    row = _rows(_run(isolated_data_dir, import_id))[_inner(IA.TRUNCATED)]
    assert (row["outcome"], row["status"], row["detail"]) == ("failed", 400, response.json()["detail"])


def test_an_untyped_exception_fails_one_row_and_the_job_completes(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _entry_bytes()[SAMPLE_RUN]

    def around(real, raw, kwargs):
        if raw == target:
            raise KeyError("seam fault")
        return real(raw, **kwargs)

    _wrap_ingest(monkeypatch, around)
    import_id = _create(isolated_data_dir, tmp_path)
    report = _run(isolated_data_dir, import_id)
    files = report["files"]

    assert report["status"] == "completed"
    failed = _rows(report)[_inner(SAMPLE_RUN)]
    assert (failed["outcome"], failed["status"], failed["detail"]) == (
        "failed",
        None,
        "KeyError: 'seam fault'",
    )
    after = [row for row in files if row["seq"] > failed["seq"]]
    assert any(row["outcome"] == "stored" for row in after)
    assert report["counts"]["failed"] == 2  # this row and the truncated FIT


def test_a_locked_store_fails_one_row_and_the_next_file_is_stored(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second connection holds ``BEGIN EXCLUSIVE`` while one file is stored:
    that row fails with "database is locked" within the lowered timeout, the
    lock is released before the row is written, and the next file is stored."""
    monkeypatch.setattr(job, "STORE_TIMEOUT_S", LOWERED_TIMEOUT_S)
    target = _entry_bytes()[STRAP_RUN]
    seen: dict[str, object] = {}

    def around(real, raw, kwargs):
        if raw != target:
            return real(raw, **kwargs)
        seen["timeout"] = kwargs.get("timeout")
        holder = sqlite3.connect(isolated_data_dir / db.DB_FILENAME)
        job_keep = kwargs["keep"]

        def locking_keep(session):
            # ``keep`` runs after decoding and just before the store is
            # opened: the lock and the clock start here, so the time measured
            # is the locked attempt alone, not the decode.
            kept = job_keep(session)
            holder.execute("BEGIN EXCLUSIVE")
            seen["started"] = time.perf_counter()
            return kept

        try:
            return real(raw, **{**kwargs, "keep": locking_keep})
        finally:
            seen["elapsed"] = time.perf_counter() - seen["started"]
            holder.rollback()
            holder.close()

    _wrap_ingest(monkeypatch, around)
    import_id = _create(isolated_data_dir, tmp_path)
    report = _run(isolated_data_dir, import_id)

    assert seen["timeout"] == LOWERED_TIMEOUT_S
    # Bounded by the lowered timeout, far inside sqlite3's 5 s default.
    assert LOWERED_TIMEOUT_S * 0.5 <= seen["elapsed"] < LOCKED_ATTEMPT_BOUND_S, seen["elapsed"]
    assert report["status"] == "completed", report["error"]
    rows = _rows(report)
    locked = rows[_inner(STRAP_RUN)]
    assert (locked["outcome"], locked["status"], locked["detail"]) == ("failed", None, "database is locked")
    following = next(row for row in report["files"] if row["seq"] == locked["seq"] + 1)
    assert following["outcome"] == "stored"
    assert following["session_id"] in _session_import_ids(isolated_data_dir)


def test_a_failing_import_files_write_leaves_no_session(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report row is written in the session's own transaction: a fault in
    that write rolls the session back and fails the row."""
    real_write = job._write_stored_row
    seen: list[str] = []

    def failing(conn, row, *args, **kwargs):
        if row.entry == _inner(SAMPLE_RUN):
            seen.append(row.session_id)
            raise sqlite3.OperationalError("disk I/O error")
        return real_write(conn, row, *args, **kwargs)

    monkeypatch.setattr(job, "_write_stored_row", failing)
    import_id = _create(isolated_data_dir, tmp_path)
    report = _run(isolated_data_dir, import_id)

    assert len(seen) == 1
    row = _rows(report)[_inner(SAMPLE_RUN)]
    assert (row["outcome"], row["status"], row["detail"]) == (
        "failed",
        None,
        "OperationalError: disk I/O error",
    )
    assert row["session_id"] is None
    assert seen[0] not in _session_import_ids(isolated_data_dir)
    assert report["status"] == "completed"


# The exception a corrupt stream raises from each decompressor, as the row's detail names it.
_CORRUPT_STREAM_ERRORS = {zipfile.ZIP_BZIP2: "OSError", zipfile.ZIP_LZMA: "LZMAError"}


def _zip_with_corrupt_entry(entries: list[tuple[str, bytes]], corrupt: str | None, method: int) -> bytes:
    """A stored zip of ``entries`` in which ``corrupt`` (unless None) is compressed by ``method`` and its
    compressed stream is overwritten past its header, so reading it raises from the decompressor."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for name, data in entries:
            zf.writestr(name, data, compress_type=method if name == corrupt else zipfile.ZIP_STORED)
    raw = bytearray(buffer.getvalue())
    if corrupt is None:
        return bytes(raw)
    with zipfile.ZipFile(io.BytesIO(bytes(raw))) as zf:
        info = zf.getinfo(corrupt)
    name_len, extra_len = struct.unpack("<HH", raw[info.header_offset + 26 : info.header_offset + 30])
    start = info.header_offset + 30 + name_len + extra_len + 9
    assert info.compress_size > 9 + 30
    for i in range(start, start + 30):
        raw[i] ^= 0xA5
    return bytes(raw)


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"])
def test_a_corrupt_compressed_fit_entry_fails_one_row_and_the_job_completes(
    isolated_data_dir: Path, tmp_path: Path, method: int
) -> None:
    """A FIT entry of an inner zip whose bzip2 or LZMA stream is corrupt fails its own row, with
    status None; the good file beside it is stored and the job completes."""
    good = (IA.FIXTURES / "sample_run.fit").read_bytes()
    inner = _zip_with_corrupt_entry([("bad.fit", good)], "bad.fit", method)
    archive_file = tmp_path / "corrupt_fit.zip"
    archive_file.write_bytes(_zip_with_corrupt_entry([("good.fit", good), ("inner.zip", inner)], None, method))
    import_id = _create(isolated_data_dir, tmp_path, archive=archive_file)
    report = _run(isolated_data_dir, import_id)
    print(report["error"], report["files"])

    assert report["status"] == "completed", report["error"]
    rows = _rows(report)
    assert rows["good.fit"]["outcome"] == "stored"
    bad = rows["inner.zip!bad.fit"]
    assert (bad["outcome"], bad["status"]) == ("failed", None)
    assert bad["detail"].startswith(f"entry could not be read: {_CORRUPT_STREAM_ERRORS[method]}: "), bad["detail"]
    assert report["counts"]["unreadable_archives"] == []


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"])
def test_a_corrupt_compressed_inner_zip_is_unreadable_and_the_job_completes(
    isolated_data_dir: Path, tmp_path: Path, method: int
) -> None:
    """An inner zip whose own bzip2 or LZMA stream in the outer archive is corrupt is an
    ``unreadable_archives`` item; the good file beside it is stored and the job completes."""
    good = (IA.FIXTURES / "sample_run.fit").read_bytes()
    inner = _zip_with_corrupt_entry([("other.fit", good)], None, zipfile.ZIP_STORED)
    archive_file = tmp_path / "corrupt_inner.zip"
    archive_file.write_bytes(_zip_with_corrupt_entry([("good.fit", good), ("inner.zip", inner)], "inner.zip", method))
    import_id = _create(isolated_data_dir, tmp_path, archive=archive_file)
    report = _run(isolated_data_dir, import_id)
    print(report["error"], report["counts"]["unreadable_archives"])

    assert report["status"] == "completed", report["error"]
    assert [row["entry"] for row in report["files"]] == ["good.fit"]
    assert report["files"][0]["outcome"] == "stored"
    unreadable = report["counts"]["unreadable_archives"]
    assert [u["label"] for u in unreadable] == ["inner.zip"]
    assert unreadable[0]["reason"].startswith(f"could not be read: {_CORRUPT_STREAM_ERRORS[method]}: "), unreadable


# --- AC2: screening counts -----------------------------------------------------------------------


def test_non_activity_and_non_fit_entries_are_counted_with_no_files_row(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    import_id = _create(isolated_data_dir, tmp_path)
    report = _run(isolated_data_dir, import_id)
    counts = report["counts"]

    assert counts["non_activity"] == {"monitoring_b": 1, "monitoring_daily": 1}
    assert counts["not_fit"] == {"tcx": 1, "json": 1, "zip": 1}
    # Each item is exactly {"label", "reason"}: the CLI prints item["label"] (spec F020 section 4 Counts).
    unreadable = counts["unreadable_archives"]
    assert [sorted(u) for u in unreadable] == [["label", "reason"]]
    assert unreadable[0]["label"] == IA.CORRUPT_ZIP
    assert isinstance(unreadable[0]["reason"], str) and unreadable[0]["reason"]
    assert report["progress"]["fit_files_scanned"] == 10
    entries = set(_rows(report))
    for name in (IA.MONITORING_B, IA.TCX, IA.JSON, IA.NESTED_ZIP):
        assert _inner(name) not in entries
    assert IA.LOOSE_FIT not in entries
    assert not any(entry.startswith(IA.CORRUPT_ZIP) for entry in entries)
    assert len(entries) == len(STORED_NAMES) + 2  # the cycling copy and the truncated FIT


# --- AC8: lifecycle, recovery and cleanup --------------------------------------------------------


def test_a_finished_job_leaves_no_temp_dir(isolated_data_dir: Path, tmp_path: Path) -> None:
    completed = _create(isolated_data_dir, tmp_path)
    assert job.job_dir(isolated_data_dir, completed).is_dir()
    assert _run(isolated_data_dir, completed)["status"] == "completed"
    assert not job.job_dir(isolated_data_dir, completed).exists()

    not_a_zip = tmp_path / "not_a_zip.zip"
    not_a_zip.write_bytes(b"this is not a zip archive")
    failed = _create(isolated_data_dir, tmp_path, archive=not_a_zip)
    report = _run(isolated_data_dir, failed)
    assert report["status"] == "failed"
    assert report["error"].startswith("BadZipFile:")
    assert report["finished_at"] is not None
    assert not job.job_dir(isolated_data_dir, failed).exists()


class _ApiStopped(BaseException):
    """Stands in for the process ending under the daemon worker."""


@pytest.mark.parametrize("left_status", ["queued", "scanning", "running"])
def test_startup_recovery_marks_an_active_job_interrupted_and_removes_its_dir(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, left_status: str
) -> None:
    """A job left in each active status keeps its temp dir: ``queued`` (never
    run), ``scanning`` (stopped inside the scan) and ``running`` (stopped after
    two stored files). Recovery marks it ``interrupted`` with ``finished_at``,
    removes the dir, and keeps the sessions it stored (with their
    ``import_id``) and its report rows. A job already terminal is left as it was."""
    not_a_zip = tmp_path / "not_a_zip.zip"
    not_a_zip.write_bytes(b"not a zip")
    finished = _create(isolated_data_dir, tmp_path, archive=not_a_zip)
    finished_report = _run(isolated_data_dir, finished)
    assert finished_report["status"] == "failed"
    calls: list[int] = []

    def around(real, raw, kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise _ApiStopped()
        return real(raw, **kwargs)

    def stopped_scan(*args, **kwargs):
        raise _ApiStopped()

    _wrap_ingest(monkeypatch, around)
    if left_status == "scanning":
        monkeypatch.setattr(job.archive, "scan", stopped_scan)
    stopped = _create(isolated_data_dir, tmp_path)
    if left_status != "queued":
        with pytest.raises(_ApiStopped):
            job.run_job(stopped, isolated_data_dir, [CAPTURE_PROFILE], UTC_ZONE)

    left = _report(isolated_data_dir, stopped)
    assert left["status"] == left_status
    assert left["finished_at"] is None
    assert job.job_dir(isolated_data_dir, stopped).is_dir()
    expected_outcomes = ["stored", "stored"] if left_status == "running" else []
    assert [row["outcome"] for row in left["files"]] == expected_outcomes

    conn = db.get_connection(isolated_data_dir)
    try:
        interrupted = job.recover_at_startup(conn, isolated_data_dir)
    finally:
        conn.close()

    assert interrupted == [stopped]
    after = _report(isolated_data_dir, stopped)
    assert after["status"] == "interrupted"
    assert after["finished_at"] is not None
    assert not job.job_dir(isolated_data_dir, stopped).exists()
    assert after["files"] == left["files"]
    assert _session_import_ids(isolated_data_dir) == {row["session_id"]: stopped for row in left["files"]}
    assert _report(isolated_data_dir, finished) == finished_report


def test_an_active_job_refuses_a_second_create(isolated_data_dir: Path) -> None:
    conn = _connect(isolated_data_dir)
    try:
        first = job.create_job(conn, "a.zip", 10)
        with pytest.raises(job.ImportActive) as refused:
            job.create_job(conn, "b.zip", 20)
        assert refused.value.import_id == first
        assert first in str(refused.value)
        rows = conn.execute("SELECT import_id, status FROM imports").fetchall()
    finally:
        conn.close()
    assert [tuple(row) for row in rows] == [(first, "queued")]


def test_discard_job_removes_the_row_and_the_dir(isolated_data_dir: Path, tmp_path: Path) -> None:
    import_id = _create(isolated_data_dir, tmp_path)
    conn = db.get_connection(isolated_data_dir)
    try:
        job.discard_job(conn, isolated_data_dir, import_id)
        assert job.read_import(conn, import_id) is None
        assert job.list_imports(conn) == []
    finally:
        conn.close()
    assert not job.job_dir(isolated_data_dir, import_id).exists()


def test_discard_job_removes_the_dir_when_the_row_delete_fails(isolated_data_dir: Path, tmp_path: Path) -> None:
    """A store that refuses the DELETEs (a full disk) still gets the partial archive removed."""
    import_id = _create(isolated_data_dir, tmp_path)

    class FullStore:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            raise sqlite3.OperationalError("database or disk is full")

    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        job.discard_job(FullStore(), isolated_data_dir, import_id)
    assert not job.job_dir(isolated_data_dir, import_id).exists()


def test_list_imports_is_newest_first_without_files(isolated_data_dir: Path, tmp_path: Path) -> None:
    first = _create(isolated_data_dir, tmp_path)
    _run(isolated_data_dir, first)
    second = _create(isolated_data_dir, tmp_path)
    _run(isolated_data_dir, second)
    conn = db.get_connection(isolated_data_dir)
    try:
        listed = job.list_imports(conn)
    finally:
        conn.close()
    assert [item["import_id"] for item in listed] == [second, first]
    assert all("files" not in item for item in listed)


# --- the worker thread ---------------------------------------------------------------------------


def test_the_worker_writes_only_to_its_captured_data_dir(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker gets its data dir, profile names and zone at start and never
    reads the config: here the config's store is ``isolated_data_dir`` and any
    config read raises, yet the job completes in the captured store."""
    captured = tmp_path / "captured"
    import_id = _create(captured, tmp_path)

    def no_config():
        raise AssertionError("the import worker read the config")

    monkeypatch.setattr(db, "_load_config_cached", no_config)
    worker = job.start_worker(import_id, captured, [CAPTURE_PROFILE], UTC_ZONE)

    assert worker.daemon
    assert job.wait_idle(DRAIN_TIMEOUT_S)
    assert not worker.is_alive()
    assert not (isolated_data_dir / db.DB_FILENAME).exists()
    report = _report(captured, import_id)
    assert report["status"] == "completed", report["error"]
    assert report["counts"]["stored"] == len(STORED_NAMES)
    assert report["counts"]["failed"] == 1


def test_wait_idle_returns_false_while_a_worker_is_busy(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()

    def around(real, raw, kwargs):
        gate.wait(DRAIN_TIMEOUT_S)
        return real(raw, **kwargs)

    _wrap_ingest(monkeypatch, around)
    import_id = _create(isolated_data_dir, tmp_path)
    try:
        job.start_worker(import_id, isolated_data_dir, [CAPTURE_PROFILE], UTC_ZONE)
        started = time.perf_counter()
        assert job.wait_idle(0.2) is False
        assert time.perf_counter() - started < 2.0
    finally:
        gate.set()
    assert job.wait_idle(DRAIN_TIMEOUT_S) is True
    assert _report(isolated_data_dir, import_id)["status"] == "completed"


# --- the exception mapping -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["not_fit", "truncated", "no_session", "duplicate"],
)
def test_http_status_detail_gives_what_post_sessions_returns(case: str, post_fit_bytes) -> None:
    entries = _entry_bytes()
    raw = {
        "not_fit": b"plainly not a FIT file",
        "truncated": entries[IA.TRUNCATED],
        "no_session": IA.minimal_fit(IA.FILE_TYPE_MONITORING_B),
        "duplicate": entries[SAMPLE_RUN],
    }[case]
    with TestClient(app) as client:
        if case == "duplicate":
            assert post_fit_bytes(client, "first.fit", raw).status_code == 201
        response = post_fit_bytes(client, "upload.fit", raw)
    with pytest.raises(Exception) as raised:
        pipeline.ingest_fit_bytes(raw)
    assert exceptions.http_status_detail(raised.value) == (response.status_code, response.json()["detail"])


def test_http_status_detail_for_a_decode_ceiling_matches_the_413_body() -> None:
    exc = exceptions.TooManyRecordsError(11, 10, "RR beats")
    assert exceptions.http_status_detail(exc) == (413, "upload exceeds the limit of 10 RR beats (reached 11)")


def test_http_status_detail_for_a_lock_and_an_untyped_exception() -> None:
    assert exceptions.http_status_detail(sqlite3.OperationalError("database is locked")) == (
        None,
        "database is locked",
    )
    assert exceptions.http_status_detail(sqlite3.OperationalError("no such table: x")) == (
        None,
        "OperationalError: no such table: x",
    )
    assert exceptions.http_status_detail(ValueError("bad")) == (None, "ValueError: bad")
