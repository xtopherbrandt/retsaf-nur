"""The CLI's port to Garmin Connect: the client interface, its exceptions and the token directory.

Nothing here imports the Garmin library. The real adapter lives in ``garmin_connect`` (installed
with the ``garmin`` extra); tests and the sync command depend only on this module, so a fake that
serves zips built from corpus fixtures stands in for Garmin wherever the network would be.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from runcoach_cli import config


class GarminError(Exception):
    """Base of every Garmin failure the CLI reports; also raised for an unclassified one.

    ``status`` is the HTTP status Garmin answered with, when the adapter read one, else ``None``;
    sync reads it to decide whether a failed download is retried by later runs.
    """

    def __init__(self, *args: object, status: int | None = None) -> None:
        super().__init__(*args)
        self.status = status


class LoginNeeded(GarminError):
    """No usable token: none saved, or Garmin refused it (401 or 403). Run ``runcoach sync login``."""


class RateLimited(GarminError):
    """Garmin answered 429. The library never retries it, and neither does the CLI."""


class GarminUnreachable(GarminError):
    """No HTTP reply at all: network down, connection refused or a timeout (after the library's retries).

    Only a network error in the cause chain is this, also while the saved token is loaded, or a
    ``sync login`` whose every login attempt failed with a network error (the library names that
    error only in its message, with no cause). A reply
    Garmin made is never this: a 4xx or 5xx other than 401, 403, 404 and 429 is a plain
    ``GarminError``, so a download that gets one is ``failed`` and the sync goes on, and so is a
    reply with no status (a body that is not JSON).
    """


class NotFound(GarminError):
    """Garmin answered 404 for the requested activity or day."""


class TokenNotSaved(GarminError):
    """Garmin accepted a login, but the token could not be written to ``token_dir()``.

    The library writes a temporary file and replaces the token file with it, so a failed write
    leaves any saved token as it was.
    """


@runtime_checkable
class GarminClient(Protocol):
    """The three reads sync makes. Each raises only ``GarminError`` and its subclasses."""

    def list_activities(self, start: date, end: date) -> list[dict[str, Any]]:
        """Activity summaries whose start falls from ``start`` to ``end``, both inclusive."""
        ...

    def download_original(self, activity_id: int) -> bytes:
        """The activity's ORIGINAL download: a zip holding the file the watch uploaded."""
        ...

    def download_snapshot(self, day: date) -> bytes:
        """The zip of ``day``'s Health Snapshot download (that calendar day's device uploads)."""
        ...


def token_dir() -> Path:
    """The Garmin token directory: ``garmin`` beside the CLI config file (``~/.runcoach/garmin``).

    Resolved from ``config.CONFIG_PATH`` at each call, so whatever points the config at a scratch
    home (the CLI tests' autouse fixture, a demo) moves the token with it. Nothing is created here.

    The library writes the token file owner-only with ``chmod``; on Windows ``chmod`` does not
    restrict who can read the file, so the token is protected only by the user profile's own
    permissions. The token grants access to the Garmin account until it expires or is revoked.
    """
    return config.CONFIG_PATH.parent / "garmin"
