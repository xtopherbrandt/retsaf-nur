"""Tests for the real Garmin adapter (``runcoach_cli.garmin_connect``) against the installed library.

Every exception here is the installed ``garminconnect`` package's own class, constructed directly
in the shape the package raises it or raised by the package itself, and every ``Garmin`` is the
package's real class with only its network-facing ``login`` or its HTTP session replaced.
Nothing reaches Garmin and no real token is read: the token directory is the autouse
``isolated_config_path`` tmp home's ``garmin`` folder.

Library facts these tests rely on (read from the installed 0.3.17 source):

- ``Garmin.login(tokenstore)`` does ``tokenstore or os.getenv("GARMINTOKENS")``, so a None path
  silently picks up the environment variable.
- ``Garmin.login`` with a path whose token file is missing and no credentials raises
  ``GarminConnectAuthenticationError("Username and password are required")`` without a request.
- ``Client._run_request`` raises ``GarminConnectConnectionError("API Error NNN")`` for a status of
  400 or more, with no ``response`` attached. The API decorator turns a 401 into
  ``GarminConnectAuthenticationError``, a 429 into ``GarminConnectTooManyRequestsError``, a 404
  into ``GarminConnectNotFoundError``, any other 4xx into ``GarminConnectConnectionError("<label>
  client error (NNN): ...")``, a 5xx (after its retries) into ``"<label> HTTP error: ..."`` and a
  ``requests`` network error into ``"Connection error: ..."``, logging the last two with
  ``logger.exception``. So the status is only in the message, which is why the status rows below
  drive the real ``Garmin`` with only its HTTP session stubbed (the ``library_garmin`` fixture).
- ``Client.dump(path)`` writes ``<path>/garmin_tokens.json``.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import garminconnect
import pytest
import requests
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectInvalidFileFormatError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from runcoach_cli import garmin, garmin_connect

SENTINEL_EMAIL = "sentinel-email-7f3a@example.invalid"
SENTINEL_PASSWORD = "sentinel-password-91c2-do-not-store"


def _not_found() -> GarminConnectNotFoundError:
    # What the decorator raises for a 404 (``Client._run_request`` attaches no response).
    return GarminConnectNotFoundError("Download client error (404): API Error 404")


# A refused connection as ``requests`` words it on Windows: "WinError 10061" holds "Error 100",
# which the library's own message parser reads as a status.
REFUSED_CONNECTION = (
    "HTTPSConnectionPool(host='connectapi.garmin.com', port=443): Max retries exceeded with url: "
    "/x (Caused by NewConnectionError('<urllib3.connection.HTTPSConnection object>: Failed to "
    "establish a new connection: [WinError 10061] No connection could be made because the target "
    "machine actively refused it'))"
)


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (lambda: GarminConnectAuthenticationError("Authentication failed"), garmin.LoginNeeded),
        (lambda: GarminConnectTooManyRequestsError("Rate limit exceeded"), garmin.RateLimited),
        (_not_found, garmin.NotFound),
        (lambda: GarminConnectInvalidFileFormatError("bad"), garmin.GarminError),
        # The load path's own wrapping ("Login failed: <the request's error>").
        (lambda: GarminConnectConnectionError("Login failed: API Error 403"), garmin.LoginNeeded),
        (lambda: GarminConnectConnectionError("Login failed: API Error 401"), garmin.LoginNeeded),
        (lambda: GarminConnectConnectionError("Login failed: API Error 429"), garmin.RateLimited),
        (lambda: GarminConnectConnectionError("Login failed: API Error 500"), garmin.GarminError),
        (lambda: GarminConnectConnectionError("Pagination exceeded 1000 requests"), garmin.GarminUnreachable),
    ],
    ids=["auth", "toomany", "notfound", "other-library-error", "load403", "load401", "load429",
         "load500", "no-status"],
)
def test_library_exceptions_map_to_the_cli_exceptions(make, expected) -> None:
    mapped = garmin_connect.translate_error(make())
    assert type(mapped) is expected
    assert isinstance(mapped, garmin.GarminError)


def test_a_library_404_is_not_read_as_unreachable() -> None:
    # GarminConnectNotFoundError subclasses GarminConnectConnectionError: the subclass must win,
    # also when the library had no response to attach (it sets ``response`` only when present).
    assert issubclass(GarminConnectNotFoundError, GarminConnectConnectionError)
    bare = _not_found()
    assert bare.response is None
    assert type(garmin_connect.translate_error(bare)) is garmin.NotFound


def _answer_status(status: int):
    def answer(method, url, params):
        return status

    return answer


def _answer_raise(exc: Exception):
    def answer(method, url, params):
        return exc

    return answer


# Every HTTP status per the user's ruling of 2026-10-13: 401/403 need a login, 404 is not found,
# 429 is a rate limit, any other 4xx or 5xx is a plain GarminError (a download's item fails), and an
# error with no HTTP status at all is GarminUnreachable.
_STATUS_ROWS = [
    (_answer_status(400), garmin.GarminError),
    (_answer_status(401), garmin.LoginNeeded),
    (_answer_status(403), garmin.LoginNeeded),
    (_answer_status(404), garmin.NotFound),
    (_answer_status(410), garmin.GarminError),
    (_answer_status(429), garmin.RateLimited),
    (_answer_status(500), garmin.GarminError),
    (_answer_status(503), garmin.GarminError),
    (_answer_raise(requests.ConnectionError(REFUSED_CONNECTION)), garmin.GarminUnreachable),
    (_answer_raise(requests.Timeout("Read timed out. (read timeout=15)")), garmin.GarminUnreachable),
]
_STATUS_IDS = ["400", "401", "403", "404", "410", "429", "500", "503", "refused", "timeout"]


@pytest.mark.parametrize(("answer", "expected"), _STATUS_ROWS, ids=_STATUS_IDS)
@pytest.mark.parametrize("call", ["snapshot", "original", "list"])
def test_each_status_through_the_real_library_maps_per_the_ruling(
    library_garmin, call, answer, expected
) -> None:
    lib, session = library_garmin(answer)
    client = garmin_connect.GarminConnectClient(lib)
    calls = {
        "snapshot": lambda: client.download_snapshot(date(2026, 10, 12)),
        "original": lambda: client.download_original(24686524347),
        "list": lambda: client.list_activities(date(2026, 10, 10), date(2026, 10, 13)),
    }
    with pytest.raises(garmin.GarminError) as info:
        calls[call]()
    assert type(info.value) is expected, (info.value, info.value.__cause__)
    # The library's own exception is the cause, and the request went through the stubbed session.
    assert type(info.value.__cause__).__module__.startswith("garminconnect")
    assert session.requests


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Download client error (403): API Error 403", garmin.LoginNeeded),
        ("Login failed: API Error 403", garmin.LoginNeeded),
        ("Download HTTP error: API Error 500", garmin.GarminError),
        ("Download client error (410): x", garmin.GarminError),
        ("Connection error: " + REFUSED_CONNECTION, garmin.GarminUnreachable),
    ],
    ids=["client403", "load403", "http500", "client410", "refused"],
)
def test_the_status_is_read_from_the_message_without_the_library_parser(
    monkeypatch, message, expected
) -> None:
    # A later library line may drop its private parser: the adapter's own reading still holds.
    monkeypatch.delattr(garminconnect, "_extract_status_code")
    assert type(garmin_connect.translate_error(GarminConnectConnectionError(message))) is expected


def test_a_network_cause_has_no_status_whatever_its_text(library_garmin) -> None:
    # A network error whose text happens to read like a status is still "no reply".
    lib, _ = library_garmin(_answer_raise(requests.ConnectionError("upstream said API Error 403")))
    with pytest.raises(garmin.GarminUnreachable):
        garmin_connect.GarminConnectClient(lib).download_snapshot(date(2026, 10, 12))


def test_a_5xx_is_a_plain_garmin_error_after_the_library_retries(library_garmin) -> None:
    lib, session = library_garmin(_answer_status(503), retry_attempts=2)
    client = garmin_connect.GarminConnectClient(lib)
    with pytest.raises(garmin.GarminError) as info:
        client.download_snapshot(date(2026, 10, 12))
    assert type(info.value) is garmin.GarminError
    assert len(session.requests) == 3


def test_a_token_that_cannot_be_saved_is_a_garmin_error(monkeypatch) -> None:
    from garminconnect.client import Client

    _record_login(monkeypatch, [])
    failure = OSError("disk full")

    def failing_dump(self, path):
        raise failure

    monkeypatch.setattr(Client, "dump", failing_dump)
    with pytest.raises(garmin.GarminError) as info:
        garmin_connect.login(SENTINEL_EMAIL, SENTINEL_PASSWORD, lambda: "123456")
    assert info.value.__cause__ is failure


def _reset_library_logger() -> logging.Logger:
    lib_logger = logging.getLogger("garminconnect")
    for handler in list(lib_logger.handlers):
        lib_logger.removeHandler(handler)
    lib_logger.propagate = True
    return lib_logger


def _record_login(monkeypatch: pytest.MonkeyPatch, calls: list, *, raises: Exception | None = None):
    def fake_login(self, /, tokenstore=None):
        import os

        calls.append({"tokenstore": tokenstore, "env": os.environ.get("GARMINTOKENS"), "self": self})
        if raises is not None:
            raise raises
        self.client.di_token = "di-token-value"
        self.client.di_refresh_token = "di-refresh-value"
        self.client.di_client_id = "di-client-value"
        return None, None

    monkeypatch.setattr(Garmin, "login", fake_login)


def test_open_client_passes_the_token_path_with_a_decoy_env(monkeypatch, tmp_path) -> None:
    decoy = tmp_path / "decoy-tokens"
    monkeypatch.setenv("GARMINTOKENS", str(decoy))
    calls: list = []
    _record_login(monkeypatch, calls)

    client = garmin_connect.open_client()

    assert [c["tokenstore"] for c in calls] == [str(garmin.token_dir())]
    assert isinstance(client, garmin.GarminClient)
    assert not decoy.exists()


def test_open_client_without_a_token_needs_login_from_the_real_library(monkeypatch, tmp_path) -> None:
    # The real Garmin.login: no token file under token_dir and no credentials -> its own
    # authentication error, raised before any request, mapped to LoginNeeded.
    decoy = tmp_path / "decoy-tokens"
    monkeypatch.setenv("GARMINTOKENS", str(decoy))
    with pytest.raises(garmin.LoginNeeded):
        garmin_connect.open_client()


def test_the_library_logger_is_silenced(monkeypatch, caplog, capfd) -> None:
    lib_logger = _reset_library_logger()
    _record_login(monkeypatch, [])
    garmin_connect.open_client()

    assert lib_logger.propagate is False
    assert any(isinstance(h, logging.NullHandler) for h in lib_logger.handlers)

    caplog.set_level(logging.DEBUG)
    try:
        raise GarminConnectConnectionError("boom")
    except GarminConnectConnectionError:
        logging.getLogger("garminconnect").exception("Download failed for path")
        logging.getLogger("garminconnect.client").exception("Download failed for path")
    assert caplog.records == []
    out, err = capfd.readouterr()
    assert "Download failed" not in out + err


def test_login_silences_the_library_logger(monkeypatch) -> None:
    lib_logger = _reset_library_logger()
    _record_login(monkeypatch, [])
    garmin_connect.login(SENTINEL_EMAIL, SENTINEL_PASSWORD, lambda: "123456")
    assert lib_logger.propagate is False


def test_login_dumps_the_real_token_without_the_credentials(monkeypatch, tmp_path) -> None:
    decoy = tmp_path / "decoy-tokens"
    monkeypatch.setenv("GARMINTOKENS", str(decoy))
    calls: list = []
    _record_login(monkeypatch, calls)

    def prompt() -> str:
        return "123456"

    garmin_connect.login(SENTINEL_EMAIL, SENTINEL_PASSWORD, prompt)

    (call,) = calls
    # A fresh login: no token store, and the decoy env var cannot stand in for one.
    assert call["tokenstore"] is None
    assert call["env"] is None
    assert call["self"].username == SENTINEL_EMAIL
    assert call["self"].prompt_mfa is prompt
    # The env var is back afterwards.
    import os

    assert os.environ.get("GARMINTOKENS") == str(decoy)
    assert not decoy.exists()

    token_dir = garmin.token_dir()
    files = [p for p in token_dir.rglob("*") if p.is_file()]
    assert [p.name for p in files] == ["garmin_tokens.json"]
    data = files[0].read_bytes()
    assert b"di-refresh-value" in data
    for sentinel in (SENTINEL_EMAIL, SENTINEL_PASSWORD):
        for encoded in (sentinel.encode("utf-8"), sentinel.encode("utf-16-le")):
            assert encoded not in data


def test_a_failed_login_keeps_the_old_token(monkeypatch) -> None:
    token_file = garmin.token_dir() / "garmin_tokens.json"
    token_file.parent.mkdir(parents=True)
    token_file.write_bytes(b'{"di_token": "old"}')
    _record_login(monkeypatch, [], raises=GarminConnectAuthenticationError("Authentication failed"))

    with pytest.raises(garmin.LoginNeeded):
        garmin_connect.login(SENTINEL_EMAIL, SENTINEL_PASSWORD, lambda: "000000")

    assert token_file.read_bytes() == b'{"di_token": "old"}'
    assert [p.name for p in token_file.parent.iterdir()] == ["garmin_tokens.json"]


class FakeGarmin:
    """Stands in for a logged-in ``Garmin``: records calls, returns or raises as told."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.raises = raises

    def _answer(self, value):
        if self.raises is not None:
            raise self.raises
        return value

    def get_activities_by_date(self, startdate, enddate=None, activitytype=None, sortorder=None):
        self.calls.append(("list", startdate, enddate))
        return self._answer([{"activityId": 1}])

    def download_activity(self, activity_id, dl_fmt=None):
        self.calls.append(("activity", activity_id, dl_fmt))
        return self._answer(b"zip-activity")

    def download_health_snapshot(self, requested_date):
        self.calls.append(("snapshot", requested_date))
        return self._answer(b"zip-snapshot")


def test_adapter_calls_the_library_with_its_own_shapes() -> None:
    fake = FakeGarmin()
    client = garmin_connect.GarminConnectClient(fake)

    assert client.list_activities(date(2026, 10, 10), date(2026, 10, 13)) == [{"activityId": 1}]
    assert client.download_original(24686524347) == b"zip-activity"
    assert client.download_snapshot(date(2026, 10, 13)) == b"zip-snapshot"
    assert fake.calls == [
        ("list", "2026-10-10", "2026-10-13"),
        ("activity", "24686524347", Garmin.ActivityDownloadFormat.ORIGINAL),
        ("snapshot", "2026-10-13"),
    ]


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (GarminConnectTooManyRequestsError("Rate limit exceeded"), garmin.RateLimited),
        (GarminConnectAuthenticationError("Authentication failed"), garmin.LoginNeeded),
        (_not_found(), garmin.NotFound),
        (GarminConnectConnectionError("Download HTTP error: API Error 503"), garmin.GarminError),
        (GarminConnectConnectionError("Connection error: " + REFUSED_CONNECTION), garmin.GarminUnreachable),
    ],
    ids=["429", "auth", "404", "503", "no-status"],
)
def test_adapter_calls_raise_the_cli_exceptions(raised, expected) -> None:
    client = garmin_connect.GarminConnectClient(FakeGarmin(raises=raised))
    for call in (
        lambda: client.list_activities(date(2026, 10, 10), date(2026, 10, 13)),
        lambda: client.download_original(1),
        lambda: client.download_snapshot(date(2026, 10, 13)),
    ):
        with pytest.raises(expected) as info:
            call()
        assert info.value.__cause__ is raised


def test_the_installed_library_is_the_supported_line() -> None:
    from importlib.metadata import version

    major, minor, patch = (int(p) for p in version("garminconnect").split(".")[:3])
    assert (major, minor) == (0, 3) and patch >= 17
    assert hasattr(garminconnect.Garmin, "ActivityDownloadFormat")


def test_the_adapter_module_does_not_import_the_library_at_import_time() -> None:
    source = Path(garmin_connect.__file__).read_text(encoding="utf-8")
    top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not any("garminconnect" in line for line in top_level), top_level
