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


def _from_network(exc: Exception) -> Exception:
    # What the library does with a ``requests`` network error: wraps it, with the raw error as cause.
    exc.__cause__ = requests.ConnectionError(REFUSED_CONNECTION)
    return exc


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
        # The credential path's own wrapping (``Garmin.login`` with an email and password, as
        # ``runcoach sync login`` calls it): "Login failed: <the request's error>". Loading a saved
        # token (``runcoach sync``) never produces this shape; its rows are the open_client test.
        (lambda: GarminConnectConnectionError("Login failed: API Error 403"), garmin.LoginNeeded),
        (lambda: GarminConnectConnectionError("Login failed: API Error 401"), garmin.LoginNeeded),
        (lambda: GarminConnectConnectionError("Login failed: API Error 429"), garmin.RateLimited),
        (lambda: GarminConnectConnectionError("Login failed: API Error 500"), garmin.GarminError),
        # Garmin replied, so "could not reach" would be false: no status and no network cause.
        (lambda: GarminConnectConnectionError("Pagination exceeded 1000 requests"), garmin.GarminError),
    ],
    ids=["auth", "toomany", "notfound", "other-library-error", "login-path-403", "login-path-401",
         "login-path-429", "login-path-500", "no-status"],
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
    ("make", "expected"),
    [
        (lambda: GarminConnectConnectionError("Download client error (403): API Error 403"), garmin.LoginNeeded),
        (lambda: GarminConnectConnectionError("Login failed: API Error 403"), garmin.LoginNeeded),
        (lambda: GarminConnectConnectionError("Download HTTP error: API Error 500"), garmin.GarminError),
        (lambda: GarminConnectConnectionError("Download client error (410): x"), garmin.GarminError),
        (
            lambda: _from_network(GarminConnectConnectionError("Connection error: " + REFUSED_CONNECTION)),
            garmin.GarminUnreachable,
        ),
        # The saved-token load's wrapping: an authentication error raised from the request's error.
        (lambda: _load_error(GarminConnectConnectionError("API Error 503")), garmin.GarminError),
        (lambda: _load_error(GarminConnectConnectionError("API Error 429")), garmin.RateLimited),
        (lambda: _load_error(GarminConnectConnectionError("API Error 403")), garmin.LoginNeeded),
        # The library's own authentication error as the cause (a token it cannot use) needs a login.
        (lambda: _load_error(GarminConnectAuthenticationError("No DI refresh token available")), garmin.LoginNeeded),
        # A reply that is not the profile (no status, no network): Garmin answered, not a login.
        (lambda: _load_error(ValueError("not json")), garmin.GarminError),
    ],
    ids=["client403", "login-path-403", "http500", "client410", "refused", "load503", "load429", "load403",
         "load-auth-cause", "load-not-json"],
)
def test_the_status_is_read_from_the_message_without_the_library_parser(
    monkeypatch, make, expected
) -> None:
    # A later library line may drop its private parser: the adapter's own reading still holds.
    monkeypatch.delattr(garminconnect, "_extract_status_code")
    assert type(garmin_connect.translate_error(make())) is expected


def _load_error(cause: Exception) -> GarminConnectAuthenticationError:
    # How ``Garmin._load_social_profile`` reports a failed request (no response attached).
    try:
        raise GarminConnectAuthenticationError("Failed to retrieve social profile") from cause
    except GarminConnectAuthenticationError as exc:
        return exc


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (401, garmin.LoginNeeded),
        (403, garmin.LoginNeeded),
        (429, garmin.RateLimited),
        (404, garmin.GarminError),
        (500, garmin.GarminError),
        (503, garmin.GarminError),
        # A 200 whose body is not the profile: Garmin replied, so neither a login nor unreachable.
        (200, garmin.GarminError),
        (requests.ConnectionError(REFUSED_CONNECTION), garmin.GarminUnreachable),
        (requests.Timeout("Read timed out. (read timeout=15)"), garmin.GarminUnreachable),
    ],
    ids=["401", "403", "429", "404", "500", "503", "200-not-json", "refused", "timeout"],
)
def test_loading_the_saved_token_maps_each_answer_per_the_ruling(
    saved_token_session, answer, expected
) -> None:
    # The user's ruling of 2026-10-13: offline at sync start is "could not reach Garmin Connect",
    # a status other than 401/403 is not "login needed".
    seen = saved_token_session(answer)
    with pytest.raises(garmin.GarminError) as info:
        garmin_connect.open_client()
    assert type(info.value) is expected, (info.value, info.value.__cause__, info.value.__cause__.__cause__)
    assert isinstance(info.value.__cause__, GarminConnectAuthenticationError)
    assert seen and all("socialProfile" in url for url in seen), seen


def _profile_then_settings(profile: object, settings: object):
    # The saved-token load fetches the social profile, then the user settings.
    def answer(url: str) -> object:
        return profile if "socialProfile" in url else settings

    return answer


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (["not", "the", "profile"], "Invalid profile data found"),
        (_profile_then_settings({"displayName": "runner"}, {"preferences": {}}), "Invalid user settings found"),
    ],
    ids=["profile-is-a-list", "settings-without-userdata"],
)
def test_a_saved_token_load_answered_with_json_that_is_not_the_profile_is_a_plain_garmin_error(
    saved_token_session, answer, says
) -> None:
    # Garmin answered 200 with JSON, but not the profile or the settings: the library raises its
    # authentication error with no cause. Garmin replied and refused nothing, so not "login needed".
    seen = saved_token_session(answer)
    with pytest.raises(garmin.GarminError) as info:
        garmin_connect.open_client()
    assert type(info.value) is garmin.GarminError, (info.value, info.value.__cause__)
    assert says in str(info.value)
    assert isinstance(info.value.__cause__, GarminConnectAuthenticationError)
    assert info.value.__cause__.__cause__ is None
    assert seen and any("socialProfile" in url for url in seen), seen


def test_saved_token_session_refuses_curl_cffi(saved_token_session) -> None:
    # The login strategies and the token refresh post through curl_cffi, not requests.
    cffi_requests = pytest.importorskip("curl_cffi.requests")
    from curl_cffi.requests.exceptions import ConnectionError as CffiConnectionError

    seen = saved_token_session(401)
    with pytest.raises(CffiConnectionError):
        cffi_requests.post("https://diauth.garmin.com/di-oauth2-service/oauth/token")
    with pytest.raises(CffiConnectionError):
        cffi_requests.Session(impersonate="chrome").get("https://sso.garmin.com/portal/sso/en-US/sign-in")
    assert seen == [
        "https://diauth.garmin.com/di-oauth2-service/oauth/token",
        "https://sso.garmin.com/portal/sso/en-US/sign-in",
    ]


def _all_strategies_failed(last: str) -> GarminConnectConnectionError:
    # How a credential login reports that every strategy failed: ``Client.login`` raises "All login
    # strategies exhausted: <type>: <text>" with no cause, and ``Garmin.login`` wraps it.
    inner = GarminConnectConnectionError("All login strategies exhausted: " + last)
    try:
        raise GarminConnectConnectionError(f"Login failed: {inner}") from inner
    except GarminConnectConnectionError as exc:
        return exc


@pytest.mark.parametrize(
    ("last", "expected"),
    [
        ("ConnectionError: " + REFUSED_CONNECTION, garmin.GarminUnreachable),
        ("ConnectTimeout: Connection to sso.garmin.com timed out.", garmin.GarminUnreachable),
        ("ReadTimeout: Read timed out. (read timeout=30)", garmin.GarminUnreachable),
        ("Timeout: Failed to perform, curl: (28) Operation timed out", garmin.GarminUnreachable),
        ("DNSError: Failed to perform, curl: (6) Could not resolve host: sso.garmin.com", garmin.GarminUnreachable),
        # A strategy that got a reply keeps today's mapping.
        ("GarminConnectConnectionError: Portal login failed (non-JSON): HTTP 500", garmin.GarminError),
        ("GarminConnectConnectionError: portal+requests: token rejected by API tier", garmin.GarminError),
        ("ValueError: ConnectionError: not a network error", garmin.GarminError),
        ("no strategies ran", garmin.GarminError),
    ],
    ids=["connection-error", "connect-timeout", "read-timeout", "timeout", "dns", "http-500",
         "token-rejected", "other-type", "none-ran"],
)
def test_a_login_whose_strategies_all_failed_is_unreachable_only_for_a_network_error(last, expected) -> None:
    assert type(garmin_connect.translate_error(_all_strategies_failed(last))) is expected


def test_a_network_cause_has_no_status_whatever_its_text(library_garmin) -> None:
    # A network error whose text happens to read like a status is still "no reply".
    lib, _ = library_garmin(_answer_raise(requests.ConnectionError("upstream said API Error 403")))
    with pytest.raises(garmin.GarminUnreachable):
        garmin_connect.GarminConnectClient(lib).download_snapshot(date(2026, 10, 12))


def test_a_list_reply_that_is_not_json_is_a_plain_garmin_error(library_garmin) -> None:
    # A 200 whose body is a web page: the library wraps the JSON error as "Connection error: ...",
    # but Garmin replied, so the list call stops with "Garmin: <reason>", not "could not reach".
    lib, session = library_garmin(_answer_body(b"<html>maintenance</html>"))
    with pytest.raises(garmin.GarminError) as info:
        garmin_connect.GarminConnectClient(lib).list_activities(date(2026, 10, 10), date(2026, 10, 13))
    assert type(info.value) is garmin.GarminError, (info.value, info.value.__cause__)
    assert isinstance(info.value.__cause__, GarminConnectConnectionError)
    assert session.requests


def _answer_body(body: bytes):
    def answer(method, url, params):
        return body

    return answer


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


def test_a_successful_login_replaces_an_older_saved_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "decoy-tokens"))
    token_file = garmin.token_dir() / "garmin_tokens.json"
    token_file.parent.mkdir(parents=True)
    token_file.write_bytes(b'{"di_token": "old"}')
    calls: list = []
    _record_login(monkeypatch, calls)

    garmin_connect.login(SENTINEL_EMAIL, SENTINEL_PASSWORD, lambda: "123456")

    assert len(calls) == 1
    data = token_file.read_bytes()
    assert b"old" not in data
    assert b"di-token-value" in data and b"di-refresh-value" in data
    assert [p.name for p in token_file.parent.iterdir()] == ["garmin_tokens.json"]


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
        (
            _from_network(GarminConnectConnectionError("Connection error: " + REFUSED_CONNECTION)),
            garmin.GarminUnreachable,
        ),
        (GarminConnectConnectionError("Pagination exceeded 1000 requests"), garmin.GarminError),
    ],
    ids=["429", "auth", "404", "503", "network", "no-status"],
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
        assert type(info.value) is expected
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
