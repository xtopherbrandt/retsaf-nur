"""``runcoach sync`` against hostile downloads and summaries (F022 reference section 7).

One test per row of the section 7 table. Each runs ``runcoach sync`` through ``CliRunner`` with a
fake Garmin client and a mock API, under ``monkeypatch.chdir(tmp_path)``; the test home (the CLI
config, and the Garmin token directory beside it) is under ``tmp_path`` too. Each asserts that no
file was written under ``tmp_path`` (a listing before and after) and that no exception escaped
(``result.exception`` is ``None`` or the ``SystemExit`` of a clean exit code).

Rows: bytes that are not a zip (activity and snapshot); an empty zip (activity: failed; snapshot:
zero items); a non-FIT entry (uploaded; the 400 is failed); an entry over the cap (not uploaded,
failed) and one at the cap (uploaded); entry names ``../x.fit`` and ``C:\\x.fit`` (read in memory,
never written); a corrupt entry (failed, the rest go on); summaries lacking ``typeId``,
``activityId`` or ``startTimeLocal`` (failed, naming it); a snapshot day answering 404 or an empty
body (zero items); any other HTTP error status for a snapshot or activity download (failed, the sync going on: driven
through the real library with only its HTTP session stubbed); a 401 or 403 or no reply at all for a
download, and an error on the activity list (stops).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import requests
from runcoach_cli import garmin, garmin_connect
from runcoach_cli.commands import sessions, sync
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

TODAY = date(2026, 10, 11)
START = date(2026, 10, 10)

SWITCH_DOC = {
    "paths": {
        "/sessions": {
            "post": {
                "requestBody": {
                    "content": {
                        "multipart/form-data": {"schema": {"properties": {"runs_and_captures_only": {}}}}
                    }
                }
            }
        }
    }
}


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            info = zipfile.ZipInfo("placeholder")
            info.filename = name  # set after construction, so a backslash is kept as written
            archive.writestr(info, data)
    return buffer.getvalue()


def _activity(activity_id: int, local: str = "2026-10-10 07:00:00") -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityName": f"run {activity_id}",
        "startTimeLocal": local,
        "activityType": {"typeKey": "running", "typeId": 1, "parentTypeId": 17},
        "manualActivity": False,
    }


class FakeGarmin:
    def __init__(self, activities=None, originals=None, snapshots=None) -> None:
        self.activities = activities or []
        self.originals = originals or {}
        self.snapshots = snapshots or {}
        self.events: list[tuple[str, Any]] = []

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        return list(self.activities)

    def download_original(self, activity_id: int) -> bytes:
        self.events.append(("original", activity_id))
        value = self.originals.get(
            activity_id, _zip([(f"{activity_id}_ACTIVITY.fit", f"ACT-{activity_id}".encode())])
        )
        if isinstance(value, Exception):
            raise value
        return value

    def download_snapshot(self, day: date) -> bytes:
        self.events.append(("snapshot", day))
        value = self.snapshots.get(day, garmin.NotFound("404"))
        if isinstance(value, Exception):
            raise value
        return value


def _file_part(body: bytes) -> bytes:
    for part in body.split(b"\r\n--"):
        head, _, content = part.partition(b"\r\n\r\n")
        if b'name="file"' in head:
            return content.removesuffix(b"\r\n")
    raise AssertionError("no file part")


class Api:
    """Answers an upload 400 when its bytes start with ``NOTFIT``, else 201 stored."""

    def __init__(self) -> None:
        self.uploads: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/sessions":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if request.method == "GET" and request.url.path == "/openapi.json":
            return httpx.Response(200, json=SWITCH_DOC)
        if request.method == "POST" and request.url.path == "/sessions":
            content = _file_part(request.read())
            self.uploads.append(content)
            if content.startswith(b"NOTFIT"):
                return httpx.Response(400, json={"detail": "not a FIT file"})
            return httpx.Response(201, json={"session_id": f"s{len(self.uploads)}", "filter_applied": True})
        raise AssertionError(f"unexpected request {request.method} {request.url}")


def _listing(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")}


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, install_mock_client, tmp_path: Path):
    """Run ``runcoach sync --since 2026-10-10`` against ``fake``; assert nothing written, nothing escaped."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", timezone(timedelta(hours=-7)))

    def go(fake: FakeGarmin) -> tuple[Any, Api]:
        api = Api()
        install_mock_client(api)
        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: fake))
        before = _listing(tmp_path)
        result = runner.invoke(app, ["sync", "--since", "2026-10-10"], input=b"")
        after = _listing(tmp_path)
        assert after == before, sorted(set(after) ^ set(before))
        assert result.exception is None or type(result.exception) is SystemExit, repr(result.exception)
        assert "Traceback" not in result.output
        return result, api

    return go


def _failed(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("Failed")]


def test_an_activity_download_that_is_not_a_zip_is_failed(run) -> None:
    fake = FakeGarmin(
        [_activity(101), _activity(102, "2026-10-11 07:00:00")], originals={101: b"\x00\x01garbage"}
    )
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    failed = _failed(result.output)
    assert len(failed) == 1
    assert failed[0].startswith("Failed: 2026-10-10, activity 101 (run 101), not a zip")
    assert failed[0].endswith("runcoach sync --since 2026-10-10")
    assert api.uploads == [b"ACT-102"]


def test_a_snapshot_download_that_is_not_a_zip_is_failed(run) -> None:
    fake = FakeGarmin([_activity(101)], snapshots={START: b"PK\x03\x04truncated"})
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    failed = _failed(result.output)
    assert len(failed) == 1 and failed[0].startswith("Failed: 2026-10-10, snapshot 2026-10-10, not a zip")
    assert api.uploads == [b"ACT-101"]


def test_an_empty_zip_is_failed_for_an_activity_and_zero_items_for_a_snapshot(run) -> None:
    fake = FakeGarmin([_activity(101)], originals={101: _zip([])}, snapshots={START: _zip([])})
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    assert _failed(result.output) == [
        "Failed: 2026-10-10, activity 101 (run 101), the download holds no file, runcoach sync --since 2026-10-10"
    ]
    assert api.uploads == []


def test_a_non_fit_entry_is_uploaded_and_its_400_is_failed(run) -> None:
    fake = FakeGarmin(
        [_activity(101)], originals={101: _zip([("notes.txt", b"NOTFIT hello"), ("101.fit", b"ACT-101")])}
    )
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    assert api.uploads == [b"NOTFIT hello", b"ACT-101"]
    assert _failed(result.output) == [
        (
            "Failed: 2026-10-10, activity 101 (run 101): notes.txt, API returned 400: not a FIT file, "
            "runcoach sync --since 2026-10-10"
        )
    ]
    assert "Stored: 1" in result.output


def test_an_entry_over_the_cap_is_not_uploaded_and_is_failed(run, monkeypatch) -> None:
    assert sync.FIT_ENTRY_CAP == 50 * 1024 * 1024
    monkeypatch.setattr(sync, "FIT_ENTRY_CAP", 64)
    fake = FakeGarmin(
        [_activity(101)], originals={101: _zip([("big.fit", b"B" * 65), ("cap.fit", b"C" * 64)])}
    )
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    assert api.uploads == [b"C" * 64]
    assert _failed(result.output) == [
        (
            "Failed: 2026-10-10, activity 101 (run 101): big.fit, the entry is over 50 MB and was not uploaded, "
            "runcoach sync --since 2026-10-10"
        )
    ]


def test_an_entry_over_the_cap_reads_at_most_cap_plus_one_bytes(run, monkeypatch) -> None:
    monkeypatch.setattr(sync, "FIT_ENTRY_CAP", 64)
    reads: list[int] = []
    real_open = zipfile.ZipFile.open

    def spying_open(self, name, *args, **kwargs):
        handle = real_open(self, name, *args, **kwargs)
        real_read = handle.read

        def read(n=-1):
            reads.append(n)
            return real_read(n)

        handle.read = read
        return handle

    monkeypatch.setattr(zipfile.ZipFile, "open", spying_open)
    fake = FakeGarmin([_activity(101)], originals={101: _zip([("big.fit", b"B" * 10_000)])})
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    assert reads == [65]
    assert api.uploads == []


def test_an_entry_named_dot_dot_slash_is_read_in_memory_and_never_written(run) -> None:
    fake = FakeGarmin([_activity(101)], originals={101: _zip([("../x.fit", b"ACT-dotdot")])})
    result, api = run(fake)

    assert result.exit_code == 0, result.output
    assert api.uploads == [b"ACT-dotdot"]
    assert not (Path.cwd().parent / "x.fit").exists()


def test_an_entry_named_with_a_drive_is_read_in_memory_and_never_written(run) -> None:
    fake = FakeGarmin(
        [_activity(101)], snapshots={START: _zip([("C:\\x.fit", b"SNAP-drive"), ("/abs/y.fit", b"SNAP-abs")])}
    )
    result, api = run(fake)

    assert result.exit_code == 0, result.output
    assert api.uploads == [b"SNAP-drive", b"SNAP-abs", b"ACT-101"]


def test_a_corrupt_entry_is_failed_and_the_rest_go_on(run) -> None:
    good = _zip([("a.fit", b"ACT-a" * 20), ("b.fit", b"ACT-b")])
    # Corrupt a.fit's data bytes so its CRC check fails on read.
    corrupt = good.replace(b"ACT-aACT-a", b"XXXXXXXXXX", 1)
    fake = FakeGarmin([_activity(101)], originals={101: corrupt})
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    assert api.uploads == [b"ACT-b"]
    failed = _failed(result.output)
    assert len(failed) == 1 and failed[0].startswith("Failed: 2026-10-10, activity 101 (run 101): a.fit, ")


def test_a_bzip2_or_lzma_entry_is_failed_and_never_decompressed(run, monkeypatch) -> None:
    # CPython decompresses a whole bzip2 or LZMA entry before read(n) slices it, so the entry cap
    # cannot bound memory for them: a 471-byte bzip2 zip peaked at 870 MB. They are refused unread.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.fit", b"ACT-bz", compress_type=zipfile.ZIP_BZIP2)
        archive.writestr("b.fit", b"ACT-xz", compress_type=zipfile.ZIP_LZMA)
        archive.writestr("c.fit", b"ACT-c", compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("d.fit", b"ACT-d", compress_type=zipfile.ZIP_STORED)
    decompressors: list[int] = []
    real = zipfile._get_decompressor

    def spy(compress_type: int):
        decompressors.append(compress_type)
        return real(compress_type)

    monkeypatch.setattr(zipfile, "_get_decompressor", spy)
    fake = FakeGarmin([_activity(101)], originals={101: buffer.getvalue()})
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    assert api.uploads == [b"ACT-c", b"ACT-d"]
    assert decompressors == [zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED]
    assert _failed(result.output) == [
        (
            f"Failed: 2026-10-10, activity 101 (run 101): {name}, the entry is compressed with {method}, "
            "which sync does not read (only stored or deflated entries), runcoach sync --since 2026-10-10"
        )
        for name, method in (("a.fit", "bzip2"), ("b.fit", "lzma"))
    ]


def test_running_out_of_memory_reading_an_entry_is_failed(run, monkeypatch) -> None:
    # The backstop: a MemoryError while an entry is read fails that entry and the sync goes on.
    download = _zip([("a.fit", b"ACT-a"), ("b.fit", b"ACT-b")])
    real_open = zipfile.ZipFile.open

    def open_entry(self, name, *args, **kwargs):
        if getattr(name, "filename", name) == "a.fit":
            raise MemoryError
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", open_entry)
    fake = FakeGarmin([_activity(101)], originals={101: download})
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    assert api.uploads == [b"ACT-b"]
    assert _failed(result.output) == [
        "Failed: 2026-10-10, activity 101 (run 101): a.fit, MemoryError, runcoach sync --since 2026-10-10"
    ]


@pytest.mark.parametrize(
    ("summary", "named"),
    [
        (
            {
                "activityId": 7,
                "activityName": "no type",
                "startTimeLocal": "2026-10-10 07:00:00",
                "activityType": {"typeKey": "hiking"},
            },
            "activity 7 (no type)",
        ),
        (
            {
                "activityId": 8,
                "activityName": "type not an object",
                "startTimeLocal": "2026-10-10 07:00:00",
                "activityType": "running",
            },
            "activity 8 (type not an object)",
        ),
        (
            {"activityId": 9, "activityName": "no activityType", "startTimeLocal": "2026-10-10 07:00:00"},
            "activity 9 (no activityType)",
        ),
    ],
)
def test_a_summary_lacking_type_id_is_failed_naming_it(run, summary, named) -> None:
    """A summary without ``typeId`` is failed even when its other type fields miss the filter."""
    fake = FakeGarmin([summary, _activity(101)])
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    failed = _failed(result.output)
    assert len(failed) == 1
    assert failed[0].startswith(f"Failed: day unknown, {named}, unreadable summary: ")
    assert "typeId" in failed[0] or "activityType" in failed[0]
    assert failed[0].endswith("runcoach sync --since 2026-10-10")
    assert api.uploads == [b"ACT-101"]


@pytest.mark.parametrize("activity_id", [None, "12", True, 1.5])
def test_a_summary_lacking_activity_id_is_failed_naming_it(run, activity_id) -> None:
    summary = _activity(0)
    summary["activityName"] = "nameless id"
    if activity_id is None:
        del summary["activityId"]
    else:
        summary["activityId"] = activity_id
    fake = FakeGarmin([summary, _activity(101)])
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    failed = _failed(result.output)
    assert len(failed) == 1
    assert failed[0].startswith(
        f"Failed: day unknown, activity {activity_id} (nameless id), unreadable summary: "
    )
    assert "activityId" in failed[0]
    assert api.uploads == [b"ACT-101"]


@pytest.mark.parametrize("local", [None, "", "yesterday", 20261010])
def test_a_summary_lacking_start_time_local_is_failed_naming_it(run, local) -> None:
    summary = _activity(55)
    if local is None:
        del summary["startTimeLocal"]
    else:
        summary["startTimeLocal"] = local
    fake = FakeGarmin([summary, _activity(101)])
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    failed = _failed(result.output)
    assert len(failed) == 1
    assert failed[0].startswith("Failed: day unknown, activity 55 (run 55), unreadable summary: ")
    assert "startTimeLocal" in failed[0]
    assert ("original", 55) not in fake.events
    assert api.uploads == [b"ACT-101"]


def test_a_summary_that_is_not_an_object_is_failed(run) -> None:
    fake = FakeGarmin(["not a dict", _activity(101)])
    result, api = run(fake)

    assert result.exit_code == 1, result.output
    failed = _failed(result.output)
    assert len(failed) == 1 and failed[0].startswith(
        "Failed: day unknown, activity 'not a dict', unreadable summary"
    )
    assert api.uploads == [b"ACT-101"]


def test_a_snapshot_day_answering_404_is_zero_items(run) -> None:
    fake = FakeGarmin(
        [_activity(101)], snapshots={START: garmin.NotFound("404"), TODAY: garmin.NotFound("404")}
    )
    result, api = run(fake)

    assert result.exit_code == 0, result.output
    assert api.uploads == [b"ACT-101"]
    assert _failed(result.output) == []
    assert fake.events == [("snapshot", START), ("original", 101), ("snapshot", TODAY)]


def test_a_snapshot_day_answering_an_empty_body_is_zero_items(run) -> None:
    fake = FakeGarmin([_activity(101)], snapshots={START: b"", TODAY: b""})
    result, api = run(fake)

    assert result.exit_code == 0, result.output
    assert api.uploads == [b"ACT-101"]
    assert _failed(result.output) == []


def _library_client(library_garmin, activities, snapshots=None, originals=None):
    """The real adapter over the real library, with only the library's HTTP session stubbed.

    ``snapshots`` and ``originals`` map a day or an activity id to what Garmin answers: an HTTP
    status, zip bytes or a ``requests`` error. A day with no entry answers 404 (no snapshot); an
    activity with no entry answers a one-entry zip. Returns ``(client, session)``.
    """
    snapshots = snapshots or {}
    originals = originals or {}

    def answer(method: str, url: str, params: dict) -> object:
        tail = url.rsplit("/", 1)[1]
        if "/activitylist-service/" in url:
            return list(activities) if params.get("start") == "0" else []
        if "/download-service/files/wellness/" in url:
            return snapshots.get(date.fromisoformat(tail), 404)
        if "/download-service/files/activity/" in url:
            activity_id = int(tail)
            return originals.get(activity_id, _zip([(f"{activity_id}_ACTIVITY.fit", f"ACT-{activity_id}".encode())]))
        raise AssertionError(f"unexpected Garmin request {method} {url}")

    lib, session = library_garmin(answer)
    return garmin_connect.GarminConnectClient(lib), session


def _requested(session) -> list[str]:
    """The Garmin requests made, as ``snapshot <day>`` / ``original <id>`` / ``list``."""
    names = []
    for _, url in session.requests:
        tail = url.rsplit("/", 1)[1]
        if "/files/wellness/" in url:
            names.append(f"snapshot {tail}")
        elif "/files/activity/" in url:
            names.append(f"original {tail}")
        else:
            names.append("list")
    return names


# Any HTTP status but 401, 403, 404 and 429 on a download is the item's failure (the user's ruling,
# 2026-10-13): 4xx at once, 5xx after the library's own retries. Driven through the real library.
OTHER_STATUSES = [400, 410, 500, 503]


@pytest.mark.parametrize("status", OTHER_STATUSES)
def test_any_other_snapshot_error_status_is_failed(run, library_garmin, status) -> None:
    client, session = _library_client(library_garmin, [_activity(101)], snapshots={START: status})
    result, api = run(client)

    assert result.exit_code == 1, result.output
    (failed,) = _failed(result.output)
    assert failed.startswith("Failed: 2026-10-10, snapshot 2026-10-10, Garmin: "), failed
    assert f"API Error {status}" in failed
    assert failed.endswith(", runcoach sync --since 2026-10-10")
    # Not a stop: the day's activity and the next day are still fetched.
    assert api.uploads == [b"ACT-101"]
    downloads = [name for name in _requested(session) if name != "list"]
    assert downloads == ["snapshot 2026-10-10", "original 101", "snapshot 2026-10-11"]


@pytest.mark.parametrize("status", OTHER_STATUSES)
def test_any_other_activity_download_error_status_is_failed(run, library_garmin, status) -> None:
    client, session = _library_client(
        library_garmin, [_activity(101), _activity(102, "2026-10-10 08:00:00")], originals={101: status}
    )
    result, api = run(client)

    assert result.exit_code == 1, result.output
    (failed,) = _failed(result.output)
    assert failed.startswith("Failed: 2026-10-10, activity 101 (run 101), Garmin: "), failed
    assert f"API Error {status}" in failed
    assert failed.endswith(", runcoach sync --since 2026-10-10")
    assert api.uploads == [b"ACT-102"]
    assert _requested(session)[-2:] == ["original 102", "snapshot 2026-10-11"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_download_stops_with_login_needed(run, library_garmin, status) -> None:
    client, session = _library_client(library_garmin, [_activity(101)], originals={101: status})
    result, api = run(client)

    assert result.exit_code == 1, result.output
    assert result.output.rstrip().endswith(sync.LOGIN_NEEDED), result.output
    assert api.uploads == []
    assert "snapshot 2026-10-11" not in _requested(session)


def test_a_network_error_on_a_download_stops_as_unreachable(run, library_garmin) -> None:
    refused = requests.ConnectionError("Failed to establish a new connection: [WinError 10061] refused")
    client, session = _library_client(library_garmin, [_activity(101)], originals={101: refused})
    result, api = run(client)

    assert result.exit_code == 1, result.output
    last = result.output.rstrip().splitlines()[-1]
    assert "could not reach Garmin Connect" in last, result.output
    assert last.endswith("run `runcoach sync --since 2026-10-10`")
    assert "snapshot 2026-10-11" not in _requested(session)
    assert api.uploads == []


@pytest.mark.parametrize("status", [400, 404, 500, 503])
def test_an_http_error_on_the_activity_list_stops_without_claiming_garmin_is_unreachable(
    run, library_garmin, status
) -> None:
    client, session = _library_client(library_garmin, [])
    client._garmin.client._api_session.answer = lambda method, url, params: status
    result, api = run(client)

    assert result.exit_code == 1, result.output
    last = result.output.rstrip().splitlines()[-1]
    assert "could not reach" not in last, last
    assert f"API Error {status}" in last, last
    assert last.endswith("run `runcoach sync --since 2026-10-10`"), last
    assert _requested(session)[-1] == "list"
    assert api.uploads == []


@pytest.mark.parametrize(
    ("answer", "ending"),
    [
        (403, sync.LOGIN_NEEDED),
        (429, "wait, then run `runcoach sync --since 2026-10-10`"),
        (requests.Timeout("Read timed out. (read timeout=15)"), "run `runcoach sync --since 2026-10-10`"),
    ],
    ids=["403", "429", "timeout"],
)
def test_the_activity_list_stops_on_a_refusal_a_rate_limit_or_no_reply(
    run, library_garmin, answer, ending
) -> None:
    client, _ = _library_client(library_garmin, [])
    client._garmin.client._api_session.answer = lambda method, url, params: answer
    result, api = run(client)

    assert result.exit_code == 1, result.output
    last = result.output.rstrip().splitlines()[-1]
    assert last.endswith(ending), last
    if isinstance(answer, Exception):
        assert "could not reach Garmin Connect" in last, last
    assert api.uploads == []
