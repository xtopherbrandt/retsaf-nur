"""``runcoach sync``: each item's outcome, the summary and the exit code (F022 reference section 5).

A fake Garmin client serves the range 2026-10-10 to 10-12 and a mock API answers each upload by
the bytes it was sent (every file is opaque bytes naming itself; nothing is decoded). The summary
follows the ``From`` line: ``Stored``, ``Already stored``, one ``Skipped <sport>`` line per sport
naming each item, ``Not an activity`` (a count), ``No FIT file`` (naming each) and one ``Failed``
line per failed item ending with ``runcoach sync --since <its day>``. Exit 0 when nothing failed,
else 1. A sync state file holding no dates is present (``fresh_sync_state``), so no line about a
missing one comes between the ``From`` line and the summary.
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

pytestmark = pytest.mark.usefixtures("fresh_sync_state")

runner = CliRunner()

TODAY = date(2026, 10, 12)
START = date(2026, 10, 10)
API_URL = "http://example.test"

SWITCH_DOC = {
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


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _activity(
    activity_id: int, local: str, name: str, type_key: str = "running", manual: bool = False
) -> dict[str, Any]:
    type_id = {"running": 1, "other": 4}[type_key]
    return {
        "activityId": activity_id,
        "activityName": name,
        "startTimeLocal": local,
        "activityType": {"typeKey": type_key, "typeId": type_id, "parentTypeId": 17},
        "manualActivity": manual,
    }


class FakeGarmin:
    def __init__(self, activities, originals=None, snapshots=None) -> None:
        self.activities = activities
        self.originals = originals or {}
        self.snapshots = snapshots or {}

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        return list(self.activities)

    def download_original(self, activity_id: int) -> bytes:
        value = self.originals.get(
            activity_id, _zip([(f"{activity_id}_ACTIVITY.fit", f"ACT-{activity_id}".encode())])
        )
        if isinstance(value, Exception):
            raise value
        return value

    def download_snapshot(self, day: date) -> bytes:
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


def _stored(n: int) -> httpx.Response:
    return httpx.Response(201, json={"session_id": f"s{n}", "filter_applied": True})


def _skipped_sport(sport: str) -> httpx.Response:
    return httpx.Response(
        200, json={"skipped": True, "reason": "skipped_sport", "sport": sport, "filter_applied": True}
    )


def _non_activity(file_type: str) -> httpx.Response:
    return httpx.Response(
        200, json={"skipped": True, "reason": "non_activity", "file_type": file_type, "filter_applied": True}
    )


class Api:
    """Answers each upload from ``replies[content]`` (default 201 stored)."""

    def __init__(self, replies: dict[bytes, httpx.Response] | None = None) -> None:
        self.replies = replies or {}
        self.uploads: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/sessions":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if request.method == "GET" and request.url.path == "/openapi.json":
            return httpx.Response(200, json=SWITCH_DOC)
        if request.method == "POST" and request.url.path == "/sessions":
            content = _file_part(request.read())
            self.uploads.append(content)
            return self.replies.get(content, _stored(len(self.uploads)))
        raise AssertionError(f"unexpected request {request.method} {request.url}")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, install_mock_client):
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", timezone(timedelta(hours=-7)))

    def setup(fake: FakeGarmin, api: Api) -> Api:
        install_mock_client(api)
        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=lambda: fake))
        return api

    return setup


def _sync(*args: str):
    return runner.invoke(app, ["sync", "--since", "2026-10-10", *args], input=b"")


def _summary(output: str) -> list[str]:
    """The lines after the ``From`` line."""
    lines = output.splitlines()
    assert lines[0] == "From 2026-10-10 to 2026-10-12", output
    return lines[1:]


def test_each_outcome_row_has_its_summary_line(env) -> None:
    snapshot = _zip(
        [("1_WELLNESS.fit", b"MON-1"), ("2_METRICS.fit", b"MON-2"), ("3_ACTIVITY.fit", b"SNAP-ACT")]
    )
    fake = FakeGarmin(
        [
            _activity(101, "2026-10-10 07:00:00", "Morning Run"),
            _activity(102, "2026-10-10 08:00:00", "Old Run"),
            _activity(301, "2026-10-11 09:00:00", "Whistler Alpine Skiing", type_key="other"),
            _activity(302, "2026-10-11 10:00:00", "Whistler Again", type_key="other"),
            _activity(303, "2026-10-11 11:00:00", "Yoga\x1b[2J", type_key="other"),
            _activity(203, "2026-10-12 06:00:00", "Typed In", manual=True),
            _activity(204, "2026-10-12 07:00:00", "Gone"),
            _activity(205, "2026-10-12 08:00:00", "Empty"),
        ],
        originals={204: garmin.NotFound("404"), 205: b""},
        snapshots={START: snapshot},
    )
    api = env(
        fake,
        Api(
            {
                b"MON-1": _non_activity("monitoring_b"),
                b"MON-2": _non_activity("44"),
                b"ACT-102": httpx.Response(409, json={"detail": "duplicate"}),
                b"ACT-301": _skipped_sport("alpine_skiing"),
                b"ACT-302": _skipped_sport("alpine_skiing"),
                b"ACT-303": _skipped_sport("yoga"),
            }
        ),
    )

    result = _sync()

    assert result.exit_code == 0, result.output
    assert _summary(result.output) == [
        "Stored: 2",
        "Already stored: 1",
        "Skipped alpine_skiing: 2 (activity 301 (Whistler Alpine Skiing), activity 302 (Whistler Again))",
        "Skipped yoga: 1 (activity 303 (Yoga?[2J))",
        "Not an activity: 2",
        "No FIT file: 3 (activity 203 (Typed In), activity 204 (Gone), activity 205 (Empty))",
    ]
    assert len(api.uploads) == 8


def test_outcomes_are_recorded_on_each_result(env) -> None:
    fake = FakeGarmin(
        [_activity(101, "2026-10-10 07:00:00", "Run"), _activity(102, "2026-10-10 08:00:00", "Dup")],
        snapshots={START: _zip([("1_WELLNESS.fit", b"MON-1")])},
    )
    env(fake, Api({b"MON-1": _non_activity("monitoring_b"), b"ACT-102": httpx.Response(409, json={})}))

    results = sync.fetch(fake, API_URL, START, TODAY)

    assert [(r.item, r.outcome) for r in results] == [
        ("snapshot 2026-10-10: 1_WELLNESS.fit", "non_activity"),
        ("activity 101 (Run): 101_ACTIVITY.fit", "stored"),
        ("activity 102 (Dup): 102_ACTIVITY.fit", "already_stored"),
    ]


def test_an_empty_range_prints_zero_counts_and_exits_0(env) -> None:
    env(FakeGarmin([]), Api())

    result = _sync()

    assert result.exit_code == 0, result.output
    assert _summary(result.output) == [
        "Stored: 0",
        "Already stored: 0",
        "Not an activity: 0",
        "No FIT file: 0",
    ]


def test_a_failed_item_prints_its_since_command_and_exits_1(env) -> None:
    fake = FakeGarmin(
        [
            _activity(101, "2026-10-10 07:00:00", "Run"),
            _activity(201, "2026-10-11 07:00:00", "Bad"),
            _activity(301, "2026-10-12 07:00:00", "Later"),
        ]
    )
    api = env(fake, Api({b"ACT-201": httpx.Response(400, json={"detail": "not a FIT file"})}))

    result = _sync()

    assert result.exit_code == 1, result.output
    summary = _summary(result.output)
    assert "Stored: 2" in summary
    failed = [line for line in summary if line.startswith("Failed")]
    assert failed == [
        (
            "Failed: 2026-10-11, activity 201 (Bad): 201_ACTIVITY.fit, API returned 400: not a FIT file, "
            "later runs do not return for it: it would fail the same way again, runcoach sync --since 2026-10-11"
        )
    ]
    # A failed item does not stop the sync.
    assert api.uploads == [b"ACT-101", b"ACT-201", b"ACT-301"]


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        (
            httpx.Response(413, text="too large"),
            # A 413 rejects the file itself (over a size or decode ceiling): permanent.
            "API returned 413: too large, later runs do not return for it: it would fail the same way again",
        ),
        (httpx.Response(500, json={"detail": ["x"]}), 'API returned 500: {"detail":["x"]}'),
        (
            httpx.Response(200, json={"skipped": True, "reason": "mystery", "filter_applied": True}),
            "API returned 200 with an unknown skip reason 'mystery'",
        ),
        (httpx.Response(201, text="not json"), None),
    ],
)
def test_any_other_reply_is_failed(env, reply: httpx.Response, detail: str | None) -> None:
    fake = FakeGarmin([_activity(101, "2026-10-10 07:00:00", "Run")])
    env(fake, Api({b"ACT-101": reply}))

    result = _sync()

    assert result.exit_code == 1, result.output
    if detail is None:
        # A 201 whose body cannot be read carries no filter_applied: the old-API stop.
        assert "the API does not support runs_and_captures_only: update it" in result.output
    else:
        failed = [line for line in _summary(result.output) if line.startswith("Failed")]
        assert failed == [
            f"Failed: 2026-10-10, activity 101 (Run): 101_ACTIVITY.fit, {detail}, runcoach sync --since 2026-10-10"
        ]


def test_no_fit_original_is_not_a_failure(env) -> None:
    fake = FakeGarmin(
        [
            _activity(203, "2026-10-10 06:00:00", "Typed In", manual=True),
            _activity(204, "2026-10-11 07:00:00", "Gone"),
            _activity(205, "2026-10-12 08:00:00", "Empty"),
        ],
        originals={204: garmin.NotFound("404"), 205: b""},
    )
    api = env(fake, Api())

    result = _sync()

    assert result.exit_code == 0, result.output
    summary = _summary(result.output)
    assert "No FIT file: 3 (activity 203 (Typed In), activity 204 (Gone), activity 205 (Empty))" in summary
    assert not any(line.startswith("Failed") for line in summary)
    assert api.uploads == []


def test_a_failure_with_no_day_names_the_range_start(env) -> None:
    """An unreadable summary has no day: its Failed line says so and recovers from the range start."""
    fake = FakeGarmin(
        [{"activityName": "No Id", "startTimeLocal": "2026-10-11 07:00:00", "activityType": {"typeId": 1}}]
    )
    env(fake, Api())

    result = _sync()

    assert result.exit_code == 1, result.output
    failed = [line for line in _summary(result.output) if line.startswith("Failed")]
    assert len(failed) == 1
    assert failed[0].startswith("Failed: day unknown, activity None (No Id), unreadable summary: ")
    assert failed[0].endswith(", runcoach sync --since 2026-10-10")


def test_hostile_names_and_details_are_printed_with_controls_replaced(env) -> None:
    fake = FakeGarmin([_activity(101, "2026-10-10 07:00:00", "Run\r\x07")])
    env(fake, Api({b"ACT-101": httpx.Response(400, json={"detail": "bad\x1b[31m"})}))

    result = _sync()

    failed = [line for line in _summary(result.output) if line.startswith("Failed")]
    assert failed == [
        (
            "Failed: 2026-10-10, activity 101 (Run??): 101_ACTIVITY.fit, API returned 400: bad?[31m, "
            "later runs do not return for it: it would fail the same way again, runcoach sync --since 2026-10-10"
        )
    ]
    assert "\x1b" not in result.output and "\x07" not in result.output
