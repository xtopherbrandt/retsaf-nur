"""``POST /imports``, ``GET /imports`` and ``GET /imports/{import_id}`` (F020 AC1, AC5, AC7, AC8).

Every request goes through ``with TestClient(app)``, so the app's lifespan runs
(schema, then the startup recovery of any job left active). The store is the
autouse ``isolated_data_dir``; the section 9 archive is built at run time by
``tests/support/import_archive.py`` from corpus fixtures in ``tmp_path``.

Fixtures cited by the provenance table's kind: ``sample_run`` and
``strap_run_hrv`` (real runs), ``strap_hrv_capture`` (an HRV capture on the
"HRV Snapshot" profile, tagged ``resting_hrv_check`` when that profile is
declared), ``sample_health_snapshot`` (a health snapshot), and
``wrist_ppg_run`` (a real run, not in the archive, for the one-file upload made
during an import).

The worker is a daemon thread the route starts and does not hand back: every
test drains it with ``job.wait_idle`` before it asserts on the finished job and
again before teardown (the autouse ``_drain_workers``). A test that holds a job
in ``running`` does so with an Event-gated seam on the job's per-file ingest
and releases it in a ``finally``, so a failing assertion never hangs the suite.
"""

from __future__ import annotations

import importlib.util
import io
import sqlite3
import threading
import time
import types
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db, main
from runcoach_api.imports import job
from runcoach_api.ingestion import pipeline
from runcoach_api.main import app

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
import_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(import_archive)

IA = import_archive
FIXTURES = Path(__file__).parent / "fixtures"
CAPTURE_PROFILE = "HRV Snapshot"
DRAIN_TIMEOUT_S = 60.0
STATUS_TIMEOUT_S = 30.0
ACTIVE = {"queued", "scanning", "running"}
RESTING_TAGS = {"resting_hrv_check", "health_snapshot"}
IMPORT_KEYS = {
    "import_id",
    "archive_name",
    "archive_bytes",
    "status",
    "error",
    "created_at",
    "started_at",
    "finished_at",
    "progress",
    "counts",
}
FILE_KEYS = {
    "seq",
    "entry",
    "time_created",
    "outcome",
    "session_id",
    "sport",
    "activity_tag",
    "quality_flags",
    "status",
    "detail",
}


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


@pytest.fixture
def capture_declared(declared_config):
    """The athlete declared the capture profile, so ``strap_hrv_capture`` is a resting capture."""
    return declared_config(CAPTURE_PROFILE)


def _archive(tmp_path: Path, **kwargs) -> Path:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    return IA.build_archive(source, **kwargs)


def _plain_zip(tmp_path: Path, *fixture_names: str, name: str = "runs.zip") -> Path:
    """A zip holding corpus fixtures at its root (no inner zips)."""
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    path = source / name
    with zipfile.ZipFile(path, "w") as out:
        for fixture in fixture_names:
            out.writestr(fixture, (FIXTURES / fixture).read_bytes())
    return path


def _post_archive(client: TestClient, path: Path, upload_name: str | None = None):
    with path.open("rb") as fh:
        return client.post("/imports", files={"file": (upload_name or path.name, fh, "application/zip")})


def _post_bytes(client: TestClient, name: str, raw: bytes):
    return client.post("/imports", files={"file": (name, raw, "application/zip")})


def _drain() -> None:
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"


def _get(client: TestClient, import_id: str) -> dict:
    response = client.get(f"/imports/{import_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_status(client: TestClient, import_id: str, status: str) -> dict:
    deadline = time.monotonic() + STATUS_TIMEOUT_S
    while True:
        body = _get(client, import_id)
        if body["status"] == status:
            return body
        assert time.monotonic() < deadline, f"import never reached {status}: {body}"
        time.sleep(0.02)


def _import_rows(data_dir: Path) -> list[tuple]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        return conn.execute("SELECT import_id, status FROM imports").fetchall()
    finally:
        conn.close()


def _temp_children(data_dir: Path) -> list[str]:
    root = data_dir / "imports"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _gate_ingest(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Hold the job's per-file ingest until the returned Event is set."""
    gate = threading.Event()
    real = pipeline.ingest_fit_bytes

    def gated(raw, **kwargs):
        gate.wait(DRAIN_TIMEOUT_S)
        return real(raw, **kwargs)

    monkeypatch.setattr(job.pipeline, "ingest_fit_bytes", gated)
    return gate


def _multipart(name: str, raw: bytes, boundary: str = "runcoach-boundary") -> tuple[bytes, str]:
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + raw + tail, f"multipart/form-data; boundary={boundary}"


# --- AC1: 202 queued, then completed, each stored session carrying the import ---------------------


def test_post_imports_is_202_queued_and_reaches_completed(
    isolated_data_dir: Path, tmp_path: Path, capture_declared, post_fit_bytes
) -> None:
    archive = _archive(tmp_path)
    with TestClient(app) as client:
        response = _post_archive(client, archive)
        assert response.status_code == 202, response.text
        started = response.json()
        assert started["status"] == "queued"
        assert set(started) == IMPORT_KEYS | {"files"}
        assert started["archive_name"] == archive.name
        assert started["archive_bytes"] == archive.stat().st_size
        assert started["files"] == []
        import_id = started["import_id"]

        _drain()
        done = _get(client, import_id)
        assert done["status"] == "completed", done["error"]
        assert done["error"] is None
        assert done["started_at"] is not None and done["finished_at"] is not None

        # The shape the CLI reads: ints, maps of name -> int, and {label, reason} items.
        counts = done["counts"]
        for key in ("stored", "already_stored", "failed"):
            assert type(counts[key]) is int, (key, counts[key])
        for key in ("skipped_sport", "non_activity", "not_fit"):
            assert isinstance(counts[key], dict) and all(type(v) is int for v in counts[key].values())
        assert all("." not in ext for ext in counts["not_fit"]), counts["not_fit"]
        assert counts["not_fit"]["tcx"] == 1 and counts["not_fit"]["json"] == 1
        assert counts["skipped_sport"] == {"cycling": 1}
        assert [set(u) for u in counts["unreadable_archives"]] == [{"label", "reason"}]
        assert counts["unreadable_archives"][0]["label"] == IA.CORRUPT_ZIP

        files = done["files"]
        assert all(set(row) == FILE_KEYS for row in files)
        assert [row["seq"] for row in files] == list(range(1, len(files) + 1))
        stored = [row for row in files if row["outcome"] == "stored"]
        assert counts["stored"] == len(stored) == 6
        assert {row["activity_tag"] for row in stored if row["activity_tag"] in RESTING_TAGS} == RESTING_TAGS
        progress = done["progress"]
        assert progress["activities_processed"] == progress["activities_total"] == len(files)

        for row in stored:
            session = client.get(f"/sessions/{row['session_id']}")
            assert session.status_code == 200
            assert session.json()["import_id"] == import_id

        one_file = post_fit_bytes(client, "wrist_ppg_run.fit", (FIXTURES / "wrist_ppg_run.fit").read_bytes())
        assert one_file.status_code == 201, one_file.text
        assert client.get(f"/sessions/{one_file.json()['session_id']}").json()["import_id"] is None


def test_an_archive_named_without_zip_is_read_by_content(isolated_data_dir: Path, tmp_path: Path) -> None:
    """A directory upload arrives named after the directory: the zip is found by its bytes."""
    archive = _plain_zip(tmp_path, "sample_run.fit")
    with TestClient(app) as client:
        response = _post_archive(client, archive, upload_name="my_runs")
        assert response.status_code == 202, response.text
        _drain()
        done = _get(client, response.json()["import_id"])
    assert done["archive_name"] == "my_runs"
    assert done["status"] == "completed", done["error"]
    assert [row["outcome"] for row in done["files"]] == ["stored"]


def test_get_imports_for_an_unknown_id_is_404() -> None:
    with TestClient(app) as client:
        response = client.get("/imports/no-such-import")
    assert response.status_code == 404
    assert response.json()["detail"] == "import no-such-import not found"


# --- AC7: the request is checked before a job exists --------------------------------------------


@pytest.mark.parametrize("raw", [b"", b"plainly not a zip archive"], ids=["zero_bytes", "not_a_zip"])
def test_a_non_zip_is_400_and_leaves_no_row_or_dir(isolated_data_dir: Path, raw: bytes) -> None:
    with TestClient(app) as client:
        response = _post_bytes(client, "export.zip", raw)
        listed = client.get("/imports").json()
    assert response.status_code == 400, response.text
    assert "not a zip archive" in response.json()["detail"]
    assert listed == {"items": []}
    assert _import_rows(isolated_data_dir) == []
    assert _temp_children(isolated_data_dir) == []
    _drain()


def _zip_with_invalid_utf8_name() -> bytes:
    """A one-entry zip whose name carries the UTF-8 flag (bit 11) but bytes that are not UTF-8."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("abcd.fit", b"not read")
    raw = buffer.getvalue()
    for signature, flag_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = raw.index(signature)
        flags = int.from_bytes(raw[at + flag_at : at + flag_at + 2], "little") | 0x800
        raw = raw[: at + flag_at] + flags.to_bytes(2, "little") + raw[at + flag_at + 2 :]
    assert raw.count(b"abcd.fit") == 2
    return raw.replace(b"abcd.fit", b"\xff\xfe\xfd.fit\xc3")


def test_an_archive_whose_name_is_not_utf8_is_400_and_leaves_no_row_or_dir(isolated_data_dir: Path) -> None:
    """The outer zip's central directory does not decode (a UTF-8-flagged name that is not UTF-8):
    it does not open as a zip, so 400, like any non-zip."""
    with TestClient(app) as client:
        response = _post_bytes(client, "export.zip", _zip_with_invalid_utf8_name())
    print(response.status_code, response.text)
    assert response.status_code == 400, response.text
    assert response.json()["detail"].startswith("not a zip archive: UnicodeDecodeError: "), response.text
    assert _import_rows(isolated_data_dir) == []
    assert _temp_children(isolated_data_dir) == []
    _drain()


def test_a_worker_that_does_not_start_is_a_500_and_frees_the_slot(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker thread's ``start()`` raising (no thread can start) is a 500 naming the cause; the
    job's row and dir are removed, so the next import is not refused as active, and ``wait_idle``
    afterwards returns True: the thread that never started is not left registered for a join."""

    class UnstartableThread(threading.Thread):
        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(job, "threading", types.SimpleNamespace(Thread=UnstartableThread))
    archive_file = _plain_zip(tmp_path, "sample_run.fit")
    with TestClient(app) as client:
        refused = _post_archive(client, archive_file)
        rows_after_refusal = _import_rows(isolated_data_dir)
        children_after_refusal = _temp_children(isolated_data_dir)
        idle_after_refusal = job.wait_idle(DRAIN_TIMEOUT_S)
        monkeypatch.setattr(job, "threading", threading)
        accepted = _post_archive(client, archive_file)
        _drain()
    print(refused.status_code, refused.text, idle_after_refusal, accepted.status_code)
    assert refused.status_code == 500, refused.text
    assert refused.json()["detail"] == "the import could not be started: RuntimeError: can't start new thread"
    assert rows_after_refusal == []
    assert children_after_refusal == []
    assert idle_after_refusal is True
    assert accepted.status_code == 202, accepted.text


def test_a_request_with_no_file_part_is_422(isolated_data_dir: Path) -> None:
    with TestClient(app) as client:
        no_part = client.post("/imports", data={"archive": "export.zip"})
        empty = client.post("/imports")
    assert no_part.status_code == 422, no_part.text
    assert empty.status_code == 422, empty.text
    assert any(err["loc"] == ["body", "file"] for err in no_part.json()["detail"]), no_part.json()
    assert _import_rows(isolated_data_dir) == []
    assert _temp_children(isolated_data_dir) == []


def test_a_body_streamed_past_the_cap_is_413_and_its_bytes_are_deleted(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No declared length (chunked), so the middleware cannot refuse it; the route's own count does."""
    raw = _plain_zip(tmp_path, "sample_run.fit").read_bytes()
    cap = len(raw) - 1
    monkeypatch.setattr(main, "IMPORT_MAX_BYTES", cap)
    monkeypatch.setattr(main, "IMPORT_COPY_CHUNK_BYTES", 4096)
    body, content_type = _multipart("export.zip", raw)
    written: list[int] = []
    real_open = Path.open

    def counting_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if "w" in mode and self.name == job.ARCHIVE_FILENAME:
            real_write = handle.write

            def write(data):
                written.append(len(data))
                return real_write(data)

            handle.write = write
        return handle

    monkeypatch.setattr(Path, "open", counting_open)

    def chunks():
        for start in range(0, len(body), 1000):
            yield body[start : start + 1000]

    with TestClient(app) as client:
        response = client.post("/imports", content=chunks(), headers={"content-type": content_type})
    assert response.status_code == 413, response.text
    assert response.json()["detail"] == f"upload exceeds {cap} byte limit"
    assert written, "the copy never started: the cap was not reached while copying"
    assert sum(written) <= cap, (sum(written), cap)
    assert _import_rows(isolated_data_dir) == []
    assert _temp_children(isolated_data_dir) == []
    _drain()


def test_an_archive_exactly_at_the_cap_is_accepted(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _plain_zip(tmp_path, "sample_run.fit")
    monkeypatch.setattr(main, "IMPORT_MAX_BYTES", archive.stat().st_size)
    with TestClient(app) as client:
        response = _post_archive(client, archive)
        assert response.status_code == 202, response.text
        _drain()
        assert _get(client, response.json()["import_id"])["status"] == "completed"


def test_an_unwritable_temp_dir_is_a_500_naming_the_cause(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive's directory cannot be made (a file sits where it must go): 500, row and dir removed."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a file, not a directory")
    real_job_dir = job.job_dir

    def blocked_archive_path(data_dir, import_id):
        real_job_dir(data_dir, import_id).mkdir(parents=True, exist_ok=True)
        return blocker / job.ARCHIVE_FILENAME

    monkeypatch.setattr(job, "archive_path", blocked_archive_path)
    with TestClient(app) as client:
        response = _post_archive(client, _plain_zip(tmp_path, "sample_run.fit"))
    assert response.status_code == 500, response.text
    detail = response.json()["detail"]
    assert detail.startswith("the archive could not be stored: "), detail
    assert "Error" in detail, detail
    assert _import_rows(isolated_data_dir) == []
    assert _temp_children(isolated_data_dir) == []
    _drain()


def test_a_stored_copy_that_cannot_be_read_back_is_a_500_not_a_400(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the server's own copy fails with an I/O error (zipfile turns malformed input into
    ``BadZipFile``, so an ``OSError`` here is a server-side fault): 500 naming the cause, not 400
    "not a zip archive"; row and dir removed."""

    def unreadable(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(main, "zipfile", types.SimpleNamespace(ZipFile=unreadable, BadZipFile=zipfile.BadZipFile))
    with TestClient(app) as client:
        response = _post_archive(client, _plain_zip(tmp_path, "sample_run.fit"))
    print(response.status_code, response.text)
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == (
        "the archive could not be stored: PermissionError: [Errno 13] Permission denied"
    ), response.text
    assert _import_rows(isolated_data_dir) == []
    assert _temp_children(isolated_data_dir) == []
    _drain()


def test_a_second_post_while_active_is_409_naming_the_import(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _gate_ingest(monkeypatch)
    archive = _plain_zip(tmp_path, "sample_run.fit")
    try:
        with TestClient(app) as client:
            first = _post_archive(client, archive)
            assert first.status_code == 202, first.text
            first_id = first.json()["import_id"]
            for wanted in ("queued_or_later", "running"):
                second = _post_archive(client, archive)
                assert second.status_code == 409, (wanted, second.text)
                assert first_id in second.json()["detail"], second.json()
                if wanted == "queued_or_later":
                    _wait_for_status(client, first_id, "running")
            assert [row[0] for row in _import_rows(isolated_data_dir)] == [first_id]
            assert _temp_children(isolated_data_dir) == [first_id]
    finally:
        gate.set()
    _drain()


@pytest.mark.parametrize("raw", [b"", b"plainly not a zip archive"], ids=["zero_bytes", "not_a_zip"])
def test_a_non_zip_while_active_is_409_not_400(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    """Reference section 2.1's order: the active-job check comes before the copy and its zip check,
    so a body that would be a 400 on its own is a 409 naming the active import while one runs, and
    it leaves no row and no temp dir of its own."""
    gate = _gate_ingest(monkeypatch)
    archive = _plain_zip(tmp_path, "sample_run.fit")
    try:
        with TestClient(app) as client:
            first = _post_archive(client, archive)
            assert first.status_code == 202, first.text
            first_id = first.json()["import_id"]
            _wait_for_status(client, first_id, "running")
            second = _post_bytes(client, "export.zip", raw)
            print(second.status_code, second.text)
            assert second.status_code == 409, second.text
            assert first_id in second.json()["detail"], second.json()
            assert [row[0] for row in _import_rows(isolated_data_dir)] == [first_id]
            assert _temp_children(isolated_data_dir) == [first_id]
    finally:
        gate.set()
    _drain()


def test_the_trailing_slash_path_starts_no_second_route(isolated_data_dir: Path, tmp_path: Path) -> None:
    """``POST /imports/`` is redirected to ``/imports``; the redirect itself creates nothing."""
    archive = _plain_zip(tmp_path, "sample_run.fit")
    with TestClient(app, follow_redirects=False) as client, archive.open("rb") as fh:
        response = client.post("/imports/", files={"file": (archive.name, fh, "application/zip")})
    assert response.status_code == 307
    assert response.headers["location"].endswith("/imports")
    assert _import_rows(isolated_data_dir) == []


# --- AC8: lifecycle and interruption ------------------------------------------------------------


def test_an_upload_during_a_running_import_leaves_it_running(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_fit_bytes
) -> None:
    gate = _gate_ingest(monkeypatch)
    archive = _plain_zip(tmp_path, "sample_run.fit", "strap_run_hrv.fit")
    try:
        with TestClient(app) as client:
            started = _post_archive(client, archive)
            assert started.status_code == 202, started.text
            import_id = started.json()["import_id"]
            _wait_for_status(client, import_id, "running")

            one_file = post_fit_bytes(client, "wrist_ppg_run.fit", (FIXTURES / "wrist_ppg_run.fit").read_bytes())
            assert one_file.status_code == 201, one_file.text
            assert _get(client, import_id)["status"] == "running"
            gate.set()
            _drain()
            done = _get(client, import_id)
    finally:
        gate.set()
    assert done["status"] == "completed", done["error"]
    assert [row["outcome"] for row in done["files"]] == ["stored", "stored"]


def test_deleting_an_imported_session_keeps_its_files_row(isolated_data_dir: Path, tmp_path: Path) -> None:
    with TestClient(app) as client:
        started = _post_archive(client, _plain_zip(tmp_path, "sample_run.fit"))
        assert started.status_code == 202, started.text
        import_id = started.json()["import_id"]
        _drain()
        before = _get(client, import_id)["files"]
        (row,) = before
        assert row["outcome"] == "stored"
        assert client.delete(f"/sessions/{row['session_id']}").status_code == 204
        assert client.get(f"/sessions/{row['session_id']}").status_code == 404
        after = _get(client, import_id)["files"]
    assert after == before


class _ApiStopped(BaseException):
    """Stands in for the process ending under the daemon worker."""


def test_startup_marks_a_running_import_interrupted(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture_declared
) -> None:
    """A job left ``running`` with two stored rows and its temp dir: the next startup (the
    lifespan of ``with TestClient(app)``) marks it ``interrupted`` with ``finished_at``, removes the
    dir, and keeps its two sessions with their ``import_id`` and its two report rows."""
    with TestClient(app):
        pass  # the schema
    conn = db.get_connection(isolated_data_dir)
    try:
        import_id = job.create_job(conn, "export.zip", 1)
    finally:
        conn.close()
    target = job.archive_path(isolated_data_dir, import_id)
    target.parent.mkdir(parents=True)
    target.write_bytes(_archive(tmp_path).read_bytes())

    calls: list[int] = []
    real = pipeline.ingest_fit_bytes

    def stop_on_third(raw, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise _ApiStopped()
        return real(raw, **kwargs)

    monkeypatch.setattr(job.pipeline, "ingest_fit_bytes", stop_on_third)
    with pytest.raises(_ApiStopped):
        job.run_job(import_id, isolated_data_dir, [CAPTURE_PROFILE], ZoneInfo("UTC"))
    assert job.job_dir(isolated_data_dir, import_id).is_dir()

    with TestClient(app) as client:
        after = _get(client, import_id)
        sessions = [client.get(f"/sessions/{row['session_id']}").json() for row in after["files"]]
    assert after["status"] == "interrupted"
    assert after["finished_at"] is not None
    assert [row["outcome"] for row in after["files"]] == ["stored", "stored"]
    assert [s["import_id"] for s in sessions] == [import_id, import_id]
    assert not job.job_dir(isolated_data_dir, import_id).exists()


def test_a_completed_import_leaves_no_temp_dir(isolated_data_dir: Path, tmp_path: Path) -> None:
    with TestClient(app) as client:
        started = _post_archive(client, _plain_zip(tmp_path, "sample_run.fit"))
        assert started.status_code == 202, started.text
        import_id = started.json()["import_id"]
        _drain()
        assert _get(client, import_id)["status"] == "completed"
    assert not job.job_dir(isolated_data_dir, import_id).exists()
    assert _temp_children(isolated_data_dir) == []


# --- AC5: the list ------------------------------------------------------------------------------


def test_list_imports_is_newest_first_without_files(isolated_data_dir: Path, tmp_path: Path) -> None:
    archive = _plain_zip(tmp_path, "sample_run.fit", "strap_run_hrv.fit")
    ids = []
    with TestClient(app) as client:
        for _ in range(2):
            response = _post_archive(client, archive)
            assert response.status_code == 202, response.text
            ids.append(response.json()["import_id"])
            _drain()
        listed = client.get("/imports")
        second = _get(client, ids[1])
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [item["import_id"] for item in items] == ids[::-1]
    assert all(set(item) == IMPORT_KEYS for item in items), items
    assert [item["status"] for item in items] == ["completed", "completed"]
    assert [row["outcome"] for row in second["files"]] == ["already_stored", "already_stored"]


def test_a_bytes_buffer_upload_is_accepted(isolated_data_dir: Path, tmp_path: Path) -> None:
    """The part may come from memory as well as from a file handle."""
    raw = _plain_zip(tmp_path, "sample_run.fit").read_bytes()
    with TestClient(app) as client:
        response = client.post("/imports", files={"file": ("mem.zip", io.BytesIO(raw), "application/zip")})
        assert response.status_code == 202, response.text
        _drain()
        assert _get(client, response.json()["import_id"])["status"] == "completed"
