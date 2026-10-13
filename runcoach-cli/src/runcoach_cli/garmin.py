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
    """Base of every Garmin failure the CLI reports; also raised for an unclassified one."""


class LoginNeeded(GarminError):
    """No usable token: none saved, or Garmin refused it (401 or 403). Run ``runcoach sync login``."""


class RateLimited(GarminError):
    """Garmin answered 429. The library never retries it, and neither does the CLI."""


class GarminUnreachable(GarminError):
    """A network failure or a server error that survived the library's own retries."""


class NotFound(GarminError):
    """Garmin answered 404 for the requested activity or day."""


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
