"""``runcoach sync``: the stops (F022 reference section 6) and that sync never prompts (AC3).

A stop ends the sync early: it prints the summary so far, then its message, uploads nothing after
it and exits 1. Every test here drives ``runcoach sync`` through ``CliRunner`` with a fake Garmin
client (no network, no token) and a mock API (``install_mock_client``). Every invocation runs with a
stdin that answers only a ``read(0)`` probe and raises on any real read, and the command's own
prompt seams (``ask_text``, ``ask_secret``), ``input`` and ``getpass`` are patched to fail the
test, so a hidden prompt cannot pass unnoticed.

Typer's ``CliRunner`` (typer 0.27 vendors click) accepts only ``str`` or ``bytes`` for ``input``
and builds stdin with ``typer.testing.make_input_stream``; that function is patched to return the
raising stream, which the runner then wraps as ``sys.stdin`` and reads its prompt answers from.
"""

from __future__ import annotations

import builtins
import getpass
import io
import sys
import zipfile
from datetime import date, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import typer.testing
from runcoach_cli import api_client, garmin
from runcoach_cli.commands import sessions, sync
from runcoach_cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.usefixtures("prewritten_config")

runner = CliRunner()

TODAY = date(2026, 10, 12)
START = date(2026, 10, 10)
LOGIN_LINE = "Garmin login needed: run `runcoach sync login`"
OLD_API = "the API does not support runs_and_captures_only: update it"


class RaisingStdin(io.RawIOBase):
    """A stdin click accepts (``read(0)`` gives ``b""``) that fails the test on any real read."""

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        raise AssertionError(f"sync read stdin: read({size})")

    def read1(self, size: int = -1) -> bytes:
        raise AssertionError(f"sync read stdin: read1({size})")

    def readinto(self, buffer: Any) -> int:
        raise AssertionError("sync read stdin: readinto")

    def readline(self, size: int | None = -1) -> bytes:
        raise AssertionError(f"sync read stdin: readline({size})")


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _activity(activity_id: int, local: str) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityName": f"run {activity_id}",
        "startTimeLocal": local,
        "activityType": {"typeKey": "running", "typeId": 1, "parentTypeId": 17},
        "manualActivity": False,
    }


# One run a day, 10-10 to 10-12; no snapshots unless a test adds them.
RUNS = [
    _activity(101, "2026-10-10 07:00:00"),
    _activity(201, "2026-10-11 07:00:00"),
    _activity(301, "2026-10-12 07:00:00"),
]


def openapi_doc(with_switch: bool = True) -> dict[str, Any]:
    """``/openapi.json`` shaped as FastAPI serves it: a generated operationId and a ``$ref`` body."""
    fields = {"file": {"type": "string"}, "resting_capture": {"type": "boolean"}}
    if with_switch:
        fields["runs_and_captures_only"] = {"type": "boolean", "default": False}
    return {
        "paths": {
            "/sessions": {
                "get": {"operationId": "list_sessions_sessions_get"},
                "post": {
                    "operationId": "create_session_sessions_post",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {"$ref": "#/components/schemas/Body_create_session_sessions_post"}
                            }
                        }
                    },
                },
            }
        },
        "components": {"schemas": {"Body_create_session_sessions_post": {"properties": fields}}},
    }


@pytest.fixture
def fake_garmin(fake_garmin):
    """The shared fake Garmin client, serving ``RUNS`` unless the test lists other activities."""

    def make(activities: list[dict[str, Any]] | None = None, **kwargs: Any):
        return fake_garmin(RUNS if activities is None else activities, **kwargs)

    return make


@pytest.fixture
def fake_api(fake_api):
    """The shared mock API, serving ``openapi_doc()`` unless the test gives another document."""

    def make(reply: Any = None, *, doc: dict[str, Any] | None = None, **kwargs: Any):
        return fake_api(reply, doc=openapi_doc() if doc is None else doc, **kwargs)

    return make


def _fail_prompt(*args: Any, **kwargs: Any) -> str:
    raise AssertionError(f"sync prompted: {args!r}")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, install_mock_client, fake_api):
    monkeypatch.setattr(sync, "ask_text", _fail_prompt)
    monkeypatch.setattr(sync, "ask_secret", _fail_prompt)
    monkeypatch.setattr(builtins, "input", _fail_prompt)
    monkeypatch.setattr(getpass, "getpass", _fail_prompt)
    monkeypatch.setattr(typer.testing, "make_input_stream", lambda input, charset: RaisingStdin())
    monkeypatch.setattr(sync, "_library_missing", lambda: False)
    monkeypatch.setattr(sync, "today", lambda: TODAY)
    monkeypatch.setattr(sessions, "LOCAL_ZONE", timezone(timedelta(hours=-7)))

    def setup(fake: Any, api: Any = None) -> Any:
        api = api or fake_api()
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


def _ends_with(output: str, text: str) -> bool:
    return output.rstrip().endswith(text)


def test_no_token_ends_with_login_needed_and_never_reads_stdin(env) -> None:
    api = env(garmin.LoginNeeded("no saved token"))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, LOGIN_LINE), result.output
    assert "sync read stdin" not in result.output
    assert "sync prompted" not in result.output
    assert api.uploads == []
    assert type(result.exception) is SystemExit


def test_the_stdin_guard_and_the_prompt_seams_would_catch_a_prompt(env, monkeypatch, fake_garmin) -> None:
    """The guard is live: a sync that prompted through its seam, or read stdin, would fail."""
    monkeypatch.setattr(sync, "today", lambda: sync.ask_text("Garmin email"))
    env(fake_garmin())
    result = _sync()
    assert isinstance(result.exception, AssertionError)
    assert "sync prompted" in str(result.exception)

    monkeypatch.setattr(sync, "today", lambda: date.fromisoformat(input("day")))
    result = _sync()
    assert "sync prompted" in str(result.exception)

    monkeypatch.setattr(sync, "today", lambda: date.fromisoformat(sys.stdin.readline()))
    result = _sync()
    assert "sync read stdin" in str(result.exception)

    # The runner's own prompt reader (what typer.prompt calls under CliRunner) reads the same stdin.
    monkeypatch.setattr(sync, "today", lambda: date.fromisoformat(typer.prompt("day")))
    result = _sync()
    assert "sync read stdin" in str(result.exception)


def test_a_token_refused_mid_sync_stops_with_login_needed(env, fake_garmin) -> None:
    fake = fake_garmin(originals={301: garmin.LoginNeeded("401 Unauthorized")})
    api = env(fake)

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, LOGIN_LINE), result.output
    # The summary so far precedes the message: the two runs before the refused download.
    assert "Stored: 2" in result.output
    assert result.output.index("Stored: 2") < result.output.index(LOGIN_LINE)
    assert api.uploads == [b"ACT-101", b"ACT-201"]
    assert fake.events[-1] == ("original", 301)
    assert "sync prompted" not in result.output


def test_a_token_refused_on_the_activity_list_stops_with_login_needed(env, fake_garmin) -> None:
    api = env(fake_garmin(list_error=garmin.LoginNeeded("403 Forbidden")))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, LOGIN_LINE), result.output
    assert api.uploads == []


@pytest.mark.parametrize(
    "page",
    [
        b"<!DOCTYPE html><html><body>Sign in</body></html>",
        b"<html><head><title>Maintenance</title></head></html>",
        b"\xef\xbb\xbf\r\n  <!doctype html>",
    ],
)
def test_a_web_page_for_a_download_stops_and_names_sync_login(env, page: bytes, fake_garmin) -> None:
    fake = fake_garmin(originals={201: page})
    api = env(fake)

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(
        result.output,
        "Garmin returned a web page instead of the file (a login or maintenance page): "
        "run `runcoach sync login` if this repeats",
    ), result.output
    assert "Stored: 1" in result.output
    assert api.uploads == [b"ACT-101"]
    assert ("original", 301) not in fake.events


def test_a_web_page_for_a_snapshot_stops_too(env, fake_garmin) -> None:
    fake = fake_garmin(snapshots={START: b"<!DOCTYPE html>"})
    api = env(fake)

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert "run `runcoach sync login` if this repeats" in result.output
    assert api.uploads == []


def test_rate_limit_stops_with_the_since_command(env, fake_garmin) -> None:
    fake = fake_garmin(snapshots={date(2026, 10, 11): garmin.RateLimited("429 Too Many Requests")})
    api = env(fake)

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, "runcoach sync --since 2026-10-11`"), result.output
    # Results survive the stop: the summary so far is read from what fetch appended.
    assert "Stored: 1" in result.output
    assert result.output.index("Stored: 1") < result.output.index("429")
    assert api.uploads == [b"ACT-101"]
    assert fake.events[-1] == ("snapshot", date(2026, 10, 11))


def test_rate_limit_on_the_activity_list_names_the_range_start(env, fake_garmin) -> None:
    api = env(fake_garmin(list_error=garmin.RateLimited("429")))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, "runcoach sync --since 2026-10-10`"), result.output
    assert api.uploads == []


def test_garmin_unreachable_names_garmin(env, fake_garmin) -> None:
    # Unreachable is a network error with no HTTP reply; a 502 would be a failed item instead.
    fake = fake_garmin(originals={201: garmin.GarminUnreachable("Connection error: connection refused")})
    api = env(fake)

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    last = result.output.rstrip().splitlines()[-1]
    assert "could not reach Garmin Connect" in last, result.output
    assert "API" not in last
    assert last.endswith("runcoach sync --since 2026-10-11`")
    assert api.uploads == [b"ACT-101"]
    assert ("original", 301) not in fake.events


def test_a_404_never_stops_the_sync(env, fake_garmin) -> None:
    """NotFound is a GarminError beside GarminUnreachable, not under it: it is never a stop."""
    assert not issubclass(garmin.NotFound, garmin.GarminUnreachable)
    fake = fake_garmin(
        originals={201: garmin.NotFound("404")},
        snapshots={START: garmin.NotFound("404"), TODAY: b""},
    )
    api = env(fake)

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 0, result.output
    assert api.uploads == [b"ACT-101", b"ACT-301"]
    assert "No FIT file: 1 (activity 201 (run 201))" in result.output.splitlines()


def test_api_unreachable_names_the_api(env, fake_garmin, fake_api) -> None:
    fake = fake_garmin()
    api = env(fake, fake_api(unreachable_on_upload=2))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    last = result.output.rstrip().splitlines()[-1]
    assert "could not reach API at http://example.test" in last, result.output
    assert "Garmin" not in last
    assert last.endswith("runcoach sync --since 2026-10-11`")
    assert "Stored: 1" in result.output
    assert api.uploads == [b"ACT-101"]
    assert ("original", 301) not in fake.events


def test_an_upload_the_api_does_not_answer_in_time_is_not_called_unreachable(
    env, install_mock_client, fake_garmin, fake_api
) -> None:
    fake = fake_garmin()
    api = fake_api()

    def slow(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and len(api.uploads) == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return api(request)

    env(fake, api)
    install_mock_client(slow)

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    last = " ".join(result.output.rstrip().splitlines()[-1].split())
    assert "could not reach API" not in result.output
    assert "start it with" not in result.output
    assert f"the API at http://example.test did not answer within {int(api_client.UPLOAD_TIMEOUT)} s" in last
    assert "already stored" in last
    assert last.endswith("runcoach sync --since 2026-10-11`"), last
    assert api.uploads == [b"ACT-101"]
    assert ("original", 301) not in fake.events


def test_api_unreachable_before_the_range_is_known_names_plain_sync(
    env, install_mock_client, fake_garmin
) -> None:
    fake = fake_garmin()
    env(fake)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    install_mock_client(handler)

    result = _sync()

    assert result.exit_code == 1, result.output
    last = result.output.rstrip().splitlines()[-1]
    assert "could not reach API at http://example.test" in last
    assert last.endswith("then run `runcoach sync`")
    assert fake.events == []


def test_a_201_without_filter_applied_stops_before_a_second_upload(env, fake_garmin, fake_api) -> None:
    fake = fake_garmin()
    api = env(fake, fake_api(reply=lambda n, content: httpx.Response(201, json={"session_id": f"s{n}"})))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, OLD_API), result.output
    assert api.uploads == [b"ACT-101"]
    assert "Stored: 1" in result.output
    assert ("original", 201) not in fake.events


def test_a_200_without_filter_applied_stops_before_a_second_upload(env, fake_garmin, fake_api) -> None:
    fake = fake_garmin()
    reply = lambda n, content: httpx.Response(200, json={"skipped": True, "reason": "non_activity"})
    api = env(fake, fake_api(reply=reply))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, OLD_API), result.output
    assert api.uploads == [b"ACT-101"]


def test_an_api_without_the_switch_stops_before_any_upload(env, fake_garmin, fake_api) -> None:
    fake = fake_garmin()
    api = env(fake, fake_api(doc=openapi_doc(with_switch=False)))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, OLD_API + " and restart the API"), result.output
    assert api.uploads == []
    assert "POST /sessions" not in api.calls
    assert "GET /openapi.json" in api.calls


@pytest.mark.parametrize(
    "doc",
    [
        {"paths": {}},
        {"paths": {"/sessions": {"post": {"operationId": "ingestSession"}}}},
        [],
        {
            "paths": {
                "/sessions": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {"schema": {"$ref": "#/components/schemas/Missing"}}
                            }
                        }
                    }
                }
            }
        },
    ],
)
def test_an_unreadable_openapi_document_stops_before_any_upload(env, doc, fake_garmin, fake_api) -> None:
    api = env(fake_garmin(), fake_api(doc=doc))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, OLD_API + " and restart the API"), result.output
    assert api.uploads == []


def test_the_openapi_check_runs_once_and_accepts_an_inline_body(env, fake_garmin, fake_api) -> None:
    doc = {
        "paths": {
            "/sessions": {
                "post": {
                    "operationId": "ingestSession",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {"properties": {"file": {}, "runs_and_captures_only": {}}}
                            }
                        }
                    },
                }
            }
        }
    }
    api = env(fake_garmin(), fake_api(doc=doc))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 0, result.output
    assert api.calls.count("GET /openapi.json") == 1
    assert api.calls.index("GET /openapi.json") < api.calls.index("POST /sessions")
    assert len(api.uploads) == 3


def test_an_unclassified_garmin_error_on_the_activity_list_stops(env, fake_garmin) -> None:
    api = env(fake_garmin(list_error=garmin.GarminError("something odd")))

    result = _sync("--since", "2026-10-10")

    assert result.exit_code == 1, result.output
    assert _ends_with(result.output, "runcoach sync --since 2026-10-10`"), result.output
    assert api.uploads == []
