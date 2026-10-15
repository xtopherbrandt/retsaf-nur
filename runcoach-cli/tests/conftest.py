"""Shared pytest fixtures for runcoach-cli tests.

Every fixture here was previously copy-pasted into the individual test
modules -- ``_install_mock_client`` three times and ``isolated_config_path``
four times -- and the duplication was actively growing as new command test
files were added. They live here now so the next test file gets them for
free instead of inheriting another copy.

``isolated_config_path`` is autouse and deliberately **bare**: it points
``config.CONFIG_PATH`` at a per-test ``tmp_path`` location and creates
nothing, so no test -- here or in any later CLI task -- ever reads or writes
the real ``~/.runcoach/cli.toml``. Being autouse matters: tests that never
name it (``test_api_client.py``, ``test_main.py``) still get the isolation,
and making it opt-in would silently un-isolate anything that forgot to ask.
Creating nothing also matters: ``test_config.py`` asserts the missing-config
guard fires, and ``test_init.py`` asserts ``runcoach init`` creates the
parent directory itself -- both would be defeated by a fixture that
pre-created the file or its directory.

``prewritten_config`` layers on top for the command tests that need config
*content* to exist rather than just a redirected path (``test_ingest.py``,
``test_status.py``, which invoke commands that call ``load_config()``
first). It is opt-in per module via
``pytestmark = pytest.mark.usefixtures("prewritten_config")`` rather than
autouse, precisely because making it global would break the two modules
above.

``install_mock_client`` is exposed as a fixture yielding the installer
callable rather than as an importable helper, so test modules don't have to
import from ``conftest`` (fragile: two workspace members each have a
``conftest`` module). It swaps the module-level ``api_client.client`` for
one backed by an ``httpx.MockTransport``, which is how the suite simulates
connect errors and timeouts without ever touching the network.

``runcoach-api/tests/test_init_cmd.py`` has a similar-looking
``isolated_config_path`` and is intentionally **not** covered here: it
patches a different module attribute (``init_cmd.CONFIG_PATH``) against a
different filename (``api.toml``) in a different workspace package.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import tomli_w

from runcoach_cli import api_client, config


@pytest.fixture(autouse=True)
def isolated_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``config.CONFIG_PATH`` at a throwaway location for every test.

    Nothing is created on disk -- the path simply doesn't exist yet. Never
    touches the real ``~/.runcoach/`` directory.
    """
    config_path = tmp_path / ".runcoach" / "cli.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    return config_path


@pytest.fixture
def prewritten_config(isolated_config_path: Path) -> Path:
    """``isolated_config_path`` plus a real, loadable config file on disk.

    For command tests whose commands call ``load_config()`` before doing
    anything else, and so need content to exist -- not merely a redirected
    path.
    """
    isolated_config_path.parent.mkdir(parents=True, exist_ok=True)
    isolated_config_path.write_bytes(tomli_w.dumps({"api_url": "http://example.test"}).encode())
    return isolated_config_path


@pytest.fixture
def fresh_sync_state(prewritten_config: Path) -> Path:
    """``prewritten_config`` plus a ``sync-state.json`` holding no dates, as a sync leaves it when it
    has never completed and nothing has failed: the range is the store rule's, and no line about a
    missing state file is printed, so a test can compare the output that follows the ``From`` line.
    """
    state_path = prewritten_config.parent / "sync-state.json"
    state_path.write_bytes(b'{"last_completed": null, "oldest_failed": null}')
    return state_path


@pytest.fixture
def install_mock_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], None]:
    """Return an installer that swaps the module-level client for a mock one.

    Usage: ``install_mock_client(handler)``, where ``handler`` is an
    ``httpx.MockTransport`` handler -- it may return a response or raise an
    ``httpx`` transport error to simulate an unreachable API.
    """

    def _install_mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        mock_client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
        monkeypatch.setattr(api_client, "client", mock_client)

    return _install_mock_client


class LibraryResponse:
    """What ``requests`` would hand the Garmin library: a status, a body and (maybe) JSON."""

    def __init__(self, status_code: int, content: bytes = b"", payload: object = None) -> None:
        self.status_code = status_code
        self.content = content
        self.payload = payload
        self.text = content.decode("latin-1")

    def json(self) -> object:
        if self.payload is None:
            raise ValueError("not json")
        return self.payload


class LibrarySession:
    """Stands in for the library client's HTTP session; ``answer(method, url, params)`` decides.

    ``answer`` returns an int (that HTTP status, with an HTML body), bytes (a 200 download), a list
    or dict (a 200 JSON reply), or an exception to raise (a ``requests`` network error).
    """

    def __init__(self, answer: Callable[[str, str, dict], object]) -> None:
        self.answer = answer
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs: object) -> LibraryResponse:
        self.requests.append((method, url))
        result = self.answer(method, url, dict(kwargs.get("params") or {}))  # type: ignore[call-overload]
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, int):
            return LibraryResponse(result, b"<html>denied</html>")
        if isinstance(result, bytes):
            return LibraryResponse(200, result)
        return LibraryResponse(200, b"json", payload=result)


@pytest.fixture
def library_garmin() -> Callable[..., tuple[object, LibrarySession]]:
    """A factory for the installed library's real ``Garmin`` with only its HTTP session stubbed.

    ``library_garmin(answer, retry_attempts=0)`` returns ``(garmin, session)``. Everything between
    the session and the caller (``Client._run_request``, the ``_handle_api_errors`` decorator and its
    retries) is the installed code; the token refresh and API headers are stubbed and the retry
    back-off is zero seconds, so nothing reaches Garmin and no test waits.
    """
    from garminconnect import Garmin

    def make(answer: Callable[[str, str, dict], object], retry_attempts: int = 0):
        lib = Garmin(retry_attempts=retry_attempts, retry_min_wait=0.0, retry_max_wait=0.0)
        session = LibrarySession(answer)
        lib.client._api_session = session
        lib.client.get_api_headers = dict
        lib.client._refresh_session = lambda: None
        return lib, session

    return make


def _refuse_curl_cffi(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """Make every ``curl_cffi`` entry point the Garmin library uses raise ``curl_cffi``'s own
    ``ConnectionError``, recording the URL in ``seen``.

    The library's login strategies open ``curl_cffi.requests.Session`` and call its ``get`` and
    ``post`` (which go through ``Session.request``), and ``Client._http_post`` calls
    ``curl_cffi.requests.post``; none of them goes through ``requests``, so a ``requests`` stub
    alone does not keep a login off Garmin's SSO. Nothing happens when ``curl_cffi`` is not
    installed.
    """
    try:
        from curl_cffi import requests as cffi_requests
        from curl_cffi.requests.exceptions import ConnectionError as CffiConnectionError
        from curl_cffi.requests.session import AsyncSession, Session
    except ImportError:
        return

    def refuse(*args: object, **kwargs: object) -> None:
        url = kwargs.get("url") or next((a for a in args if isinstance(a, str) and "://" in a), None)
        seen.append(str(url))
        raise CffiConnectionError(f"curl_cffi blocked in tests: {url}")

    for name in ("request", "get", "post", "put", "patch", "delete", "head", "options"):
        if hasattr(cffi_requests, name):
            monkeypatch.setattr(cffi_requests, name, refuse)
    monkeypatch.setattr(Session, "request", refuse)
    monkeypatch.setattr(AsyncSession, "request", refuse)


def _library_reply(answer: object, url: str) -> LibraryResponse:
    """``answer`` (or ``answer(url)`` when it is callable) as the reply ``requests`` would give."""
    if callable(answer) and not isinstance(answer, BaseException):
        answer = answer(url)
    if isinstance(answer, BaseException):
        raise answer
    if isinstance(answer, int):
        return LibraryResponse(answer, b"<html>x</html>")
    return LibraryResponse(200, b"json", payload=answer)


@pytest.fixture
def credential_login_network(monkeypatch: pytest.MonkeyPatch) -> Callable[[object], list[str]]:
    """Every request a credential login (``Garmin(email, password).login()``) makes, answered offline.

    ``credential_login_network(answer)`` returns the list of URLs requested. ``requests.Session.request``
    and ``Client._http_post`` answer with ``answer`` (an int status with an HTML body, or an
    exception to raise); every ``curl_cffi`` entry point raises ``curl_cffi``'s connection error, as
    it does offline. The library's anti-bot sleeps are zero, so no test waits and nothing leaves it.
    """
    import time

    import requests
    from garminconnect.client import Client

    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    seen: list[str] = []
    _refuse_curl_cffi(monkeypatch, seen)

    def install(answer: object) -> list[str]:
        def request(self, method, url, **kwargs):
            seen.append(url)
            return _library_reply(answer, url)

        def http_post(self, url, **kwargs):
            seen.append(url)
            return _library_reply(answer, url)

        monkeypatch.setattr(requests.Session, "request", request)
        monkeypatch.setattr(Client, "_http_post", http_post)
        return seen

    return install


@pytest.fixture
def saved_token_session(monkeypatch: pytest.MonkeyPatch) -> Callable[[object], list[str]]:
    """A saved Garmin token in the tmp home, with every ``requests`` call answered by ``answer``.

    ``saved_token_session(answer)`` returns the list of URLs requested. ``answer`` is an int (that
    HTTP status, with an HTML body that is not JSON), an exception to raise (a ``requests``
    network error), a list or dict (a 200 JSON reply), or a callable taking the URL and returning
    one of those. ``Garmin.login(token_dir)`` then runs as ``runcoach sync`` runs it: the token
    loads from the file, and the profile fetch goes through ``requests.Session.request``. The
    library's one-second sleeps between its profile retries are zero, its token refresh (which
    posts through ``curl_cffi`` when that is installed, bypassing ``requests``) is refused as a
    network error, and every ``curl_cffi`` entry point the login strategies use raises, so nothing
    leaves the test even if a later test gives the library credentials.
    """
    import json
    import time

    import requests
    from garminconnect.client import Client

    from runcoach_cli import garmin

    token_file = garmin.token_dir() / "garmin_tokens.json"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(
        json.dumps({"di_token": "abc", "di_refresh_token": "def", "di_client_id": "ghi"}), encoding="utf-8"
    )
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    def no_refresh_post(self, url, **kwargs):
        raise requests.ConnectionError("token refresh blocked in tests")

    monkeypatch.setattr(Client, "_http_post", no_refresh_post)
    seen: list[str] = []
    _refuse_curl_cffi(monkeypatch, seen)

    def install(answer: object) -> list[str]:
        def request(self, method, url, **kwargs):
            seen.append(url)
            return _library_reply(answer, url)

        monkeypatch.setattr(requests.Session, "request", request)
        return seen

    return install


# ---------------------------------------------------------------------------
# The sync tests' fake Garmin client and API stub.
#
# Every ``test_sync_*.py`` module drives ``runcoach sync`` against a fake Garmin client (handed to
# the command through ``sync._adapter``) and a mock API (installed with ``install_mock_client``).
# Both live here, each configured per test, and are exposed as fixtures returning the class
# (``fake_garmin``, ``fake_api``): under ``--import-mode=importlib`` a test module cannot import
# from ``conftest``.
# ---------------------------------------------------------------------------


def _one_entry_zip(activity_id: int) -> bytes:
    """The download a fake activity answers by default: one entry holding ``ACT-<id>``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{activity_id}_ACTIVITY.fit", f"ACT-{activity_id}".encode())
    return buffer.getvalue()


class FakeGarmin:
    """A ``GarminClient`` that serves what the test gives it and records what it was asked.

    ``activities`` is the activity list (each call answers a copy; ``list_error``, when set, is
    raised instead, on every call or, with ``list_error_on=n``, on the n-th call only). ``originals`` and ``snapshots`` map an activity id or a day to what its download
    answers: bytes (or any other value) are returned, an exception is raised. An activity with no
    entry answers a one-entry zip holding ``ACT-<id>``; a day with no entry raises ``NotFound``.
    With ``allow_originals=False`` any activity download fails the test.

    ``events`` holds every call in order, as ``("list", (start, end))``, ``("original", id)`` and
    ``("snapshot", day)``; ``downloads`` is the same without the list calls; ``listed`` and
    ``snapshot_days`` hold the list ranges and the snapshot days asked for.
    """

    def __init__(
        self,
        activities: list[Any] | None = None,
        *,
        originals: dict[int, Any] | None = None,
        snapshots: dict[date, Any] | None = None,
        list_error: Exception | None = None,
        allow_originals: bool = True,
        list_error_on: int | None = None,
    ) -> None:
        self.activities = activities or []
        self.originals = originals or {}
        self.snapshots = snapshots or {}
        self.list_error = list_error
        self.list_error_on = list_error_on
        self.allow_originals = allow_originals
        self.events: list[tuple[str, Any]] = []

    @property
    def downloads(self) -> list[tuple[str, Any]]:
        return [event for event in self.events if event[0] != "list"]

    @property
    def listed(self) -> list[tuple[date, date]]:
        return [arg for kind, arg in self.events if kind == "list"]

    @property
    def snapshot_days(self) -> list[date]:
        return [arg for kind, arg in self.events if kind == "snapshot"]

    def list_activities(self, start: date, end: date) -> list[Any]:
        self.events.append(("list", (start, end)))
        if self.list_error is not None and self.list_error_on in (None, len(self.listed)):
            raise self.list_error
        return list(self.activities)

    def download_original(self, activity_id: int) -> Any:
        self.events.append(("original", activity_id))
        if not self.allow_originals:
            raise AssertionError("no activity is downloaded in this test")
        value = self.originals.get(activity_id)
        if value is None:
            value = _one_entry_zip(activity_id)
        if isinstance(value, Exception):
            raise value
        return value

    def download_snapshot(self, day: date) -> Any:
        from runcoach_cli import garmin

        self.events.append(("snapshot", day))
        value = self.snapshots.get(day)
        if value is None:
            raise garmin.NotFound(f"no snapshot on {day}")
        if isinstance(value, Exception):
            raise value
        return value


# ``/openapi.json`` as FastAPI serves it: ``POST /sessions`` lists the switch in its form body, and
# ``PUT /race-tags`` is listed as ``setRaceTags``.
OPENAPI_WITH_SWITCH: dict[str, Any] = {
    "paths": {
        "/sessions": {
            "post": {
                "requestBody": {
                    "content": {"multipart/form-data": {"schema": {"$ref": "#/components/schemas/Body"}}}
                }
            }
        },
        "/race-tags": {"put": {"operationId": "setRaceTags"}},
    },
    "components": {"schemas": {"Body": {"properties": {"file": {}, "runs_and_captures_only": {}}}}},
}


def _multipart_part(body: bytes, field: str) -> tuple[bytes, bytes] | None:
    """The headers and content of the multipart part named ``field``, or ``None``."""
    marker = f'name="{field}"'.encode()
    for part in body.split(b"\r\n--"):
        head, _, content = part.partition(b"\r\n\r\n")
        if marker in head:
            return head, content.removesuffix(b"\r\n")
    return None


class FakeApi:
    """The RunCoach API as ``runcoach sync`` sees it: an ``httpx.MockTransport`` handler.

    ``GET /sessions`` answers ``pages`` as a cursor chain (page N's ``next_cursor`` is
    ``cursor-<N+1>``; the default is one empty page). ``GET /openapi.json`` answers ``doc``; with
    ``doc=None`` it fails the test. ``POST /sessions`` (refused when ``accept_uploads`` is false)
    records the file part's bytes in ``uploads`` (and the part's headers and the switch's value in
    ``heads`` and ``switches``), runs ``check(body)`` first when given, and answers
    ``replies[content]`` when the bytes are listed there, else ``reply(n, content)`` (``n`` counts
    the uploads, this one included), else 201 stored as ``s<n>`` with ``filter_applied``.
    ``unreachable_on_upload=n`` makes the n-th upload a connect error, before it is recorded.
    ``PUT /race-tags`` records each batch's items in ``tag_batches`` (all of them in ``sent``) and
    answers every item with ``tag_outcomes[connect_activity_id]`` (default ``unchanged``): an
    outcome, an ``(outcome, session_id, candidates)`` tuple, or a callable taking the item and
    returning either. ``fail_tags`` answers every ``PUT`` with that status instead (after recording
    the batch), or, as ``"unreachable"``, makes it a connect error.
    ``requests`` holds every request; ``calls`` the same as ``"<method> <path>"``.
    """

    def __init__(
        self,
        reply: Callable[[int, bytes], httpx.Response] | None = None,
        *,
        replies: dict[bytes, httpx.Response] | None = None,
        doc: dict[str, Any] | None = OPENAPI_WITH_SWITCH,
        pages: list[list[dict[str, Any]]] | None = None,
        accept_uploads: bool = True,
        unreachable_on_upload: int | None = None,
        check: Callable[[bytes], None] | None = None,
        tag_outcomes: dict[int, Any] | None = None,
        fail_tags: int | str | None = None,
    ) -> None:
        self.reply = reply
        self.tag_outcomes = tag_outcomes or {}
        self.fail_tags = fail_tags
        self.tag_batches: list[list[dict[str, Any]]] = []
        self.replies = replies or {}
        self.doc = doc
        self.pages = [[]] if pages is None else pages
        self.accept_uploads = accept_uploads
        self.unreachable_on_upload = unreachable_on_upload
        self.check = check
        self.requests: list[httpx.Request] = []
        self.uploads: list[bytes] = []
        self.heads: list[bytes] = []
        self.switches: list[bytes | None] = []

    @property
    def calls(self) -> list[str]:
        return [f"{request.method} {request.url.path}" for request in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path == "/sessions":
            since = request.url.params.get("since")
            index = 0 if since is None else int(since.removeprefix("cursor-"))
            next_cursor = f"cursor-{index + 1}" if index + 1 < len(self.pages) else None
            return httpx.Response(200, json={"items": self.pages[index], "next_cursor": next_cursor})
        if request.method == "GET" and request.url.path == "/openapi.json" and self.doc is not None:
            return httpx.Response(200, json=self.doc)
        if request.method == "POST" and request.url.path == "/sessions" and self.accept_uploads:
            if self.unreachable_on_upload == len(self.uploads) + 1:
                raise httpx.ConnectError("connection refused", request=request)
            body = request.read()
            if self.check is not None:
                self.check(body)
            file_part = _multipart_part(body, "file")
            if file_part is None:
                raise AssertionError("no file part")
            head, content = file_part
            switch = _multipart_part(body, "runs_and_captures_only")
            self.uploads.append(content)
            self.heads.append(head)
            self.switches.append(None if switch is None else switch[1])
            n = len(self.uploads)
            if content in self.replies:
                return self.replies[content]
            if self.reply is not None:
                return self.reply(n, content)
            return httpx.Response(201, json={"session_id": f"s{n}", "filter_applied": True})
        if request.method == "PUT" and request.url.path == "/race-tags":
            return self._race_tags(request)
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    @property
    def sent(self) -> list[dict[str, Any]]:
        """Every item sent to ``PUT /race-tags``, in order."""
        return [item for batch in self.tag_batches for item in batch]

    def _race_tags(self, request: httpx.Request) -> httpx.Response:
        if self.fail_tags == "unreachable":
            raise httpx.ConnectError("connection refused", request=request)
        items = json.loads(request.read())["items"]
        self.tag_batches.append(items)
        if self.fail_tags is not None:
            return httpx.Response(int(self.fail_tags), json={"detail": "the store is busy"})
        answers = []
        for item in items:
            outcome = self.tag_outcomes.get(item["connect_activity_id"], "unchanged")
            if callable(outcome):
                outcome = outcome(item)
            name, session_id, candidates = (outcome, None, []) if isinstance(outcome, str) else outcome
            answers.append(
                {
                    "connect_activity_id": item["connect_activity_id"],
                    "outcome": name,
                    "session_id": session_id,
                    "candidates": candidates,
                }
            )
        counts: dict[str, int] = {}
        for answer in answers:
            counts[answer["outcome"]] = counts.get(answer["outcome"], 0) + 1
        return httpx.Response(200, json={"items": answers, "counts": counts})


@pytest.fixture
def fake_garmin() -> type[FakeGarmin]:
    """The shared fake Garmin client class (see ``FakeGarmin``)."""
    return FakeGarmin


@pytest.fixture
def fake_api() -> type[FakeApi]:
    """The shared mock API class (see ``FakeApi``); install an instance with ``install_mock_client``."""
    return FakeApi
