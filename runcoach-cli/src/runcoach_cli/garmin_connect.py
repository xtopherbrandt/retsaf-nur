"""The real ``GarminClient``: an adapter over the unofficial ``garminconnect`` library.

The library is an optional extra (``runcoach-cli[garmin]``, Python 3.12+), so it is imported
inside functions only: importing this module never needs it. Every library exception leaving here
is translated into the CLI's own (``runcoach_cli.garmin``), and the library's logger is silenced so
its ``logger.exception`` tracebacks never reach a scheduled task's log.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from runcoach_cli.garmin import (
    GarminError,
    GarminUnreachable,
    LoginNeeded,
    NotFound,
    RateLimited,
    TokenNotSaved,
    token_dir,
)

# The library reads this when it is given no token path.
_TOKEN_ENV = "GARMINTOKENS"

# Garmin's answers that mean the token (or login) was refused.
_REFUSED = frozenset({401, 403})
# How the library words a status in its messages, when it attaches no response.
_STATUS_IN_MESSAGE = re.compile(r"client error \((\d{3})\)|API Error (\d{3})")
# A credential login whose every strategy failed: ``Client.login`` raises "All login strategies
# exhausted: <type>: <text>" naming the last strategy's error, with no cause attached.
_ALL_STRATEGIES_FAILED = re.compile(r"All login strategies exhausted: (\w+): ")
# The network error types (``requests``' and ``curl_cffi``'s) that mean no strategy got a reply.
_NETWORK_ERROR_NAMES = frozenset({"ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "DNSError"})
# The library's authentication errors, raised with no cause, for a 200 whose JSON is not the
# profile or the settings: Garmin replied and refused nothing, so they are not a login.
_NOT_THE_PROFILE = frozenset({"Invalid profile data found", "Invalid user settings found"})


def _silence_library_logger() -> None:
    """Stop the ``garminconnect`` logger (and its children) from reaching any handler."""
    lib_logger = logging.getLogger("garminconnect")
    if not any(isinstance(h, logging.NullHandler) for h in lib_logger.handlers):
        lib_logger.addHandler(logging.NullHandler())
    lib_logger.propagate = False


_silence_library_logger()


def _has_network_cause(exc: BaseException) -> bool:
    """True when a ``requests`` connection error or timeout is in ``exc``'s cause chain."""
    import requests

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, requests.ConnectionError | requests.Timeout):
            return True
        current = current.__cause__ or current.__context__
    return False


def _no_login_strategy_got_a_reply(exc: BaseException) -> bool:
    """True when a credential login failed because every strategy hit a network error.

    ``Client.login`` names only the last strategy's error, as text, and attaches no cause, so the
    error's type name is read from the message: a ``requests`` or ``curl_cffi`` connection error,
    timeout or name-resolution error means no reply. Any other type (the library's own error for
    a status or a page it could not use) means Garmin replied, and keeps the usual mapping.
    """
    match = _ALL_STRATEGIES_FAILED.search(str(exc))
    return match is not None and match.group(1) in _NETWORK_ERROR_NAMES


def _status_from_message(exc: BaseException) -> int | None:
    """The status in the library's own wording: ``client error (NNN)`` or ``API Error NNN``."""
    match = _STATUS_IN_MESSAGE.search(str(exc))
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


def _status_of(exc: BaseException) -> int | None:
    """The HTTP error status behind a library exception, or ``None`` when there was no reply.

    The installed library attaches no ``response`` to the errors ``Client._run_request`` raises; the
    status is only in the message (``API Error 403``, ``Download client error (403): ...``). The
    library's own parser reads it when the installed package has one, else the message is parsed
    here. A network error in the cause chain means there was no reply, whatever its text holds (a
    Windows ``[WinError 10061]`` reads as "Error 100" to the library's parser), and only a 4xx or
    5xx counts.
    """
    if _has_network_cause(exc):
        return None
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(status, int):
        try:
            from garminconnect import _extract_status_code
        except ImportError:
            status = _status_from_message(exc)
        else:
            status = _extract_status_code(exc)
    if isinstance(status, int) and not isinstance(status, bool) and 400 <= status <= 599:
        return status
    return None


def _translate_authentication_error(exc: Exception, message: str) -> GarminError:
    """Map the library's authentication error by what caused it.

    Loading a saved token (``Garmin.login(path)``) fetches the profile with the undecorated
    ``Client.connectapi`` and, after its own retries, raises ``GarminConnectAuthenticationError
    ("Failed to retrieve social profile")`` from the request's last error, whatever that was. So:
    a network error in the chain is ``GarminUnreachable``; a cause with an HTTP status of 401 or
    403 needs a login; a 429 is ``RateLimited``; any other status, or another cause that is not the
    library's own authentication error (a reply that is not the profile), is a plain
    ``GarminError``. An authentication error with no cause needs a login (no saved token, no
    credentials), except the library's "Invalid profile data found" and "Invalid user settings
    found": Garmin answered 200 with JSON that is not the profile or the settings, so those are a
    plain ``GarminError`` too.
    """
    from garminconnect import GarminConnectAuthenticationError

    cause = exc.__cause__
    if cause is None:
        if message in _NOT_THE_PROFILE:
            return GarminError(message)
        return LoginNeeded(message)
    detail = f"{message} ({str(cause) or type(cause).__name__})"
    if _has_network_cause(exc):
        return GarminUnreachable(detail)
    status = _status_of(cause)
    if status in _REFUSED:
        return LoginNeeded(message)
    if status == 429:
        return RateLimited(detail)
    if status is None and isinstance(cause, GarminConnectAuthenticationError):
        return LoginNeeded(message)
    return GarminError(detail)


def translate_error(exc: Exception) -> GarminError:
    """Map a library exception to the CLI's own (the F022 reference, sections 5 and 6).

    An authentication error is mapped by its cause (``_translate_authentication_error``). A
    rate-limit error or a 429 is ``RateLimited``; a not-found error or a 404 is ``NotFound``; an
    HTTP 401 or 403 needs a login (``LoginNeeded``); any other HTTP 4xx or 5xx (a 5xx after the
    library's own retries) is a plain ``GarminError``, so a download with it is ``failed`` and the
    sync goes on. ``GarminUnreachable`` (a stop) only when a network error (connection refused,
    network down, timeout) is in the cause chain: an error with no HTTP status and no network
    cause (a reply that is not JSON, a pagination limit) means Garmin replied, so it is a plain
    ``GarminError``, as is anything else. A credential login whose every strategy failed with a
    network error (``_no_login_strategy_got_a_reply``) is ``GarminUnreachable`` too, though the
    library attaches no cause to it.
    """
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectNotFoundError,
        GarminConnectTooManyRequestsError,
    )

    message = str(exc) or type(exc).__name__
    if isinstance(exc, GarminConnectAuthenticationError):
        return _translate_authentication_error(exc, message)
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return RateLimited(message)
    if isinstance(exc, GarminConnectNotFoundError):
        return NotFound(message)
    if isinstance(exc, GarminConnectConnectionError):
        if _has_network_cause(exc) or _no_login_strategy_got_a_reply(exc):
            return GarminUnreachable(message)
        status = _status_of(exc)
        if status in _REFUSED:
            return LoginNeeded(message)
        if status == 429:
            return RateLimited(message)
        if status == 404:
            return NotFound(message)
        return GarminError(message)
    return GarminError(message)


def _call(func: Callable[..., Any], *args: Any) -> Any:
    try:
        return func(*args)
    except GarminError:
        raise
    except Exception as exc:
        raise translate_error(exc) from exc


class GarminConnectClient:
    """``GarminClient`` over a logged-in ``garminconnect.Garmin``."""

    def __init__(self, garmin: Any) -> None:
        self._garmin = garmin

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        return _call(self._garmin.get_activities_by_date, start.isoformat(), end.isoformat())

    def download_original(self, activity_id: int) -> bytes:
        from garminconnect import Garmin

        return _call(
            self._garmin.download_activity, str(activity_id), Garmin.ActivityDownloadFormat.ORIGINAL
        )

    def download_snapshot(self, day: date) -> bytes:
        return _call(self._garmin.download_health_snapshot, day.isoformat())


def open_client() -> GarminConnectClient:
    """Load the saved token from ``token_dir()`` and return a client; never prompts.

    The path is always passed: given None, the library would read the ``GARMINTOKENS``
    environment variable instead. With no saved token (or one Garmin refuses) and no credentials,
    the library raises its authentication error, which surfaces here as ``LoginNeeded``.
    """
    _silence_library_logger()
    from garminconnect import Garmin

    garmin = Garmin()
    _call(garmin.login, str(token_dir()))
    return GarminConnectClient(garmin)


@contextmanager
def _without_token_env() -> Iterator[None]:
    saved = os.environ.pop(_TOKEN_ENV, None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ[_TOKEN_ENV] = saved


def login(email: str, password: str, prompt_mfa: Callable[[], str]) -> None:
    """Log in afresh with ``email`` and ``password`` and save the token to ``token_dir()``.

    The library logs in with no token store (and with ``GARMINTOKENS`` hidden for the call, so it
    cannot load a token from there instead); only after it succeeds is the token written, so a
    failed login keeps the old token. A token that cannot be written after Garmin accepted the
    login raises ``TokenNotSaved``. ``prompt_mfa`` is called if Garmin asks for a code. The token
    file holds only the tokens the library serialises, never the email or password.
    """
    _silence_library_logger()
    from garminconnect import Garmin

    garmin = Garmin(email, password, prompt_mfa=prompt_mfa)
    with _without_token_env():
        _call(garmin.login, None)
    try:
        garmin.client.dump(str(token_dir()))
    except Exception as exc:
        raise TokenNotSaved(str(exc) or type(exc).__name__) from exc
