"""Tests for ``runcoach import`` and ``runcoach import-status`` (F020 AC10, against a mocked API).

The API is an ``httpx.MockTransport`` serving bodies shaped as the F020 reference's interface
section (``Import`` with ``progress``, ``counts`` and, on ``GET /imports/{id}``, ``files[]``). The
real-API run of the same criterion is a separate end-to-end test. The poll sleep is patched: to a
recorder, so no test waits, or to a function raising ``KeyboardInterrupt`` to simulate Ctrl-C.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from runcoach_cli import api_client
from runcoach_cli.commands import imports
from runcoach_cli.main import app

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("prewritten_config")

IMPORT_ID = "imp-0001"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the poll sleep with a recorder; a test that wants Ctrl-C patches it again."""
    slept: list[float] = []
    monkeypatch.setattr(imports, "sleep", slept.append)
    return slept


@pytest.fixture(autouse=True)
def private_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A per-test temp root, so a left-behind zip is visible and nothing lands in the real one."""
    root = tmp_path / "tmp-root"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _counts(**overrides: Any) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "stored": 0,
        "already_stored": 0,
        "failed": 0,
        "skipped_sport": {},
        "non_activity": {},
        "not_fit": {},
        "unreadable_archives": [],
    }
    counts.update(overrides)
    return counts


def _import(status: str, processed: int | None = None, total: int | None = None, **extra: Any) -> dict:
    body: dict[str, Any] = {
        "import_id": IMPORT_ID,
        "archive_name": "export.zip",
        "archive_bytes": 1234,
        "status": status,
        "error": None,
        "created_at": "2026-10-11T08:00:00+00:00",
        "started_at": None if status == "queued" else "2026-10-11T08:00:01+00:00",
        "finished_at": None,
        "progress": {
            "fit_files_scanned": 0,
            "activities_total": total,
            "activities_processed": processed,
        },
        "counts": _counts(),
    }
    body.update(extra)
    return body


def _row(seq: int, entry: str, outcome: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "seq": seq,
        "entry": entry,
        "time_created": "2026-01-01T00:00:00+00:00",
        "outcome": outcome,
        "session_id": None,
        "sport": None,
        "activity_tag": None,
        "quality_flags": [],
        "status": None,
        "detail": None,
    }
    row.update(extra)
    return row


FINISHED_FILES = [
    _row(1, "a/run1.fit", "stored", session_id="s1", sport="running"),
    _row(2, "a/run2.fit", "stored", session_id="s2", sport="running"),
    _row(3, "a/hrv.fit", "stored", session_id="s3", sport="other", activity_tag="resting_hrv_check"),
    _row(4, "a/snap.fit", "stored", session_id="s4", sport="other", activity_tag="health_snapshot"),
    _row(5, "a/dup.fit", "already_stored", session_id="s1", sport="running"),
    _row(6, "a/ride.fit", "skipped_sport", sport="cycling"),
    _row(7, "a/bad.fit", "failed", status=400, detail="not a FIT file: truncated"),
    _row(8, "a/odd.fit", "failed", status=None, detail="ValueError: boom"),
]

FINISHED_COUNTS = _counts(
    stored=4,
    already_stored=1,
    failed=2,
    skipped_sport={"cycling": 1},
    non_activity={"monitoring_b": 3, "sleep": 2},
    not_fit={"tcx": 1, "json": 2},
    unreadable_archives=[{"label": "a/broken.zip", "reason": "not a zip file"}],
)


def _finished(status: str = "completed", **extra: Any) -> dict:
    return _import(
        status,
        processed=250,
        total=250,
        counts=FINISHED_COUNTS,
        files=FINISHED_FILES,
        finished_at="2026-10-11T08:05:00+00:00",
        **extra,
    )


class _Api:
    """Serve ``POST /imports`` with ``start`` and each ``GET /imports/{id}`` with the next of ``polls``.

    A poll entry is an ``httpx.Response``, a body dict (served 200), or an exception to raise.
    The last entry repeats once the list is exhausted.
    """

    def __init__(self, polls: list[Any], start: httpx.Response | None = None, listing: Any = None):
        self.polls = list(polls)
        self.start = start or httpx.Response(202, json=_import("queued"))
        self.listing = listing
        self.requests: list[httpx.Request] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.on_upload: Callable[[], None] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/imports":
            self.uploads.append(_file_part(request))
            if self.on_upload is not None:
                self.on_upload()
            return self.start
        if request.method == "GET" and path == "/imports":
            return httpx.Response(200, json=self.listing)
        if request.method == "GET" and path.startswith("/imports/"):
            entry = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            if isinstance(entry, BaseException):
                raise entry
            if isinstance(entry, httpx.Response):
                return entry
            return httpx.Response(200, json=entry)
        return httpx.Response(599, text=f"unexpected {request.method} {path}")

    def gets(self) -> list[str]:
        return [r.url.path for r in self.requests if r.method == "GET"]


def _file_part(request: httpx.Request) -> tuple[str, bytes]:
    """The ``file`` part's file name and bytes from a multipart request body."""
    request.read()
    content_type = request.headers["content-type"]
    boundary = content_type.split("boundary=", 1)[1].encode()
    for part in request.content.split(b"--" + boundary):
        head, _, body = part.partition(b"\r\n\r\n")
        if b'name="file"' in head:
            name = head.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
            return name, body.removesuffix(b"\r\n")
    raise AssertionError("no file part in the upload")


def _zip_file(tmp_path: Path) -> Path:
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("x.fit", b"not really fit")
    return path


def _invoke(*args: str):
    return runner.invoke(app, list(args))


def test_import_prints_the_id_status_lines_and_summary_and_exits_0(
    tmp_path: Path, install_mock_client, no_wait: list[float]
) -> None:
    archive = _zip_file(tmp_path)
    api = _Api(
        [
            _import("queued"),
            _import("scanning"),
            _import("running", processed=0, total=250),
            _import("running", processed=99, total=250),
            _import("running", processed=120, total=250),
            _import("running", processed=150, total=250),
            _import("running", processed=230, total=250),
            _finished(),
        ]
    )
    install_mock_client(api)

    result = _invoke("import", str(archive))

    assert result.exit_code == 0, result.output
    out = result.output
    lines = out.splitlines()
    assert f"Import {IMPORT_ID} started" in lines[0]
    # One line per status change: queued is announced once though it was served twice.
    status_lines = [line for line in lines if line.startswith("Status:")]
    assert status_lines == [
        "Status: queued",
        "Status: scanning",
        "Status: running (250 activities)",
        "Status: completed",
    ]
    # One line each time activities_processed passes a multiple of 100, never twice for one.
    assert [line for line in lines if line.startswith("Processed")] == [
        "Processed 120 of 250",
        "Processed 230 of 250",
    ]
    for expected in [
        "Stored: 2 runs, 2 resting captures",
        "Already stored: 1",
        "Skipped by sport: 1 (cycling 1)",
        "Failed: 2",
        "  a/bad.fit  400  not a FIT file: truncated",
        "  a/odd.fit  -  ValueError: boom",
        "Non-activity files: 5",
        "Not FIT: 3 (json 2, tcx 1)",
        "Unreadable archives: 1",
        "  a/broken.zip: not a zip file",
    ]:
        assert expected in lines, (expected, out)
    # The upload is the zip itself as the multipart part ``file``, under its own name.
    assert api.uploads == [("export.zip", archive.read_bytes())]
    assert set(api.gets()) == {f"/imports/{IMPORT_ID}"}
    assert no_wait and all(seconds == imports.POLL_SECONDS for seconds in no_wait)
    assert imports.POLL_SECONDS == 2


def test_a_directory_is_zipped_uploaded_and_the_zip_removed(
    tmp_path: Path, install_mock_client, private_temp: Path
) -> None:
    source = tmp_path / "my-runs"
    (source / "nested" / "deeper").mkdir(parents=True)
    (source / "run1.fit").write_bytes(b"run one")
    (source / "nested" / "run2.fit").write_bytes(b"run two")
    (source / "nested" / "deeper" / "route.tcx").write_bytes(b"<tcx/>")
    before = sorted(p.relative_to(source).as_posix() for p in source.rglob("*"))

    seen_during_upload: list[list[Path]] = []
    api = _Api([_finished()])
    api.on_upload = lambda: seen_during_upload.append(sorted(private_temp.rglob("*.zip")))
    install_mock_client(api)

    result = _invoke("import", str(source))

    assert result.exit_code == 0, result.output
    [(name, payload)] = api.uploads
    assert name == "my-runs"  # archive_name is the directory's name
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        assert sorted(zf.namelist()) == ["nested/deeper/route.tcx", "nested/run2.fit", "run1.fit"]
        assert zf.read("nested/run2.fit") == b"run two"
        assert zf.read("nested/deeper/route.tcx") == b"<tcx/>"
    # The zip was written to the temp root, outside the directory, and is gone afterwards.
    assert len(seen_during_upload) == 1 and len(seen_during_upload[0]) == 1
    assert source not in seen_during_upload[0][0].parents
    assert list(private_temp.rglob("*")) == []
    assert sorted(p.relative_to(source).as_posix() for p in source.rglob("*")) == before


@pytest.mark.parametrize(
    "start",
    [
        httpx.Response(413, json={"detail": "archive over 1 GiB"}),
        httpx.ConnectError("refused"),
    ],
    ids=["non-2xx", "unreachable"],
)
def test_a_directory_zip_is_removed_when_the_upload_fails(
    tmp_path: Path, install_mock_client, private_temp: Path, start: Any
) -> None:
    source = tmp_path / "dir"
    source.mkdir()
    (source / "run1.fit").write_bytes(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(start, BaseException):
            raise start
        return start

    install_mock_client(handler)
    result = _invoke("import", str(source))
    assert result.exit_code == 1
    assert list(private_temp.rglob("*")) == []
    assert sorted(p.name for p in source.iterdir()) == ["run1.fit"]


def test_interrupting_prints_the_status_hint_and_exits_130(
    tmp_path: Path, install_mock_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ctrl_c(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(imports, "sleep", ctrl_c)
    api = _Api([_import("running", processed=3, total=10)])
    install_mock_client(api)

    result = _invoke("import", str(_zip_file(tmp_path)))

    assert result.exit_code == 130, result.output
    assert (
        f"Import {IMPORT_ID} keeps running in the API; `runcoach import-status {IMPORT_ID}` shows it."
        in result.output
    )
    # Nothing was sent to stop the job: one upload, then polls only.
    assert [r.method for r in api.requests].count("POST") == 1
    assert all(r.method == "GET" for r in api.requests[1:])


def test_three_failed_polls_exit_1(tmp_path: Path, install_mock_client) -> None:
    api = _Api(
        [
            httpx.Response(503, text="busy"),
            httpx.ConnectError("refused"),
            httpx.Response(200, text="<html>not json"),
            _finished(),
        ]
    )
    install_mock_client(api)

    result = _invoke("import", str(_zip_file(tmp_path)))

    assert result.exit_code == 1, result.output
    assert len(api.gets()) == 3  # it stopped at the third failure; the fourth body was never read
    assert f"`runcoach import-status {IMPORT_ID}`" in result.output
    assert "Status: completed" not in result.output


def test_two_failed_polls_then_a_good_one_carry_on(tmp_path: Path, install_mock_client) -> None:
    api = _Api(
        [
            _import("running", processed=1, total=10),
            httpx.Response(503, text="busy"),
            httpx.ConnectError("refused"),
            _import("running", processed=5, total=10),
            httpx.Response(503, text="busy"),
            httpx.Response(503, text="busy"),
            _finished(),
        ]
    )
    install_mock_client(api)

    result = _invoke("import", str(_zip_file(tmp_path)))

    assert result.exit_code == 0, result.output
    assert len(api.gets()) == 7
    assert "Status: completed" in result.output


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        ("failed", "disk full", "failed: disk full"),
        ("interrupted", None, "was interrupted"),
    ],
)
def test_a_failed_or_interrupted_import_prints_its_report_and_exits_1(
    tmp_path: Path, install_mock_client, status: str, error: str | None, expected: str
) -> None:
    install_mock_client(_Api([_finished(status, error=error)]))

    result = _invoke("import", str(_zip_file(tmp_path)))

    assert result.exit_code == 1, result.output
    assert f"Status: {status}" in result.output
    assert expected in result.output
    assert "Stored: 2 runs, 2 resting captures" in result.output


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (httpx.Response(409, json={"detail": "import imp-0000 is active"}), "API returned 409"),
        (httpx.Response(400, json={"detail": "not a zip"}), "API returned 400"),
        (httpx.ConnectError("refused"), "could not reach API at http://example.test"),
        (httpx.Response(202, text="not json"), "could not be understood"),
        (httpx.Response(202, json={"status": "queued"}), "could not be understood"),
    ],
    ids=["409", "400", "unreachable", "not-json", "no-id"],
)
def test_a_refused_upload_exits_1(
    tmp_path: Path, install_mock_client, start: Any, expected: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(start, BaseException):
            raise start
        return start

    install_mock_client(handler)
    result = _invoke("import", str(_zip_file(tmp_path)))
    assert result.exit_code == 1, result.output
    assert expected in result.output
    assert "Status:" not in result.output


def test_a_malformed_final_report_exits_1(tmp_path: Path, install_mock_client) -> None:
    install_mock_client(_Api([_import("completed", counts="nope")]))
    result = _invoke("import", str(_zip_file(tmp_path)))
    assert result.exit_code == 1, result.output
    assert "could not be understood" in result.output


def test_import_status_without_an_id_shows_the_newest(install_mock_client) -> None:
    newest = _finished()
    older = dict(_finished(), import_id="imp-0000")
    listing = {"items": [{k: v for k, v in body.items() if k != "files"} for body in (newest, older)]}
    api = _Api([_finished()], listing=listing)
    install_mock_client(api)

    result = _invoke("import-status")

    assert result.exit_code == 0, result.output
    assert api.gets() == ["/imports", f"/imports/{IMPORT_ID}"]
    assert f"Import {IMPORT_ID}" in result.output
    assert "Status: completed" in result.output
    assert "Stored: 2 runs, 2 resting captures" in result.output
    assert "  a/bad.fit  400  not a FIT file: truncated" in result.output


def test_import_status_with_an_id_shows_that_import_once(install_mock_client) -> None:
    api = _Api([_import("running", processed=5, total=10)])
    install_mock_client(api)

    result = _invoke("import-status", IMPORT_ID)

    assert result.exit_code == 0, result.output
    assert api.gets() == [f"/imports/{IMPORT_ID}"]
    assert "Status: running (10 activities)" in result.output
    assert "Processed 5 of 10" in result.output


def test_import_status_with_an_unknown_id_prints_the_404_detail_and_exits_1(install_mock_client) -> None:
    api = _Api([httpx.Response(404, json={"detail": "no import nope-1"})])
    install_mock_client(api)

    result = _invoke("import-status", "nope-1")

    assert result.exit_code == 1
    assert "no import nope-1" in result.output
    assert "404" in result.output


def test_import_status_with_no_imports_says_so(install_mock_client) -> None:
    install_mock_client(_Api([_finished()], listing={"items": []}))
    result = _invoke("import-status")
    assert result.exit_code == 0, result.output
    assert "No imports" in result.output


def test_import_status_with_an_unparseable_listing_exits_1(install_mock_client) -> None:
    install_mock_client(_Api([_finished()], listing={"nope": []}))
    result = _invoke("import-status")
    assert result.exit_code == 1
    assert "could not be understood" in result.output


@pytest.mark.parametrize("command", ["import", "import-status"])
def test_the_help_names_the_profile_setting_and_the_api_restart(command: str) -> None:
    result = _invoke(command, "--help")
    assert result.exit_code == 0
    flat = " ".join(result.output.split())
    assert "resting_hrv_profile_names" in flat
    assert "restart the API" in flat


def test_the_upload_has_its_own_long_timeout(tmp_path: Path, install_mock_client) -> None:
    api = _Api([_finished()])
    install_mock_client(api)
    _invoke("import", str(_zip_file(tmp_path)))
    upload = next(r for r in api.requests if r.method == "POST")
    assert api_client.IMPORT_UPLOAD_TIMEOUT == 600
    assert upload.extensions["timeout"]["read"] == 600
    poll = next(r for r in api.requests if r.method == "GET")
    assert poll.extensions["timeout"]["read"] == api_client.LIST_TIMEOUT
