"""``runcoach sync`` reads race tags after its uploads (F023 AC4 and AC6, the ``sync`` side).

After an upload pass that ends without a stop (failed items do not prevent it) and after
``sync-state.json`` is written, ``sync`` lists Connect activities for ``[min(start, today - 29
days), today]`` (reusing its own listing when that covers the window, else one more call) and sends
their race tags to ``PUT /race-tags``. ``/openapi.json`` is read once per run, shared with the
upload switch check. Every test drives the command through ``CliRunner`` with the shared fake
Garmin client and mock API (``fake_garmin``, ``fake_api``); a socket block keeps any stray
connection off the network. Today is 2026-10-12 in a UTC-7 computer zone.
"""

from __future__ import annotations

import json
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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
ZONE = timezone(timedelta(hours=-7))
WINDOW_START = TODAY - timedelta(days=29)
LOGIN_LINE = "Garmin login needed: run `runcoach sync login`"
OLD_API_TAGS = "the API does not support race tags: update it and restart the API"
FRESH_STATE = b'{"last_completed": null, "oldest_failed": null}'


def _activity(activity_id: int, day: date, *, race: bool = False, hour: int = 7) -> dict[str, Any]:
    """A listed running activity at ``hour`` local on ``day``, its event type Race or not."""
    local = datetime(day.year, day.month, day.day, hour, 0, 0)
    gmt = local.replace(tzinfo=ZONE).astimezone(timezone.utc)
    return {
        "activityId": activity_id,
        "activityName": f"run {activity_id}",
        "startTimeLocal": local.isoformat(sep=" "),
        "startTimeGMT": gmt.strftime("%Y-%m-%d %H:%M:%S"),
        "activityType": {"typeKey": "running", "typeId": 1, "parentTypeId": 17},
        "eventType": {"typeKey": "race" if race else "uncategorized", "typeId": 9},
        "distance": 5000.0,
        "duration": 1500.0,
        "manualActivity": False,
    }


def _row(session_id: str, start_time: str) -> dict[str, Any]:
    """A stored run as ``GET /sessions`` lists it."""
    return {"session_id": session_id, "start_time": start_time, "sport": "running", "activity_tag": None}


# The newest stored run is on 10-11 (local), so the upload range is 10-10 to 10-12.
RECENT_STORE = [[_row("r1", "2026-10-11T14:00:00+00:00")]]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"a test opened a network connection: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, install_mock_client):
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)

    def setup(fake: Any, api: Any) -> Any:
        install_mock_client(api)

        def open_client() -> Any:
            if isinstance(fake, Exception):
                raise fake
            return fake

        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=open_client))
        return api

    return setup


def _sync(*args: str):
    return runner.invoke(app, ["sync", *args])


def _flat(output: str) -> str:
    return " ".join(output.split())


def _counts(**overrides: int) -> str:
    counts = dict.fromkeys(
        ("tagged", "cleared", "unchanged", "not stored", "mismatch", "ambiguous", "other tag", "skipped"), 0
    )
    counts.update({key.replace("_", " "): value for key, value in overrides.items()})
    return "Race tags: " + ", ".join(f"{key} {value}" for key, value in counts.items())


def test_race_29_days_back_is_tagged(env, fake_garmin, fake_api) -> None:
    """The upload range is 10-10 to 10-12; one more listing covers 09-13 (today - 29) on, and the
    race stored on that day is sent and tagged. It is not downloaded: only the upload range is."""
    fake = fake_garmin([_activity(913, WINDOW_START, race=True), _activity(1011, date(2026, 10, 11))])
    api = env(fake, fake_api(pages=RECENT_STORE, tag_outcomes={913: ("tagged", "s-913", [])}))

    result = _sync()

    assert result.exit_code == 0, result.output
    assert fake.listed == [(date(2026, 10, 10), TODAY), (WINDOW_START, TODAY)]
    race = [item for item in api.sent if item["connect_activity_id"] == 913]
    assert race == [
        {
            "connect_activity_id": 913,
            "start_time": "2026-09-13T14:00:00Z",
            "distance_m": 5000.0,
            "duration_s": 1500.0,
            "race": True,
        }
    ]
    assert ("original", 913) not in fake.downloads
    assert _counts(tagged=1, unchanged=1) in result.output.splitlines()


def test_race_30_days_back_is_not_read(env, fake_garmin, fake_api) -> None:
    """A race 30 days back lies outside the window: never sent, so its tag is not read."""
    fake = fake_garmin(
        [_activity(912, WINDOW_START - timedelta(days=1), race=True), _activity(913, WINDOW_START)]
    )
    api = env(fake, fake_api(pages=RECENT_STORE, tag_outcomes={912: "tagged"}))

    result = _sync()

    assert result.exit_code == 0, result.output
    assert fake.listed == [(date(2026, 10, 10), TODAY), (WINDOW_START, TODAY)]
    assert [item["connect_activity_id"] for item in api.sent] == [913]
    assert _counts(unchanged=1) in result.output.splitlines()


def test_run_uploaded_in_this_sync_is_tagged_in_it(env, fake_garmin, fake_api) -> None:
    """Today's race is uploaded first, then its tag is sent and matched in the same run: the API
    answers ``tagged`` only for an activity whose file it already holds."""
    fake = fake_garmin([_activity(1012, TODAY, race=True)])
    api = fake_api(pages=RECENT_STORE)

    def match(item: dict[str, Any]) -> str:
        stored = f"ACT-{item['connect_activity_id']}".encode() in api.uploads
        return "tagged" if stored else "not_stored"

    api.tag_outcomes = {1012: match}
    env(fake, api)

    result = _sync()

    assert result.exit_code == 0, result.output
    assert api.uploads == [b"ACT-1012"]
    assert api.calls.index("POST /sessions") < api.calls.index("PUT /race-tags")
    lines = result.output.splitlines()
    assert lines.index("Stored: 1") < lines.index(_counts(tagged=1))


def test_listing_is_reused_when_it_covers_the_window(env, fake_garmin, fake_api) -> None:
    """``--since`` today - 29 makes the upload listing cover the whole window: no second call, and
    an activity on that first day is sent from it."""
    fake = fake_garmin([_activity(913, WINDOW_START, race=True), _activity(1012, TODAY)])
    api = env(fake, fake_api(tag_outcomes={913: "tagged"}))

    result = _sync("--since", WINDOW_START.isoformat())

    assert result.exit_code == 0, result.output
    assert fake.listed == [(WINDOW_START, TODAY)]
    assert [item["connect_activity_id"] for item in api.sent] == [913, 1012]
    assert _counts(tagged=1, unchanged=1) in result.output.splitlines()


def test_one_extra_listing_when_start_is_recent(env, fake_garmin, fake_api) -> None:
    """A start one day after today - 29 does not cover the window: F022's call keeps its range and
    exactly one more call covers today - 29 to today."""
    since = WINDOW_START + timedelta(days=1)
    fake = fake_garmin([_activity(913, WINDOW_START)])
    api = env(fake, fake_api())

    result = _sync("--since", since.isoformat())

    assert result.exit_code == 0, result.output
    assert fake.listed == [(since, TODAY), (WINDOW_START, TODAY)]
    assert [item["connect_activity_id"] for item in api.sent] == [913]


def test_tags_step_skipped_after_a_stop(env, fake_garmin, fake_api) -> None:
    """A rate limit stops the uploads: no tag is read, nothing is sent, no counts line."""
    fake = fake_garmin(
        [_activity(1011, date(2026, 10, 11), race=True)],
        originals={1011: garmin.RateLimited("429 Too Many Requests")},
    )
    api = env(fake, fake_api(pages=RECENT_STORE))

    result = _sync()

    assert result.exit_code == 1, result.output
    assert fake.listed == [(date(2026, 10, 10), TODAY)]
    assert "PUT /race-tags" not in api.calls
    assert "Race tags" not in result.output
    assert _flat(result.output).endswith("wait, then run `runcoach sync --since 2026-10-11`")


def test_tags_step_runs_after_failed_items(env, fake_garmin, fake_api) -> None:
    """A failed download is reported and exits 1, but the tags step still runs after it."""
    fake = fake_garmin(
        [_activity(1011, date(2026, 10, 11), race=True)],
        originals={1011: garmin.GarminError("500 Server Error")},
    )
    api = env(fake, fake_api(pages=RECENT_STORE, tag_outcomes={1011: "tagged"}))

    result = _sync()

    assert result.exit_code == 1, result.output
    lines = result.output.splitlines()
    failed = [line for line in lines if line.startswith("Failed: 2026-10-11")]
    assert failed, result.output
    assert lines.index(failed[0]) < lines.index(_counts(tagged=1))
    assert [item["connect_activity_id"] for item in api.sent] == [1011]


def _state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def test_tags_failure_leaves_state_file_as_uploads_left_it(
    env, fake_garmin, fake_api, fresh_sync_state: Path
) -> None:
    """Each tags failure comes after the upload summary, exits 1 and leaves ``sync-state.json`` as
    the uploads wrote it: the run reached its end, so ``last_completed`` is today."""
    uploads_left = {"last_completed": TODAY.isoformat(), "oldest_failed": None}
    cases = [
        (
            "login on the extra listing",
            dict(list_error=garmin.LoginNeeded("token expired"), list_error_on=2),
            {},
            [LOGIN_LINE],
        ),
        (
            "Garmin unreachable on the extra listing",
            dict(list_error=garmin.GarminUnreachable("connection reset"), list_error_on=2),
            {},
            [
                "could not reach Garmin Connect (connection reset)",
                f"Race tags not read: run runcoach sync tags --since {WINDOW_START.isoformat()}",
            ],
        ),
        ("an old API", {}, {"doc": _switch_only_doc()}, [OLD_API_TAGS]),
        (
            "an API error on the batch",
            {},
            {"fail_tags": 500},
            [f"Race tags stopped at {WINDOW_START.isoformat()}: run runcoach sync tags --since {WINDOW_START.isoformat()}"],
        ),
        (
            "an unreachable API on the batch",
            {},
            {"fail_tags": "unreachable"},
            [f"Race tags stopped at {WINDOW_START.isoformat()}: run runcoach sync tags --since {WINDOW_START.isoformat()}"],
        ),
    ]
    for name, garmin_kwargs, api_kwargs, ending in cases:
        fresh_sync_state.write_bytes(FRESH_STATE)
        fake = fake_garmin([_activity(913, WINDOW_START, race=True)], **garmin_kwargs)
        api = env(fake, fake_api(pages=RECENT_STORE, **api_kwargs))

        result = _sync()

        assert result.exit_code == 1, (name, result.output)
        assert _state(fresh_sync_state) == uploads_left, name
        lines = result.output.splitlines()
        assert "Stored: 0" in lines, (name, result.output)
        tail = _flat("\n".join(ending))
        assert _flat(result.output).endswith(tail), (name, result.output)
        assert _flat(result.output).count("runcoach sync tags --since") <= 1, (name, result.output)
        assert "runcoach sync --since" not in result.output, (name, result.output)
        if name == "an old API":
            assert api.tag_batches == [], name


@pytest.mark.parametrize("failure", [500, "unreachable"])
def test_batch_stop_keeps_earlier_batches_through_sync(
    env, fake_garmin, fake_api, fresh_sync_state: Path, monkeypatch: pytest.MonkeyPatch, failure: int | str
) -> None:
    """Batches of one: the first batch is answered and its count printed, the second fails (an API
    error or an unreachable API), so the stop names the second batch's first day and no later batch
    is sent. The counts line sits between the upload summary and the stop line; the state file is
    as the uploads left it."""
    monkeypatch.setattr(sync, "RACE_TAGS_BATCH", 1)
    second_day = WINDOW_START + timedelta(days=1)
    fake = fake_garmin([_activity(913, WINDOW_START, race=True), _activity(914, second_day, race=True)])
    api = env(
        fake,
        fake_api(pages=RECENT_STORE, tag_outcomes={913: "tagged"}, fail_tags=failure, fail_tags_on=2),
    )

    result = _sync()

    assert result.exit_code == 1, result.output
    assert api.calls.count("PUT /race-tags") == 2
    sent_ids = [[item["connect_activity_id"] for item in batch] for batch in api.tag_batches]
    assert sent_ids == ([[913], [914]] if failure == 500 else [[913]])
    lines = result.output.splitlines()
    assert lines[-1] == f"Race tags stopped at {second_day.isoformat()}: run runcoach sync tags --since {second_day.isoformat()}"
    assert lines.index("Stored: 0") < lines.index(_counts(tagged=1)) < len(lines) - 1
    assert _state(fresh_sync_state) == {"last_completed": TODAY.isoformat(), "oldest_failed": None}


def _switch_only_doc() -> dict[str, Any]:
    """``/openapi.json`` of an API that has the upload switch but not ``setRaceTags``."""
    return {
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


def test_openapi_read_once_per_run(env, fake_garmin, fake_api) -> None:
    """With uploads, the switch check's read is the tags step's too; with nothing to upload, the
    tags step reads it, once."""
    with_upload = fake_garmin([_activity(1011, date(2026, 10, 11))])
    api = env(with_upload, fake_api(pages=RECENT_STORE))
    result = _sync()
    assert result.exit_code == 0, result.output
    assert api.calls.count("GET /openapi.json") == 1
    assert api.calls.index("GET /openapi.json") < api.calls.index("POST /sessions")
    assert "PUT /race-tags" in api.calls

    nothing = fake_garmin([_activity(913, WINDOW_START)])
    api = env(nothing, fake_api(pages=RECENT_STORE))
    result = _sync()
    assert result.exit_code == 0, result.output
    assert "POST /sessions" not in api.calls
    assert api.calls.count("GET /openapi.json") == 1
    assert api.calls.index("GET /openapi.json") < api.calls.index("PUT /race-tags")


def _openapi_down(fake_api: Any, status: int | None, **kwargs: Any) -> Any:
    """A ``fake_api`` whose ``GET /openapi.json`` is a connect error (``status`` None) or answers
    its document with ``status``."""

    class OpenApiDown(fake_api):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if status is None and request.method == "GET" and request.url.path == "/openapi.json":
                self.requests.append(request)
                raise httpx.ConnectError("connection refused", request=request)
            return super().__call__(request)

    return OpenApiDown(doc_status=status or 200, **kwargs)


@pytest.mark.parametrize("since", [None, "2026-08-01"])
def test_api_not_running_at_the_tags_openapi_read_names_the_tags_start(
    env, fake_garmin, fake_api, fresh_sync_state: Path, since: str | None
) -> None:
    """Nothing to upload, so the tags step makes the run's one ``/openapi.json`` read, and it is a
    connect error: one ``sync tags`` recovery command naming the tags start (today - 29, or a
    ``--since`` earlier than it), nothing sent, the state file as the uploads left it."""
    fake = fake_garmin([] if since else [_activity(913, WINDOW_START, race=True)])
    api = env(fake, _openapi_down(fake_api, None, pages=RECENT_STORE))

    result = _sync(*(("--since", since) if since else ()))

    tags_start = since or WINDOW_START.isoformat()
    assert result.exit_code == 1, result.output
    assert "POST /sessions" not in api.calls
    assert api.calls.count("GET /openapi.json") == 1
    assert "PUT /race-tags" not in api.calls
    lines = result.output.splitlines()
    assert lines[-1] == f"Race tags not read: run runcoach sync tags --since {tags_start}", lines
    assert any("could not reach API at http://example.test" in line for line in lines), lines
    assert "Stored: 0" in lines
    assert _flat(result.output).count("runcoach sync tags --since") == 1
    assert "runcoach sync --since" not in result.output
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)
    assert _state(fresh_sync_state) == {"last_completed": TODAY.isoformat(), "oldest_failed": None}


def test_a_503_tags_openapi_document_is_not_read(env, fake_garmin, fake_api, fresh_sync_state: Path) -> None:
    """Nothing to upload and a 503 ``/openapi.json``: not read, even though its body lists
    ``setRaceTags`` (as for the switch check), so the old-API line; nothing sent."""
    fake = fake_garmin([_activity(913, WINDOW_START, race=True)])
    api = env(fake, _openapi_down(fake_api, 503, pages=RECENT_STORE, doc=_switch_and_tags_doc()))

    result = _sync()

    assert result.exit_code == 1, result.output
    assert "POST /sessions" not in api.calls
    assert api.calls.count("GET /openapi.json") == 1
    assert "PUT /race-tags" not in api.calls
    assert result.output.splitlines()[-1] == f"Error: {OLD_API_TAGS}"
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)
    assert _state(fresh_sync_state) == {"last_completed": TODAY.isoformat(), "oldest_failed": None}


def _switch_and_tags_doc() -> dict[str, Any]:
    doc = _switch_only_doc()
    doc["paths"]["/race-tags"] = {"put": {"operationId": "setRaceTags"}}
    return doc


def test_old_api_without_set_race_tags_exits_1_after_upload_summary(env, fake_garmin, fake_api) -> None:
    """An API with the upload switch but no ``setRaceTags``: the uploads run, their summary is
    printed, then the old-API line; nothing is sent and the document is read once."""
    fake = fake_garmin([_activity(1011, date(2026, 10, 11), race=True)])
    api = env(fake, fake_api(pages=RECENT_STORE, doc=_switch_only_doc()))

    result = _sync()

    assert result.exit_code == 1, result.output
    assert api.uploads == [b"ACT-1011"]
    assert api.tag_batches == []
    assert api.calls.count("GET /openapi.json") == 1
    lines = result.output.splitlines()
    assert "Stored: 1" in lines
    assert "Race tags" not in result.output
    assert lines[-1] == f"Error: {OLD_API_TAGS}"
    assert lines.index("Stored: 1") < len(lines) - 1
