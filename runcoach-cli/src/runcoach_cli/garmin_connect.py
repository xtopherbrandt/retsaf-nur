"""The real ``GarminClient``: an adapter over the unofficial ``garminconnect`` library.

The library is an optional extra (``runcoach-cli[garmin]``, Python 3.12+), so it is imported
inside functions only: importing this module never needs it. Every library exception leaving here
is translated into the CLI's own (``runcoach_cli.garmin``), and the library's logger is silenced so
its ``logger.exception`` tracebacks never reach a scheduled task's log.
"""

from __future__ import annotations

import logging
import os
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
    token_dir,
)

# The library reads this when it is given no token path.
_TOKEN_ENV = "GARMINTOKENS"

# Garmin's answers that mean the token (or login) was refused.
_REFUSED = frozenset({401, 403})


def _silence_library_logger() -> None:
    """Stop the ``garminconnect`` logger (and its children) from reaching any handler."""
    lib_logger = logging.getLogger("garminconnect")
    if not any(isinstance(h, logging.NullHandler) for h in lib_logger.handlers):
        lib_logger.addHandler(logging.NullHandler())
    lib_logger.propagate = False


_silence_library_logger()


def _status_of(exc: BaseException) -> int | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def translate_error(exc: Exception) -> GarminError:
    """Map a library exception to the CLI's own.

    Authentication errors, and connection errors carrying a 401 or 403 response, need a login;
    a rate-limit error or a 429 response is ``RateLimited``; a not-found error or a 404 response is
    ``NotFound``; any other connection error is ``GarminUnreachable``; anything else is a plain
    ``GarminError``.
    """
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectNotFoundError,
        GarminConnectTooManyRequestsError,
    )

    message = str(exc) or type(exc).__name__
    if isinstance(exc, GarminConnectAuthenticationError):
        return LoginNeeded(message)
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return RateLimited(message)
    if isinstance(exc, GarminConnectNotFoundError):
        return NotFound(message)
    if isinstance(exc, GarminConnectConnectionError):
        status = _status_of(exc)
        if status in _REFUSED:
            return LoginNeeded(message)
        if status == 429:
            return RateLimited(message)
        if status == 404:
            return NotFound(message)
        return GarminUnreachable(message)
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
    failed login keeps the old token. ``prompt_mfa`` is called if Garmin asks for a code. The
    token file holds only the tokens the library serialises, never the email or password.
    """
    _silence_library_logger()
    from garminconnect import Garmin

    garmin = Garmin(email, password, prompt_mfa=prompt_mfa)
    with _without_token_env():
        _call(garmin.login, None)
    _call(garmin.client.dump, str(token_dir()))
