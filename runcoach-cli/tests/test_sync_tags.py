"""``runcoach sync tags``: list Connect activities and send their race tags, downloading nothing (F023).

Every test drives the command through ``CliRunner`` with this module's own fake Garmin client
(``TagsGarmin``: it serves a listing, and its download methods fail the test if called) and a mock
API (``TagsApi``: ``/openapi.json`` and ``PUT /race-tags``, every other request fails the test).
No test imports the Garmin library; the socket block below keeps any stray connection off the
network all the same. ``sync-state.json`` is absent before and after every run.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
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
DEFAULT_SINCE = date(2026, 9, 13)
ZONE = timezone(timedelta(hours=-7))
LOGIN_LINE = "Garmin login needed: run `runcoach sync login`"
OLD_API_LINE = "the API does not support race tags: update it and restart the API"


def _summary(
    activity_id: int,
    local: str,
    *,
    event: Any = "uncategorized",
    type_key: str = "running",
    type_id: int = 1,
    distance: Any = 5000.0,
    duration: Any = 1500.0,
    manual: bool = False,
    gmt: Any = None,
) -> dict[str, Any]:
    """A listed summary as ``get_activities_by_date`` returns it; ``event`` is the typeKey, or a
    whole ``eventType`` value when not a string (``...`` drops the key)."""
    when = datetime.fromisoformat(local).replace(tzinfo=ZONE)
    summary: dict[str, Any] = {
        "activityId": activity_id,
        "activityName": f"run {activity_id}",
        "startTimeLocal": local,
        "startTimeGMT": gmt if gmt is not None else when.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "activityType": {"typeKey": type_key, "typeId": type_id, "parentTypeId": 17},
        "distance": distance,
        "duration": duration,
        "manualActivity": manual,
    }
    if event is not ...:
        summary["eventType"] = {"typeKey": event, "typeId": 9} if isinstance(event, str) else event
    return summary


class TagsGarmin:
    """Serves ``activities`` from ``list_activities``; any download fails the test."""

    def __init__(self, activities: Any = None, list_error: Exception | None = None) -> None:
        self.activities = [] if activities is None else activities
        self.list_error = list_error
        self.listed: list[tuple[date, date]] = []
        self.downloads: list[Any] = []

    def list_activities(self, start: date, end: date) -> Any:
        self.listed.append((start, end))
        if self.list_error is not None:
            raise self.list_error
        return list(self.activities) if isinstance(self.activities, list) else self.activities

    def download_original(self, activity_id: int) -> bytes:
        self.downloads.append(("original", activity_id))
        raise AssertionError(f"sync tags downloaded activity {activity_id}")

    def download_snapshot(self, day: date) -> bytes:
        self.downloads.append(("snapshot", day))
        raise AssertionError(f"sync tags downloaded the snapshot of {day}")


def openapi_doc(with_race_tags: bool = True) -> dict[str, Any]:
    paths: dict[str, Any] = {"/sessions": {"get": {"operationId": "list_sessions_sessions_get"}}}
    if with_race_tags:
        paths["/race-tags"] = {"put": {"operationId": "setRaceTags"}}
    return {"paths": paths}


class TagsApi:
    """``/openapi.json`` and ``PUT /race-tags``; ``outcomes`` maps an activity id to its outcome
    (default ``unchanged``), or to ``(outcome, session_id, candidates)``. ``fail_batch`` maps a
    1-based batch number to a status to answer with, or to ``"unreachable"``."""

    def __init__(
        self,
        outcomes: dict[int, Any] | None = None,
        doc: dict[str, Any] | None = None,
        fail_batch: dict[int, Any] | None = None,
    ) -> None:
        self.outcomes = outcomes or {}
        self.doc = openapi_doc() if doc is None else doc
        self.fail_batch = fail_batch or {}
        self.batches: list[list[dict[str, Any]]] = []
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        if request.method == "GET" and request.url.path == "/openapi.json":
            return httpx.Response(200, json=self.doc)
        if request.method == "PUT" and request.url.path == "/race-tags":
            number = len(self.batches) + 1
            failure = self.fail_batch.get(number)
            if failure == "unreachable":
                raise httpx.ConnectError("connection refused", request=request)
            items = json.loads(request.read())["items"]
            self.batches.append(items)
            if failure is not None:
                return httpx.Response(failure, json={"detail": "the store is busy"})
            replies = []
            for item in items:
                outcome = self.outcomes.get(item["connect_activity_id"], "unchanged")
                if isinstance(outcome, str):
                    outcome = (outcome, None, [])
                name, session_id, candidates = outcome
                replies.append(
                    {
                        "connect_activity_id": item["connect_activity_id"],
                        "outcome": name,
                        "session_id": session_id,
                        "candidates": candidates,
                    }
                )
            counts: dict[str, int] = {}
            for reply in replies:
                counts[reply["outcome"]] = counts.get(reply["outcome"], 0) + 1
            return httpx.Response(200, json={"items": replies, "counts": counts})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [item for batch in self.batches for item in batch]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"a test opened a network connection: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, install_mock_client, prewritten_config):
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", ZONE)
    state_file = prewritten_config.parent / "sync-state.json"

    def setup(fake: TagsGarmin | Exception, api: TagsApi | None = None) -> TagsApi:
        api = api or TagsApi()
        install_mock_client(api)

        def open_client() -> TagsGarmin:
            if isinstance(fake, Exception):
                raise fake
            return fake

        monkeypatch.setattr(sync, "_adapter", lambda: SimpleNamespace(open_client=open_client))
        return api

    setup.state_file = state_file  # type: ignore[attr-defined]
    return setup


def _tags(*args: str):
    return runner.invoke(app, ["sync", "tags", *args])


def _lines(output: str) -> list[str]:
    return output.rstrip().splitlines()


def _counts(**overrides: int) -> str:
    counts = dict.fromkeys(
        ("tagged", "cleared", "unchanged", "not stored", "mismatch", "ambiguous", "other tag", "skipped"), 0
    )
    counts.update({key.replace("_", " "): value for key, value in overrides.items()})
    return "Race tags: " + ", ".join(f"{key} {value}" for key, value in counts.items())


def test_sync_tags_tags_a_listed_race_and_prints_the_summary(env) -> None:
    fake = TagsGarmin(
        [
            _summary(201, "2026-10-11 07:00:00"),
            _summary(101, "2026-10-10 07:00:00", event="race", distance=10000.5, duration=2400),
        ]
    )
    api = env(fake, TagsApi(outcomes={101: ("tagged", "s1", ["s1"])}))

    result = _tags()

    assert result.exit_code == 0, result.output
    assert _lines(result.output) == [
        "From 2026-09-13 to 2026-10-12",
        _counts(tagged=1, unchanged=1),
    ]
    assert api.batches == [
        [
            {
                "connect_activity_id": 101,
                "start_time": "2026-10-10T14:00:00Z",
                "distance_m": 10000.5,
                "duration_s": 2400,
                "race": True,
            },
            {
                "connect_activity_id": 201,
                "start_time": "2026-10-11T14:00:00Z",
                "distance_m": 5000.0,
                "duration_s": 1500.0,
                "race": False,
            },
        ]
    ]
    assert fake.listed == [(DEFAULT_SINCE, TODAY)]
    assert fake.downloads == []


def test_default_since_is_29_days_back(env) -> None:
    fake = TagsGarmin(
        [
            _summary(1, "2026-09-12 23:59:59"),  # the day before the default start
            _summary(2, "2026-09-13 00:00:00"),
            _summary(3, "2026-10-12 23:00:00"),
            _summary(4, "2026-10-13 06:00:00"),  # after today (the fakes do not filter)
        ]
    )
    api = env(fake)

    result = _tags()

    assert result.exit_code == 0, result.output
    assert _lines(result.output)[0] == "From 2026-09-13 to 2026-10-12"
    assert TODAY - DEFAULT_SINCE == timedelta(days=29)
    assert fake.listed == [(DEFAULT_SINCE, TODAY)]
    assert [item["connect_activity_id"] for item in api.sent] == [2, 3]

    fake = TagsGarmin([_summary(1, "2026-09-12 23:59:59")])
    api = env(fake)
    result = _tags("--since", "2026-09-12")
    assert result.exit_code == 0, result.output
    assert _lines(result.output)[0] == "From 2026-09-12 to 2026-10-12"
    assert fake.listed == [(date(2026, 9, 12), TODAY)]
    assert [item["connect_activity_id"] for item in api.sent] == [1]


def test_since_after_today_is_a_usage_error(env) -> None:
    for value in ("2026-10-13", "2027-01-01"):
        fake = TagsGarmin([_summary(1, "2026-10-10 07:00:00")])
        api = env(fake)

        result = _tags("--since", value)

        assert result.exit_code == 2, (value, result.output)
        assert "after today" in result.output
        assert fake.listed == []
        assert api.requests == []


def test_parent_since_with_tags_is_a_usage_error(env) -> None:
    fake = TagsGarmin([_summary(1, "2026-10-10 07:00:00")])
    api = env(fake)

    result = runner.invoke(app, ["sync", "--since", "2026-10-01", "tags"])

    assert result.exit_code == 2, result.output
    assert "sync tags --since" in " ".join(result.output.split())
    assert fake.listed == []
    assert api.requests == []

    # The rule is the tags command's alone: logout with the parent --since still runs.
    result = runner.invoke(app, ["sync", "--since", "2026-10-01", "logout"])
    assert result.exit_code == 0, result.output
    assert "nothing to delete" in result.output


def test_downloads_nothing_and_never_touches_the_state_file(env, monkeypatch) -> None:
    def no_state(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("sync tags touched the sync state")

    monkeypatch.setattr(sync, "read_state", no_state)
    monkeypatch.setattr(sync, "write_state", no_state)
    fake = TagsGarmin(
        [
            _summary(101, "2026-10-10 07:00:00", event="race"),
            _summary(102, "2026-10-10 09:00:00", type_key="other", type_id=4),
            _summary(103, "2026-10-11 07:00:00", manual=True),
        ]
    )
    api = env(fake)
    assert not env.state_file.exists()

    result = _tags("--since", "2026-10-01")

    assert result.exit_code == 0, result.output
    assert fake.downloads == []
    assert not env.state_file.exists()
    # The other-typed activity is sent like a run; the manual one is not sent.
    assert [item["connect_activity_id"] for item in api.sent] == [101, 102]
    assert all(not path.startswith("POST") for path in api.requests)
    assert "GET /sessions" not in api.requests


def test_missing_or_null_event_type_is_skipped_never_sent(env) -> None:
    fake = TagsGarmin(
        [
            _summary(1, "2026-10-10 07:00:00", event=...),
            _summary(2, "2026-10-10 08:00:00", event=None),
            _summary(3, "2026-10-10 09:00:00", event={"typeId": 9}),
            _summary(4, "2026-10-10 10:00:00", event="race"),
        ]
    )
    api = env(fake)

    result = _tags()

    assert result.exit_code == 0, result.output
    assert [item["connect_activity_id"] for item in api.sent] == [4]
    lines = _lines(result.output)
    assert lines[1] == _counts(unchanged=1, skipped=3)
    skipped = [line for line in lines if line.startswith("Skipped: ")]
    assert len(skipped) == 3
    for activity_id, local in ((1, "07"), (2, "08"), (3, "09")):
        assert any(
            line.startswith(
                f"Skipped: activity {activity_id} (run {activity_id}), 2026-10-10 {local}:00:00, "
            )
            and "eventType" in line
            for line in skipped
        ), lines


def test_unparseable_start_is_skipped(env) -> None:
    no_type = _summary(6, "2026-10-10 12:00:00")
    del no_type["activityType"]
    no_gmt = _summary(2, "2026-10-10 08:00:00", event="race")
    del no_gmt["startTimeGMT"]
    fake = TagsGarmin(
        [
            _summary(1, "2026-10-10 07:00:00", event="race", gmt="not a time"),
            no_gmt,
            _summary(3, "2026-10-10 09:00:00", event="race", gmt="2026-10-10"),
            _summary(4, "2026-10-10 10:00:00", event="race", gmt="2026-10-10 17:00:00.250"),
            _summary(5, "2026-10-10 11:00:00", event="race", gmt=20261010),
            no_type,
            _summary(7, "2026-10-10 13:00:00", event="race"),
        ]
    )
    api = env(fake)

    result = _tags()

    assert result.exit_code == 0, result.output
    assert [item["connect_activity_id"] for item in api.sent] == [7]
    lines = _lines(result.output)
    assert lines[1] == _counts(unchanged=1, skipped=6)
    skipped = [line for line in lines if line.startswith("Skipped: ")]
    for activity_id in (1, 2, 3, 4, 5):
        assert any(
            line.startswith(f"Skipped: activity {activity_id} (run {activity_id}), 2026-10-10 ")
            and "startTimeGMT" in line
            for line in skipped
        ), lines
    assert any(
        line.startswith("Skipped: activity 6 (run 6), ") and "activityType" in line for line in skipped
    )


def test_a_number_the_api_would_refuse_is_sent_as_null(env) -> None:
    """A negative distance or duration would 422 the whole batch (``RaceTagItem`` is ``ge=0``) and
    every later one; it is sent as null, which never agrees, as are null, text and a bool."""
    fake = TagsGarmin(
        [
            _summary(1, "2026-10-10 07:00:00", event="race", distance=-1.0),
            _summary(2, "2026-10-10 08:00:00", event="race", duration=-5),
            _summary(3, "2026-10-10 09:00:00", distance=None, duration=None),
            _summary(4, "2026-10-10 10:00:00", distance="5000", duration="1500"),
            _summary(5, "2026-10-10 11:00:00", distance=True, duration=False),
            _summary(6, "2026-10-10 12:00:00", distance=0, duration=0.0),
        ]
    )
    api = env(fake)

    result = _tags()

    assert result.exit_code == 0, result.output
    assert [(item["distance_m"], item["duration_s"]) for item in api.sent] == [
        (None, 1500.0),
        (5000.0, None),
        (None, None),
        (None, None),
        (None, None),
        (0, 0.0),
    ]
    assert _lines(result.output)[1] == _counts(unchanged=6)


def test_duplicate_activity_ids_are_sent_once(env) -> None:
    race = _summary(101, "2026-10-10 07:00:00", event="race")
    fake = TagsGarmin([race, _summary(201, "2026-10-11 07:00:00"), dict(race), dict(race)])
    api = env(fake)

    result = _tags()

    assert result.exit_code == 0, result.output
    assert [item["connect_activity_id"] for item in api.sent] == [101, 201]
    assert _lines(result.output)[1] == _counts(unchanged=2)


def test_batches_of_500_oldest_first(env) -> None:
    base = datetime(2026, 9, 13, 0, 0, 0, tzinfo=ZONE)
    activities = [
        _summary(10_000 + n, (base + timedelta(minutes=40 * n)).strftime("%Y-%m-%d %H:%M:%S"))
        for n in range(1001)
    ]
    assert (base + timedelta(minutes=40 * 1000)).date() <= TODAY
    fake = TagsGarmin(list(reversed(activities)))
    api = env(fake)

    result = _tags()

    assert result.exit_code == 0, result.output
    assert [len(batch) for batch in api.batches] == [500, 500, 1]
    assert [item["connect_activity_id"] for item in api.sent] == [10_000 + n for n in range(1001)]
    starts = [item["start_time"] for item in api.sent]
    assert starts == sorted(starts)
    assert _lines(result.output)[1] == _counts(unchanged=1001)


def _five_days() -> list[dict[str, Any]]:
    return [_summary(100 + day, f"2026-10-{day:02d} 07:00:00", event="race") for day in range(1, 6)]


def test_failed_batch_stops_later_batches_and_names_since(env, monkeypatch) -> None:
    monkeypatch.setattr(sync, "RACE_TAGS_BATCH", 2)
    fake = TagsGarmin(list(reversed(_five_days())))
    api = env(fake, TagsApi(outcomes={101: "tagged", 102: "tagged"}, fail_batch={2: 500}))

    result = _tags()

    assert result.exit_code == 1, result.output
    lines = _lines(result.output)
    assert lines[-1] == "Race tags stopped at 2026-10-03: run runcoach sync tags --since 2026-10-03"
    assert any("API returned 500" in line and "the store is busy" in line for line in lines), lines
    # The first batch stands and is counted; the third is never sent.
    assert _counts(tagged=2) in lines
    assert [len(batch) for batch in api.batches] == [2, 2]
    assert result.output.count("runcoach sync tags --since") == 1


def test_unreachable_api_on_a_batch_stops_and_names_since(env, monkeypatch) -> None:
    monkeypatch.setattr(sync, "RACE_TAGS_BATCH", 2)
    fake = TagsGarmin(_five_days())
    api = env(fake, TagsApi(outcomes={101: "tagged"}, fail_batch={2: "unreachable"}))

    result = _tags()

    assert result.exit_code == 1, result.output
    lines = _lines(result.output)
    assert lines[-1] == "Race tags stopped at 2026-10-03: run runcoach sync tags --since 2026-10-03"
    assert any("could not reach API at http://example.test" in line for line in lines), lines
    assert _counts(tagged=1, unchanged=1) in lines
    assert len(api.batches) == 1
    assert result.output.count("runcoach sync tags --since") == 1


def test_oldest_first_is_start_order_not_activity_id_order(env, monkeypatch) -> None:
    """Activity ids fall as start times rise, so an id sort sends 400 and 300 first and names
    10-02 on the stop; start order sends 500 and 400 first and stops at the second batch's 10-03,
    with no unsent item dated before it."""
    monkeypatch.setattr(sync, "RACE_TAGS_BATCH", 2)
    fake = TagsGarmin(
        [
            _summary(300, "2026-10-03 07:00:00", event="race"),
            _summary(500, "2026-10-01 07:00:00", event="race"),
            _summary(200, "2026-10-04 07:00:00", event="race"),
            _summary(400, "2026-10-02 07:00:00", event="race"),
        ]
    )
    api = env(fake, TagsApi(fail_batch={2: 500}))

    result = _tags()

    assert result.exit_code == 1, result.output
    sent_ids = [[item["connect_activity_id"] for item in batch] for batch in api.batches]
    assert sent_ids == [[500, 400], [300, 200]]
    lines = _lines(result.output)
    assert lines[-1] == "Race tags stopped at 2026-10-03: run runcoach sync tags --since 2026-10-03"
    unsent = [item["start_time"] for item in api.batches[1]]
    assert min(unsent) == "2026-10-03T14:00:00Z", unsent
    assert _counts(unchanged=2) in lines


class _OpenApiDown(TagsApi):
    """``TagsApi`` whose ``GET /openapi.json`` is a connect error (``status`` None) or answers
    its document with ``status``."""

    def __init__(self, status: int | None = None) -> None:
        super().__init__()
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/openapi.json":
            self.requests.append(f"{request.method} {request.url.path}")
            if self.status is None:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(self.status, json=self.doc)
        return super().__call__(request)


@pytest.mark.parametrize("since", [None, "2026-08-01"])
def test_api_not_running_at_the_openapi_read_names_the_tags_start(env, since: str | None) -> None:
    """A connect error on ``GET /openapi.json``: one ``sync tags`` recovery command naming the
    tags start (the default, or a ``--since`` earlier than today - 29), nothing listed or sent."""
    fake = TagsGarmin([_summary(101, "2026-10-10 07:00:00", event="race")])
    api = env(fake, _OpenApiDown())

    result = _tags(*(("--since", since) if since else ()))

    start = since or DEFAULT_SINCE.isoformat()
    assert result.exit_code == 1, result.output
    lines = _lines(result.output)
    assert lines[-1] == f"Race tags not read: run runcoach sync tags --since {start}", lines
    assert any("could not reach API at http://example.test" in line for line in lines), lines
    assert result.output.count("runcoach sync tags --since") == 1
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)
    assert "PUT /race-tags" not in api.requests
    assert fake.listed == []
    assert not env.state_file.exists()


def test_a_503_openapi_document_is_not_read_and_sends_nothing(env) -> None:
    """A non-200 ``/openapi.json`` is not read, even when its body lists ``setRaceTags``, as for
    the upload switch check: the API is taken not to list it (F023 AC6)."""
    fake = TagsGarmin([_summary(101, "2026-10-10 07:00:00", event="race")])
    api = env(fake, _OpenApiDown(status=503))

    result = _tags()

    assert result.exit_code == 1, result.output
    assert _lines(result.output)[-1].endswith(OLD_API_LINE), result.output
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)
    assert "PUT /race-tags" not in api.requests
    assert api.requests.count("GET /openapi.json") == 1
    assert not env.state_file.exists()


def test_api_without_set_race_tags_sends_nothing(env) -> None:
    fake = TagsGarmin([_summary(101, "2026-10-10 07:00:00", event="race")])
    api = env(fake, TagsApi(doc=openapi_doc(with_race_tags=False)))

    result = _tags()

    assert result.exit_code == 1, result.output
    assert _lines(result.output)[-1].endswith(OLD_API_LINE), result.output
    assert api.batches == []
    assert "PUT /race-tags" not in api.requests
    assert api.requests.count("GET /openapi.json") == 1


GARMIN_STOPS: list[tuple[Callable[[], Any], str]] = [
    (
        lambda: garmin.GarminUnreachable("connection refused"),
        "could not reach Garmin Connect (connection refused)",
    ),
    (
        lambda: TagsGarmin(list_error=garmin.GarminUnreachable("timed out")),
        "could not reach Garmin Connect (timed out)",
    ),
    (lambda: TagsGarmin(list_error=garmin.RateLimited("429")), "Garmin is limiting requests (429)"),
    (
        lambda: TagsGarmin(list_error=garmin.GarminError("502 Bad Gateway", status=502)),
        "Garmin: 502 Bad Gateway",
    ),
    (lambda: TagsGarmin({"not": "a list"}), "Garmin's activity list could not be understood"),
]


def test_garmin_stop_prints_one_recovery_command(env) -> None:
    """Opening the client unreachable, the listing unreachable, rate limited, failing or not a list."""
    for make, reason in GARMIN_STOPS:
        api = env(make())

        result = _tags()

        assert result.exit_code == 1, (reason, result.output)
        lines = _lines(result.output)
        assert lines[-1] == "Race tags not read: run runcoach sync tags --since 2026-09-13"
        assert any(reason in line for line in lines), lines
        assert result.output.count("runcoach sync tags --since") == 1
        assert "runcoach sync --since" not in result.output
        assert "wait, then run" not in result.output
        assert api.batches == []


def test_ambiguous_and_mismatch_lines_keep_exit_zero(env) -> None:
    fake = TagsGarmin(
        [
            _summary(101, "2026-10-01 07:00:00", event="race"),
            _summary(201, "2026-10-02 07:00:00", event="race"),
            _summary(301, "2026-10-03 07:00:00", event="race"),
            _summary(401, "2026-10-04 07:00:00"),
            _summary(501, "2026-10-05 07:00:00"),
            _summary(601, "2026-10-06 07:00:00", event="race"),
        ]
    )
    api = env(
        fake,
        TagsApi(
            outcomes={
                101: ("ambiguous", None, ["sA", "sB"]),
                201: ("mismatch", "sC", ["sC"]),
                301: "not_stored",
                401: "not_stored",
                501: ("mismatch", "sD", ["sD"]),
                601: ("other_tag", "sE", ["sE"]),
            }
        ),
    )

    result = _tags()

    assert result.exit_code == 0, result.output
    assert _lines(result.output) == [
        "From 2026-09-13 to 2026-10-12",
        _counts(not_stored=2, mismatch=2, ambiguous=1, other_tag=1),
        "Race ambiguous: activity 101 (run 101), 2026-10-01 07:00:00, sessions sA, sB",
        "Race mismatch: activity 201 (run 201), 2026-10-02 07:00:00, session sC",
        "Race not stored: activity 301 (run 301), 2026-10-03 07:00:00",
    ]
    assert len(api.sent) == 6


def test_zero_items_sends_no_put(env) -> None:
    fake = TagsGarmin(
        [
            _summary(1, "2026-10-10 07:00:00", type_key="cycling", type_id=2),
            _summary(2, "2026-10-10 08:00:00", manual=True, event="race"),
        ]
    )
    api = env(fake)

    result = _tags()

    assert result.exit_code == 0, result.output
    assert _lines(result.output) == ["From 2026-09-13 to 2026-10-12", _counts()]
    assert "PUT /race-tags" not in api.requests

    fake = TagsGarmin([_summary(3, "2026-10-10 07:00:00", event=None)])
    api = env(fake)
    result = _tags()
    assert result.exit_code == 0, result.output
    assert _lines(result.output)[1] == _counts(skipped=1)
    assert "PUT /race-tags" not in api.requests


def test_no_or_refused_token_prints_the_login_message(env) -> None:
    """No saved token when the client opens, and a token Garmin refuses on the listing."""
    for fake in (
        garmin.LoginNeeded("no saved token"),
        TagsGarmin(list_error=garmin.LoginNeeded("403 Forbidden")),
    ):
        api = env(fake)

        result = _tags()

        assert result.exit_code == 1, result.output
        assert _lines(result.output)[-1] == f"Error: {LOGIN_LINE}"
        assert "Race tags not read" not in result.output
        assert api.batches == []
