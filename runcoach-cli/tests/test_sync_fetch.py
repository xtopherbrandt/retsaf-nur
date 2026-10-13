"""``runcoach sync``: what is fetched and the upload order (F022 reference section 3).

A fake ``GarminClient`` serves three days (2026-10-10 to 10-12): a run, a treadmill run
(parentTypeId 1), a trail run, a strap check (``other``), a ski day (``other``), a cycling ride, a
manual activity and Health Snapshot zips with monitoring entries, plus a run the day before the
range. ``POST /sessions`` is served by ``install_mock_client``. Every file is opaque bytes naming
itself (the API is mocked, so nothing is decoded); the upload order is read back from those bytes.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from runcoach_cli import garmin
from runcoach_cli.commands import sessions, sync
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

TODAY = date(2026, 10, 12)
START = date(2026, 10, 10)
API_URL = "http://example.test"


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _activity(
    activity_id: int, local: str, type_key: str, type_id: int, parent: int, manual: bool = False
) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityName": f"activity {activity_id}",
        "startTimeLocal": local,
        "activityType": {"typeKey": type_key, "typeId": type_id, "parentTypeId": parent},
        "manualActivity": manual,
    }


ACTIVITIES = [
    # Listed newest first, as Connect lists them; sync must reorder.
    _activity(301, "2026-10-12 08:00:00", "other", 4, 17),  # ski day
    _activity(203, "2026-10-11 09:00:00", "running", 1, 17, manual=True),  # manual
    _activity(202, "2026-10-11 18:00:00", "treadmill_running", 18, 1),
    _activity(201, "2026-10-11 18:00:00", "trail_running", 6, 1),  # same start: id order
    _activity(103, "2026-10-10 12:00:00", "cycling", 2, 17),  # ride: never downloaded
    _activity(101, "2026-10-10 07:00:00", "running", 1, 17),  # run
    _activity(102, "2026-10-10 06:30:00", "other", 4, 17),  # strap check, before the run
    _activity(99, "2026-10-09 07:00:00", "running", 1, 17),  # before start: ignored
]

SNAPSHOTS = {
    date(2026, 10, 10): _zip(
        [
            ("1_ACTIVITY.fit", b"SNAP-1010-activity"),
            ("2_WELLNESS.fit", b"SNAP-1010-wellness"),
            ("3_METRICS.fit", b"SNAP-1010-metrics"),
        ]
    ),
    date(2026, 10, 11): _zip([("4_WELLNESS.fit", b"SNAP-1011-wellness")]),
}


class FakeGarmin:
    def __init__(
        self,
        activities: list[dict[str, Any]],
        snapshots: dict[date, bytes],
        originals: dict[int, bytes] | None = None,
    ) -> None:
        self.activities = activities
        self.snapshots = snapshots
        self.originals = originals
        self.events: list[tuple[str, Any]] = []

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        self.events.append(("list", (start, end)))
        return list(self.activities)

    def download_original(self, activity_id: int) -> bytes:
        self.events.append(("original", activity_id))
        if self.originals is not None:
            return self.originals[activity_id]
        return _zip([(f"{activity_id}_ACTIVITY.fit", f"ACT-{activity_id}".encode())])

    def download_snapshot(self, day: date) -> bytes:
        self.events.append(("snapshot", day))
        if day not in self.snapshots:
            raise garmin.NotFound(f"no snapshot on {day}")
        return self.snapshots[day]


def _part(body: bytes, field: str) -> tuple[bytes, bytes]:
    """The headers and content of the multipart part named ``field``."""
    marker = f'name="{field}"'.encode()
    for part in body.split(b"\r\n--"):
        head, _, content = part.partition(b"\r\n\r\n")
        if marker in head:
            return head, content.removesuffix(b"\r\n")
    raise AssertionError(f"no part {field}")


# ``/openapi.json`` as FastAPI serves it: ``POST /sessions`` lists the switch in its form body.
OPENAPI = {
    "paths": {
        "/sessions": {
            "post": {
                "requestBody": {
                    "content": {"multipart/form-data": {"schema": {"$ref": "#/components/schemas/Body"}}}
                }
            }
        }
    },
    "components": {"schemas": {"Body": {"properties": {"file": {}, "runs_and_captures_only": {}}}}},
}


class Api:
    """Serves an empty ``GET /sessions`` and ``/openapi.json``; answers each upload 201, recording it."""

    def __init__(self) -> None:
        self.uploads: list[tuple[bytes, bytes, bytes]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/sessions":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if request.method == "GET" and request.url.path == "/openapi.json":
            return httpx.Response(200, json=OPENAPI)
        if request.method == "POST" and request.url.path == "/sessions":
            body = request.read()
            head, content = _part(body, "file")
            _, switch = _part(body, "runs_and_captures_only")
            assert b'name="resting_capture"' not in body
            self.uploads.append((content, switch, head))
            return httpx.Response(201, json={"session_id": f"s{len(self.uploads)}", "filter_applied": True})
        raise AssertionError(f"unexpected request {request.method} {request.url}")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, install_mock_client):
    def setup(fake: FakeGarmin) -> Api:
        api = Api()
        install_mock_client(api)
        monkeypatch.setattr(sync, "_library_missing", lambda: False)
        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: fake))
        monkeypatch.setattr(sync, "today", lambda: TODAY)
        monkeypatch.setattr(sessions, "LOCAL_ZONE", timezone(timedelta(hours=-7)))
        return api

    return setup


EXPECTED_UPLOADS = [
    b"SNAP-1010-activity",
    b"SNAP-1010-wellness",
    b"SNAP-1010-metrics",
    b"ACT-102",
    b"ACT-101",
    b"SNAP-1011-wellness",
    b"ACT-201",
    b"ACT-202",
    b"ACT-301",
]


def test_snapshot_zip_first_then_activities_by_start_then_id(env) -> None:
    fake = FakeGarmin(ACTIVITIES, SNAPSHOTS)
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "From 2026-10-10 to 2026-10-12"
    assert fake.events == [
        ("list", (START, TODAY)),
        ("snapshot", date(2026, 10, 10)),
        ("original", 102),
        ("original", 101),
        ("snapshot", date(2026, 10, 11)),
        ("original", 201),
        ("original", 202),
        ("snapshot", date(2026, 10, 12)),
        ("original", 301),
    ]
    assert [content for content, _, _ in api.uploads] == EXPECTED_UPLOADS


def test_only_the_running_family_and_other_are_downloaded(env) -> None:
    fake = FakeGarmin(ACTIVITIES, SNAPSHOTS)
    env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    downloaded = [arg for kind, arg in fake.events if kind == "original"]
    assert sorted(downloaded) == [101, 102, 201, 202, 301]
    assert 103 not in downloaded  # cycling
    assert 203 not in downloaded  # manual
    assert 99 not in downloaded  # before the range


def test_a_manual_activity_is_recorded_as_no_fit_original_and_never_downloaded(env) -> None:
    fake = FakeGarmin(ACTIVITIES, SNAPSHOTS)
    env(fake)

    results = sync.fetch(fake, API_URL, START, TODAY)

    manual = [r for r in results if r.activity_id == 203]
    assert len(manual) == 1
    assert manual[0].outcome == "no_fit_original"
    assert manual[0].response is None
    assert manual[0].day == date(2026, 10, 11)
    assert ("original", 203) not in fake.events
    assert all(r.activity_id != 103 for r in results)
    uploaded = [r for r in results if r.response is not None]
    assert [r.response.status_code for r in uploaded] == [201] * len(EXPECTED_UPLOADS)
    assert [r.day for r in uploaded] == [START] * 5 + [date(2026, 10, 11)] * 3 + [TODAY]


def test_every_zip_entry_is_uploaded_with_the_switch(env) -> None:
    odd = _zip(
        [
            ("readme.txt", b"ENTRY-readme"),
            ("../escape.fit", b"ENTRY-escape"),
            ("noext", b"ENTRY-noext"),
            ("sub/dir/x.FIT", b"ENTRY-nested"),
        ]
    )
    fake = FakeGarmin([], {START: odd})
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    assert [content for content, _, _ in api.uploads] == [
        b"ENTRY-readme",
        b"ENTRY-escape",
        b"ENTRY-noext",
        b"ENTRY-nested",
    ]
    assert all(switch == b"true" for _, switch, _ in api.uploads)
    for _, _, head in api.uploads:
        assert b'filename="garmin-' in head
        for name in (b"readme", b"escape", b"noext", b"x.FIT"):
            assert name not in head


def test_activities_outside_the_range_are_ignored(env) -> None:
    after = _activity(401, "2026-10-13 07:00:00", "running", 1, 17)
    fake = FakeGarmin([after, ACTIVITIES[-1]], {})
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in fake.events].count("original") == 0
    assert api.uploads == []


def test_a_day_with_no_snapshot_uploads_only_its_activities(env) -> None:
    fake = FakeGarmin([_activity(101, "2026-10-10 07:00:00", "running", 1, 17)], {START: b""})
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-10"])

    assert result.exit_code == 0, result.output
    assert [content for content, _, _ in api.uploads] == [b"ACT-101"]
