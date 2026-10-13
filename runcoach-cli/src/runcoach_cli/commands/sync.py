"""``runcoach sync``: fetch new runs and snapshots from Garmin Connect (F022).

This module holds the ``sync`` sub-app and its ``login`` and ``logout`` commands.

- ``sync [--since YYYY-MM-DD]`` works out a date range and uploads what Garmin holds for it
  (the F022 reference, sections 2 and 3). The range starts one day before the date, in the
  computer's zone (``sessions.LOCAL_ZONE``), of the newest stored run or recognised resting
  capture, found by walking ``GET /sessions`` newest first; it starts 29 days before today when
  there is none, and ends today (the ``today`` seam). A newest date after today is replaced by
  today, with a warning naming the session. ``--since`` replaces the start; one after today is a
  usage error. One activity-list call covers the range; then, day by day oldest first, the day's
  Health Snapshot zip and the day's running-family and ``other`` activities (by local start, then
  id) are downloaded, and every entry of every zip is read in memory and uploaded with
  ``runs_and_captures_only`` set. A manual activity is never downloaded (``no_fit_original``).
  Each item's raw upload reply is recorded in an ``ItemResult``. ``sync`` never reads stdin.

- ``sync login`` asks for the email, the password (not echoed) and, only when Garmin asks for one,
  the MFA code, through the module-level prompt seams ``ask_text`` and ``ask_secret`` (tests replace
  them). It calls the adapter's ``login`` (``runcoach_cli.garmin_connect.login``), which saves the
  token only after Garmin accepts the login, so a refused login keeps any old token. The email and
  password are never written anywhere.
- ``sync logout`` deletes the token file under ``garmin.token_dir()``. It never imports the Garmin
  library, so it works without the ``garmin`` extra. Deleting the file does not revoke the token
  at Garmin.
- ``sync`` and ``sync login`` need the unofficial ``garminconnect`` library, an optional extra
  (Python 3.12+). Without it they print the install command and exit 1. No other command
  imports the library, so every other command works without the extra.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import ModuleType
from typing import Any

import httpx
import typer

from runcoach_cli import api_client, config, garmin
from runcoach_cli.commands import sessions
from runcoach_cli.commands.profile import _fail
from runcoach_cli.commands.sessions import _write_utf8

# The file the library writes into the token directory (``Client.dump``).
TOKEN_FILE_NAME = "garmin_tokens.json"

INSTALL_COMMAND = "uv sync --all-packages --all-groups --extra garmin"
_LIBRARY = "garminconnect"
_MIN_PYTHON = (3, 12)

HELP = (
    "Fetch new runs and Health Snapshots from Garmin Connect and upload them to the API.\n\n"
    "This uses the unofficial garminconnect library, not an API Garmin offers: it may break when "
    "Garmin changes its login, and its use is at the account holder's own risk under Garmin's "
    "terms.\n\n"
    "`runcoach sync login` keeps a token on disk under the runcoach config folder (garmin/). On "
    "Windows that file is not protected by file permissions, only by your user profile; anyone who "
    "can read it can use the Garmin account until the token expires. `runcoach sync logout` "
    "deletes it (it does not revoke it at Garmin)."
)

sync_app = typer.Typer(help=HELP, invoke_without_command=True)


def ask_text(prompt: str) -> str:
    """Prompt for a visible line of text (the email, the MFA code)."""
    return typer.prompt(prompt)


def ask_secret(prompt: str) -> str:
    """Prompt for a secret without echoing it (the password)."""
    return typer.prompt(prompt, hide_input=True)


def _python_version() -> tuple[int, int]:
    return sys.version_info[:2]


def _library_missing() -> bool:
    """True when ``garminconnect`` cannot be imported. Looks it up without importing it."""
    try:
        return importlib.util.find_spec(_LIBRARY) is None
    except (ImportError, ValueError):
        return True


def _require_library() -> None:
    """Exit 1 with the install command when the ``garmin`` extra is not installed."""
    if not _library_missing():
        return
    message = f"the Garmin library is not installed. Install it with `{INSTALL_COMMAND}`."
    if _python_version() < _MIN_PYTHON:
        major, minor = _MIN_PYTHON
        message += (
            f" It needs Python {major}.{minor} or later; this is Python "
            f"{'.'.join(str(part) for part in _python_version())}."
        )
    raise _fail(message)


def _adapter() -> ModuleType:
    """The real Garmin adapter, imported only when a command needs Garmin."""
    from runcoach_cli import garmin_connect

    return garmin_connect


# The range walk's page size (reference section 2).
RANGE_PAGE_SIZE = 50
# With no stored run or capture, the range covers today and the 29 days before it.
EMPTY_STORE_DAYS = 30
# A stored session counts for the range when it is a run or a recognised resting capture.
RANGE_SPORT = "running"
RANGE_TAGS = frozenset({"resting_hrv_check", "health_snapshot"})
# A listed activity is downloaded when its typeId or parentTypeId is the running family's, or its
# typeKey is ``other`` (strap checks; F020's rule on the server decides what is kept).
RUNNING_TYPE_ID = 1
OTHER_TYPE_KEY = "other"

LOGIN_NEEDED = "Garmin login needed: run `runcoach sync login`"


def today() -> date:
    """Today's date on this computer (a seam the tests fix)."""
    return datetime.now().astimezone().date()


class SyncStop(Exception):
    """The sync cannot go on; ``str(exc)`` is the message to print."""


@dataclass
class ItemResult:
    """One thing sync handled: an uploaded zip entry, or an activity it did not upload.

    ``response`` is the raw ``POST /sessions`` reply for an upload, else ``None``; ``outcome`` is
    set when the item was decided without an upload (``no_fit_original`` or ``failed``).
    """

    day: date | None
    item: str
    activity_id: int | None = None
    response: httpx.Response | None = None
    outcome: str | None = None
    detail: str = ""


def _parse_since(value: str, end: date) -> date:
    try:
        since = date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(
            f"{value!r} is not a date in YYYY-MM-DD form", param_hint="'--since'"
        ) from None
    if len(value) != 10 or value[4] != "-":
        raise typer.BadParameter(f"{value!r} is not a date in YYYY-MM-DD form", param_hint="'--since'")
    if since > end:
        raise typer.BadParameter(f"{value} is after today ({end.isoformat()})", param_hint="'--since'")
    return since


def _range_page(api_url: str, cursor: str | None) -> dict[str, Any]:
    """One ``GET /sessions`` page for the range walk, or raise ``SyncStop``."""
    try:
        resp = api_client.list_sessions(api_url, since=cursor, limit=RANGE_PAGE_SIZE)
    except api_client.ApiUnreachableError:
        raise SyncStop(
            f"could not reach API at {api_url}. Is it running? Start it with `runcoach-api`."
        ) from None
    if resp.status_code != 200:
        raise SyncStop(
            f"API returned {resp.status_code} while finding the newest stored session: {resp.text}"
        )
    try:
        body = resp.json()
    except json.JSONDecodeError as exc:
        raise SyncStop(f"the API's response could not be understood: {exc}") from None
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise SyncStop("the API's response could not be understood: the page has no items list")
    return body


def _counts_for_range(row: Any) -> bool:
    return isinstance(row, dict) and (
        row.get("sport") == RANGE_SPORT or row.get("activity_tag") in RANGE_TAGS
    )


def _local_date(start_time: Any) -> date:
    """The date of a stored (UTC) ISO start time in the computer's zone, as ``runcoach sessions`` prints it."""
    when = datetime.fromisoformat(start_time)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(sessions.LOCAL_ZONE).date()


def _newest_stored(api_url: str) -> tuple[date, dict[str, Any]] | None:
    """The local date and row of the newest stored run or recognised capture, or ``None``."""
    cursor: str | None = None
    while True:
        body = _range_page(api_url, cursor)
        for row in body["items"]:
            if _counts_for_range(row):
                try:
                    return _local_date(row["start_time"]), row
                except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
                    raise SyncStop(
                        f"the API's response could not be understood: bad start_time {exc}"
                    ) from None
        next_cursor = body.get("next_cursor")
        if next_cursor is None:
            return None
        if not isinstance(next_cursor, str) or next_cursor == cursor:
            raise SyncStop(
                "the API's response could not be understood: next_cursor repeats the page just read"
            )
        cursor = next_cursor


def find_range(api_url: str, since: date | None, end: date) -> tuple[date, str | None]:
    """``(start, warning)`` for a sync ending on ``end`` (reference section 2)."""
    newest = _newest_stored(api_url)
    warning = None
    if newest is None:
        start = end - timedelta(days=EMPTY_STORE_DAYS - 1)
    else:
        newest_day, row = newest
        if newest_day > end:
            label = row.get("name") or row.get("sport")
            warning = (
                f"Warning: the newest stored session, {label} ({row.get('session_id')}), is dated "
                f"{newest_day.isoformat()}, after today; the range starts from today instead."
            )
            newest_day = end
        start = newest_day - timedelta(days=1)
    if since is not None:
        start = since
    return start, warning


def _is_fetched_type(activity_type: dict[str, Any]) -> bool:
    return (
        activity_type.get("typeId") == RUNNING_TYPE_ID
        or activity_type.get("parentTypeId") == RUNNING_TYPE_ID
        or activity_type.get("typeKey") == OTHER_TYPE_KEY
    )


def _plan_activities(
    listed: Any, start: date, end: date, results: list[ItemResult]
) -> dict[date, list[tuple[datetime, int, dict[str, Any]]]]:
    """The fetched-type activities in the range, by day, each day's sorted by local start then id.

    An activity whose summary cannot be read is recorded as ``failed``, naming it.
    """
    by_day: dict[date, list[tuple[datetime, int, dict[str, Any]]]] = {}
    for summary in listed if isinstance(listed, list) else []:
        try:
            activity_type = summary["activityType"]
            if not isinstance(activity_type, dict):
                raise TypeError("activityType is not an object")
            if not _is_fetched_type(activity_type):
                continue
            activity_id = summary["activityId"]
            if not isinstance(activity_id, int) or isinstance(activity_id, bool):
                raise TypeError(f"activityId {activity_id!r} is not a number")
            local = datetime.fromisoformat(summary["startTimeLocal"])
        except (KeyError, TypeError, ValueError) as exc:
            results.append(
                ItemResult(
                    day=None,
                    item=f"activity {_describe(summary)}",
                    outcome="failed",
                    detail=f"unreadable summary: {exc}",
                )
            )
            continue
        day = local.date()
        if start <= day <= end:
            by_day.setdefault(day, []).append((local, activity_id, summary))
    for items in by_day.values():
        items.sort(key=lambda entry: (entry[0], entry[1]))
    return by_day


def _describe(summary: Any) -> str:
    if not isinstance(summary, dict):
        return repr(summary)[:80]
    name = summary.get("activityName")
    activity_id = summary.get("activityId")
    return f"{activity_id} ({name})" if name is not None else str(activity_id)


def _upload_zip(
    api_url: str, day: date, label: str, data: bytes, activity_id: int | None, results: list[ItemResult]
) -> None:
    """Upload every entry of the zip ``data``, read in memory, in archive order."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        results.append(
            ItemResult(
                day=day, item=label, activity_id=activity_id, outcome="failed", detail=f"not a zip: {exc}"
            )
        )
        return
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            item = f"{label}: {info.filename}"
            try:
                entry = archive.read(info)
            except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError) as exc:
                results.append(
                    ItemResult(day=day, item=item, activity_id=activity_id, outcome="failed", detail=str(exc))
                )
                continue
            try:
                resp = api_client.upload_fit_bytes(api_url, entry, runs_and_captures_only=True)
            except api_client.ApiUnreachableError:
                raise SyncStop(
                    f"could not reach API at {api_url}. Start it and run `runcoach sync --since {day.isoformat()}`."
                ) from None
            results.append(ItemResult(day=day, item=item, activity_id=activity_id, response=resp))


def _garmin_stop(exc: garmin.GarminError, day: date) -> SyncStop:
    if isinstance(exc, garmin.LoginNeeded):
        return SyncStop(LOGIN_NEEDED)
    return SyncStop(f"Garmin: {exc}. Run `runcoach sync --since {day.isoformat()}` to go on.")


def fetch(
    client: garmin.GarminClient,
    api_url: str,
    start: date,
    end: date,
    results: list[ItemResult] | None = None,
) -> list[ItemResult]:
    """Download and upload everything for ``start`` to ``end`` (reference section 3).

    Appends to ``results`` as it goes (so a ``SyncStop`` leaves what was done in it) and returns it.
    """
    results = [] if results is None else results
    try:
        listed = client.list_activities(start, end)
    except garmin.GarminError as exc:
        raise _garmin_stop(exc, start) from None
    by_day = _plan_activities(listed, start, end, results)
    day = start
    while day <= end:
        try:
            snapshot = client.download_snapshot(day)
        except garmin.NotFound:
            snapshot = b""
        except garmin.GarminError as exc:
            raise _garmin_stop(exc, day) from None
        if snapshot:
            _upload_zip(api_url, day, f"snapshot {day.isoformat()}", snapshot, None, results)
        for _, activity_id, summary in by_day.get(day, []):
            label = f"activity {_describe(summary)}"
            if summary.get("manualActivity") is True:
                results.append(
                    ItemResult(day=day, item=label, activity_id=activity_id, outcome="no_fit_original")
                )
                continue
            try:
                original = client.download_original(activity_id)
            except garmin.NotFound:
                original = b""
            except garmin.GarminError as exc:
                raise _garmin_stop(exc, day) from None
            if not original:
                results.append(
                    ItemResult(day=day, item=label, activity_id=activity_id, outcome="no_fit_original")
                )
                continue
            _upload_zip(api_url, day, label, original, activity_id, results)
        day += timedelta(days=1)
    return results


@sync_app.callback()
def sync(
    ctx: typer.Context,
    since: str | None = typer.Option(
        None,
        "--since",
        metavar="YYYY-MM-DD",
        help="Start the range on this day instead of the day before the newest stored run or capture.",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _require_library()
    end = today()
    since_day = _parse_since(since, end) if since is not None else None
    cfg = config.load_config()
    results: list[ItemResult] = []
    try:
        start, warning = find_range(cfg.api_url, since_day, end)
        _write_utf8(f"From {start.isoformat()} to {end.isoformat()}")
        if warning is not None:
            typer.echo(warning, err=True)
        try:
            client = _adapter().open_client()
        except garmin.GarminError as exc:
            raise _garmin_stop(exc, start) from None
        fetch(client, cfg.api_url, start, end, results)
    except SyncStop as exc:
        raise _fail(str(exc)) from None


@sync_app.command(name="login")
def login() -> None:
    """Log in to Garmin Connect and save the token (replacing any saved one)."""
    _require_library()
    email = ask_text("Garmin email")
    password = ask_secret("Garmin password")
    prompt_mfa: Callable[[], str] = lambda: ask_text("Garmin MFA code")
    try:
        _adapter().login(email, password, prompt_mfa)
    except garmin.GarminError as exc:
        raise _fail(f"Garmin refused the login: {exc}. Any saved token is unchanged.") from None
    _write_utf8(f"Logged in. Token saved in {garmin.token_dir()}")


@sync_app.command(name="logout")
def logout() -> None:
    """Delete the saved Garmin token. This does not revoke it at Garmin."""
    token_file = garmin.token_dir() / TOKEN_FILE_NAME
    if not token_file.exists():
        _write_utf8(f"No Garmin token at {token_file}: nothing to delete.")
        return
    try:
        token_file.unlink()
    except OSError as exc:
        raise _fail(f"could not delete the Garmin token at {token_file}: {exc}") from None
    _write_utf8(f"Deleted the Garmin token at {token_file}. This does not revoke it at Garmin.")
