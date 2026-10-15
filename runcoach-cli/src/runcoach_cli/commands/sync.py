"""``runcoach sync``: fetch new runs and snapshots from Garmin Connect (F022), and read race tags (F023).

This module holds the ``sync`` sub-app and its ``tags``, ``login`` and ``logout`` commands.

- ``sync [--since YYYY-MM-DD]`` works out a date range and uploads what Garmin holds for it
  (the F022 reference, sections 2 and 3). The range starts one day before the date, in the
  computer's zone (``sessions.LOCAL_ZONE``), of the newest stored run or recognised resting
  capture, found by walking ``GET /sessions`` newest first; it starts 29 days before today when
  there is none, and ends today (the ``today`` seam). A newest date after today is replaced by
  today, with a warning naming the session. That store rule is one candidate: the start is the
  earliest of it, the day before ``last_completed`` and ``oldest_failed``, read from the sync
  state (``sync-state.json`` beside the CLI config; ``read_state``, ``apply_state``) and written
  atomically at the end of every run that found its range (``next_state``, ``write_state``): a
  run that reaches its end sets ``last_completed`` to it when its start joins on to the previous
  value, a stop leaves it, and ``oldest_failed`` keeps the oldest day still holding a temporary
  failure (a permanent one is reported, not remembered) or, after a stop, a day the stop left
  unfetched. A missing or bad file falls back to the store rule with one line naming it; a date
  after today is read as today. ``--since`` (``YYYY-MM-DD`` only) replaces the start (the state
  does not move it, but is still updated), and then ``GET /sessions`` is not walked; one after
  today is a usage error. One activity-list
  call covers the range; then, day by day oldest first, the day's Health Snapshot zip and the
  day's running-family and ``other`` activities (by local start, then id) are downloaded, and
  every entry of every zip is read in memory and uploaded with
  ``runs_and_captures_only`` set. A manual activity is never downloaded (``no_fit_original``).
  Before the first upload, ``GET /openapi.json`` must list the switch on ``POST /sessions``.
  Each item is recorded in an ``ItemResult`` with its outcome (reference section 5): ``stored``,
  ``already_stored``, ``skipped_sport``, ``non_activity``, ``no_fit_original`` or ``failed``. The
  summary follows the ``From`` line; the exit code is 1 when any item failed. A download that
  Garmin answers with an HTTP error status other than 401, 403, 404 and 429 is ``failed`` and the
  sync goes on. A stop (reference section 6: a login needed, a web page for a download, a rate
  limit, Garmin unreachable (no HTTP reply at all) or the API unreachable, any error on the
  activity list, an API without the switch) prints the summary so far and its message, uploads
  nothing more and exits 1. Downloads are read in memory only, each entry capped at
  ``FIT_ENTRY_CAP`` and only stored or deflated (reference section 7). ``sync`` never reads stdin.
  After uploads that end without a stop (failed items do not prevent it) and after the sync state
  is written, ``sync`` reads race tags as ``sync tags`` does, for the earlier of its start and
  today - 29 days to today: the upload listing is reused when it covers that, else one more
  listing is made; the ``/openapi.json`` the switch check read is checked for ``setRaceTags``
  (read here when nothing was uploaded), so it is read once per run. The tags step prints its
  lines after the summary, never reads or writes the sync state, and a failure of it (as for
  ``sync tags``) exits 1.

- ``sync tags [--since YYYY-MM-DD]`` (default today and the 29 days before it; after today, or the
  parent's ``--since`` given with it, is a usage error) checks that ``/openapi.json`` lists
  ``setRaceTags``, makes one activity-list call for ``[since, today]`` and sends one item per
  fetched-type, non-manual activity whose local start is in the range (once per ``activityId``) to
  ``PUT /race-tags``, in batches of ``RACE_TAGS_BATCH`` oldest first; ``race`` is
  ``eventType.typeKey == "race"``. A summary whose type, ``eventType`` or ``startTimeGMT`` cannot be
  read is skipped and reported, never sent; with nothing to send no ``PUT`` is made. It prints the
  ``From`` line, the counts line and one line per race not stored, mismatched or ambiguous and per
  skipped activity (``tags_report_lines``). It downloads nothing and never reads or writes the sync
  state. A login needed prints the login message; another Garmin error, its reason and the one
  ``runcoach sync tags --since`` command; an API without ``setRaceTags`` sends nothing; a batch the
  API fails or cannot be reached for stops later batches and names the batch's first local day.
  Each exits 1; not stored, mismatch, ambiguous and skipped items do not change the exit code.
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
import lzma
import math
import os
import re
import sys
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
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
WEB_PAGE = (
    "Garmin returned a web page instead of the file (a login or maintenance page): "
    "run `runcoach sync login` if this repeats"
)
OLD_API = "the API does not support runs_and_captures_only: update it"
SWITCH_FIELD = "runs_and_captures_only"

# The most one zip entry may hold to be uploaded: ``POST /sessions``'s own cap; tests lower it.
FIT_ENTRY_CAP = 50 * 1024 * 1024
_CAP_TEXT = "50 MB"
# The only compression methods read: CPython decompresses a whole bzip2 or LZMA entry before
# ``read(n)`` slices it, so ``FIT_ENTRY_CAP`` bounds memory only for stored and deflated entries.
_SAFE_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
# Errors reading a zip or one entry's bytes out of it (the archive import's list): corrupt data
# (a corrupt bzip2 stream raises OSError, a corrupt LZMA stream LZMAError), a bad CRC, an
# unsupported compression method or a cut-off stream (an encrypted entry is refused before it is
# opened); and, as a backstop, running out of memory.
_ENTRY_READ_ERRORS = (
    MemoryError,
    zipfile.BadZipFile,
    zlib.error,
    lzma.LZMAError,
    OSError,
    RuntimeError,
    NotImplementedError,
    EOFError,
    ValueError,
)
# The Garmin errors that end the sync; a ``NotFound`` is never one of them.
_STOPPING = (garmin.LoginNeeded, garmin.RateLimited, garmin.GarminUnreachable)
# A failed reply's detail is cut to this many characters in the summary.
_DETAIL_CHARS = 300
# The API's reply text in the range walk's stop is cut to this many characters.
_RANGE_TEXT_CHARS = 200
# ``--since``'s only accepted form (ASCII digits).
_SINCE_FORM = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

STORED = "stored"
ALREADY_STORED = "already_stored"
SKIPPED_SPORT = "skipped_sport"
NON_ACTIVITY = "non_activity"
NO_FIT_ORIGINAL = "no_fit_original"
FAILED = "failed"

# What a permanent failure's Failed line says before its recover command (reference section 5).
# A later run whose range still covers the day fetches it again; no run goes back for it.
NOT_REVISITED = "later runs do not return for it: it would fail the same way again"
# The 4xx a download can meet that are not a refusal of that file: a login (401, 403), not found
# (404), a timeout (408) and a rate limit (429). Any other 4xx on a download is permanent.
_GARMIN_4XX_NOT_REFUSALS = frozenset({401, 403, 404, 408, 429})
# The upload statuses that reject the file itself, so it fails the same way again: permanent. A 400
# is a file the API finds invalid; a 413 one over a size or decode ceiling. A 5xx (a busy store)
# and any other failed reply are temporary.
_API_PERMANENT_STATUSES = frozenset({400, 413})
# The zip general-purpose flag bit that marks an encrypted entry (read without a password: never).
_ZIP_ENCRYPTED = 0x1


@dataclass(frozen=True)
class _DownloadFailed:
    """A download error that is the item's failure (not a stop): its detail and whether it is
    temporary (``retry``), so the sync state remembers its day."""

    detail: str
    retry: bool


def today() -> date:
    """Today's date on this computer (a seam the tests fix)."""
    return datetime.now().astimezone().date()


class SyncStop(Exception):
    """The sync cannot go on; ``str(exc)`` is the message to print.

    ``stopped_on`` is the day ``fetch`` was on when the stop came (the range's start for a stop on
    the activity list), or ``None`` for a stop outside ``fetch``.
    """

    stopped_on: date | None = None


@dataclass
class ItemResult:
    """One thing sync handled: an uploaded zip entry, or a download it did not upload.

    ``status`` is the HTTP status of the ``POST /sessions`` reply for an upload, else ``None``;
    the reply itself is not kept, so a long sync does not hold every upload's body in memory.
    ``outcome`` is one of the reference section 5 outcomes; ``detail`` says why an item failed, or
    names the sport of a ``skipped_sport`` and the file type of a ``non_activity``. ``name`` is what
    the summary calls the item (the activity, for an entry of an activity's download); ``day`` is
    ``None`` only for an activity summary too broken to place. ``retry`` is False for a permanent
    failure (reference section 2: one that fails the same way on every try), which the sync state
    does not remember and whose Failed line says later runs do not return for it.
    """

    day: date | None
    item: str
    activity_id: int | None = None
    status: int | None = None
    outcome: str | None = None
    detail: str = ""
    name: str = ""
    retry: bool = True

    @property
    def summary_name(self) -> str:
        return self.name or self.item


def _parse_since(value: str, end: date) -> date:
    """``value`` as a date; only ``YYYY-MM-DD`` (``date.fromisoformat`` also takes ``2026-W41-1``)."""
    since = None
    if _SINCE_FORM.fullmatch(value):
        try:
            since = date.fromisoformat(value)
        except ValueError:
            since = None
    if since is None:
        raise typer.BadParameter(f"{value!r} is not a date in YYYY-MM-DD form", param_hint="'--since'")
    if since > end:
        raise typer.BadParameter(f"{value} is after today ({end.isoformat()})", param_hint="'--since'")
    return since


def _range_page(api_url: str, cursor: str | None) -> dict[str, Any]:
    """One ``GET /sessions`` page for the range walk, or raise ``SyncStop``."""
    try:
        resp = api_client.list_sessions(api_url, since=cursor, limit=RANGE_PAGE_SIZE)
    except api_client.ApiUnreachableError:
        raise SyncStop(_api_unreachable(api_url, None)) from None
    if resp.status_code != 200:
        text = sessions._printable(resp.text[:_RANGE_TEXT_CHARS])
        raise SyncStop(f"API returned {resp.status_code} while finding the newest stored session: {text}")
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
    """``(start, warning)`` for a sync ending on ``end`` (reference section 2).

    With ``since``, the start is ``since`` and ``GET /sessions`` is not walked: the walk only finds
    the start that ``since`` replaces, so a future-dated stored session cannot warn.
    """
    if since is not None:
        return since, None
    newest = _newest_stored(api_url)
    warning = None
    if newest is None:
        start = end - timedelta(days=EMPTY_STORE_DAYS - 1)
    else:
        newest_day, row = newest
        if newest_day > end:
            label = sessions._printable(str(row.get("name") or row.get("sport")))
            session_id = sessions._printable(str(row.get("session_id")))
            warning = (
                f"Warning: the newest stored session, {label} ({session_id}), is dated "
                f"{newest_day.isoformat()}, after today; the range starts from today instead."
            )
            newest_day = end
        start = newest_day - timedelta(days=1)
    return start, warning


# The sync state (reference section 2): beside the CLI config, not in the token directory, so
# ``sync logout`` keeps it.
STATE_FILE_NAME = "sync-state.json"
_STATE_KEYS = ("last_completed", "oldest_failed")


@dataclass(frozen=True)
class SyncState:
    """``last_completed``: the end of the last run that reached the end of its range.
    ``oldest_failed``: the oldest day that may still hold a temporary ``failed`` item (after a
    stop, the day the stop came on, when the run had fetched past the previous one)."""

    last_completed: date | None = None
    oldest_failed: date | None = None


def state_path() -> Path:
    """``sync-state.json`` beside the CLI config file, resolved from ``config.CONFIG_PATH`` at each call."""
    return config.CONFIG_PATH.parent / STATE_FILE_NAME


def _state_day(value: Any, end: date) -> date | None:
    """A state value as a date: null, or ``YYYY-MM-DD``, a date after ``end`` read as ``end`` (the
    clock moved back); anything else raises ``ValueError``. Each key is read on its own."""
    if value is None:
        return None
    if not isinstance(value, str) or not _SINCE_FORM.fullmatch(value):
        raise ValueError(f"{value!r} is not null or a YYYY-MM-DD date")
    return min(date.fromisoformat(value), end)


def read_state(end: date) -> tuple[SyncState, str | None]:
    """``(state, line)``: the saved state, or an empty one and the line naming the file that explains why."""
    path = state_path()
    shown = sessions._printable(str(path))
    if not path.exists():
        return SyncState(), (
            f"No sync state at {shown}: the range follows the store alone; it is written at the end of this run."
        )
    try:
        body = json.loads(path.read_bytes().decode("utf-8"))
        if not isinstance(body, dict) or any(key not in body for key in _STATE_KEYS):
            raise ValueError(f"it is not an object holding {' and '.join(_STATE_KEYS)}")
        state = SyncState(*(_state_day(body[key], end) for key in _STATE_KEYS))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        reason = sessions._printable(str(exc)[:_RANGE_TEXT_CHARS])
        return SyncState(), (
            f"Warning: the sync state at {shown} could not be read ({reason}); the range follows "
            "the store alone and the file is rewritten at the end of this run."
        )
    return state, None


def write_state(state: SyncState) -> str | None:
    """Save ``state`` atomically (a temporary file in the same folder, then a replace).

    Returns a warning line when the file cannot be written, else ``None``.
    """
    path = state_path()
    body = {key: (getattr(state, key).isoformat() if getattr(state, key) else None) for key in _STATE_KEYS}
    temp = path.with_name(f".{STATE_FILE_NAME}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(json.dumps(body).encode("utf-8"))
        os.replace(temp, path)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return (
            f"Warning: could not save the sync state at {sessions._printable(str(path))}: "
            f"{sessions._printable(str(exc))}"
        )
    return None


def apply_state(store_start: date, state: SyncState) -> tuple[date, str | None]:
    """``(start, line)``: the earliest of the store rule's start, the day before ``last_completed``
    and ``oldest_failed``, with the line saying why when the state moved the start earlier."""
    start, line = store_start, None
    if state.oldest_failed is not None and state.oldest_failed < start:
        start = state.oldest_failed
        line = f"Starting at {start.isoformat()}: a failed item on that day"
    if state.last_completed is not None:
        day_before = state.last_completed - timedelta(days=1)
        if day_before < start:
            start = day_before
            line = (
                f"Starting at {start.isoformat()}: the last completed sync ended "
                f"{state.last_completed.isoformat()}"
            )
    return start, line


def next_state(
    previous: SyncState, results: list[ItemResult], start: date, end: date, stopped_on: date | None
) -> SyncState:
    """The state after a run of ``start`` to ``end`` (reference section 2).

    ``stopped_on`` is the day a stop ended the run on (``start`` for a stop before any day was
    fetched), or ``None`` for a run that reached ``end``. Only a run that reached ``end`` and
    joined on to what was already complete (no ``last_completed``, or ``start`` on or before the
    day after it) moves ``last_completed``. ``oldest_failed`` is the earliest of this run's
    temporary failures' days; the previous ``oldest_failed`` when it lies before ``start``; and,
    after a stop, the later of it and ``stopped_on``: the run did not fetch the days from
    ``stopped_on`` on, where a failed day newer than the previous ``oldest_failed`` may lie.
    """
    failed = [r.day or start for r in results if r.outcome == FAILED and r.retry]
    old = previous.oldest_failed
    if old is not None:
        if old < start:
            failed.append(old)
        elif stopped_on is not None:
            failed.append(max(old, stopped_on))
    joined = previous.last_completed is None or start <= previous.last_completed + timedelta(days=1)
    return SyncState(
        last_completed=end if stopped_on is None and joined else previous.last_completed,
        oldest_failed=min(failed, default=None),
    )


def _is_fetched_type(activity_type: dict[str, Any]) -> bool:
    return (
        activity_type.get("typeId") == RUNNING_TYPE_ID
        or activity_type.get("parentTypeId") == RUNNING_TYPE_ID
        or activity_type.get("typeKey") == OTHER_TYPE_KEY
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _read_summary(summary: Any) -> tuple[int, datetime, dict[str, Any]]:
    """``(activityId, local start, activityType)`` of a listed summary; raises naming the field.

    A summary lacking any of the three is unreadable whatever its type, so it is never skipped
    silently as an activity of another sport.
    """
    if not isinstance(summary, dict):
        raise TypeError("the summary is not an object")
    activity_type = summary.get("activityType")
    if not isinstance(activity_type, dict):
        raise TypeError(f"activityType {activity_type!r} is missing or not an object")
    type_id = activity_type.get("typeId")
    if not _is_int(type_id):
        raise ValueError(f"activityType.typeId {type_id!r} is missing or not a number")
    activity_id = summary.get("activityId")
    if not _is_int(activity_id):
        raise ValueError(f"activityId {activity_id!r} is missing or not a number")
    start_local = summary.get("startTimeLocal")
    if not isinstance(start_local, str):
        raise TypeError(f"startTimeLocal {start_local!r} is missing or not text")
    try:
        local = datetime.fromisoformat(start_local)
    except ValueError:
        raise ValueError(f"startTimeLocal {start_local!r} is not a date and time") from None
    return activity_id, local, activity_type


def _plan_activities(
    listed: Any, start: date, end: date, results: list[ItemResult]
) -> dict[date, list[tuple[datetime, int, dict[str, Any]]]]:
    """The fetched-type activities in the range, by day, each day's sorted by local start then id.

    An activity whose summary cannot be read is recorded as ``failed`` with no day, naming it; it
    is permanent (not remembered in the sync state), since the next listing returns the same summary.
    """
    if not isinstance(listed, list):
        raise SyncStop(
            f"Garmin's activity list could not be understood: run `runcoach sync --since {start.isoformat()}`"
        )
    by_day: dict[date, list[tuple[datetime, int, dict[str, Any]]]] = {}
    for summary in listed:
        try:
            activity_id, local, activity_type = _read_summary(summary)
        except (TypeError, ValueError) as exc:
            label = f"activity {_describe(summary)}"
            results.append(
                ItemResult(
                    day=None,
                    item=label,
                    outcome=FAILED,
                    detail=f"unreadable summary: {exc}",
                    name=label,
                    retry=False,
                )
            )
            continue
        if not _is_fetched_type(activity_type):
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


def _api_unreachable(api_url: str, day: date | None) -> str:
    recover = "runcoach sync" if day is None else f"runcoach sync --since {day.isoformat()}"
    return f"could not reach API at {api_url}: start it with `runcoach-api`, then run `{recover}`"


def _upload_unanswered(api_url: str, item: str, day: date) -> str:
    """The stop for an upload the API took but did not answer within ``UPLOAD_TIMEOUT``.

    The API is running (it accepted the connection), so this is not "could not reach API"; it may
    still have stored the item, which the next run then reports as already stored.
    """
    seconds = f"{api_client.UPLOAD_TIMEOUT:g}"
    return (
        f"the API at {api_url} did not answer within {seconds} s while storing "
        f"{sessions._printable(item)}; if it stored it, the next run reports it as already stored: "
        f"run `runcoach sync --since {day.isoformat()}`"
    )


def _garmin_stop(exc: garmin.GarminError, day: date) -> SyncStop:
    """The stop for a Garmin error that ends the sync on ``day``."""
    if isinstance(exc, garmin.LoginNeeded):
        return SyncStop(LOGIN_NEEDED)
    reason = _garmin_reason(exc)
    recover = f"runcoach sync --since {day.isoformat()}"
    if isinstance(exc, garmin.RateLimited):
        return SyncStop(f"{reason}: wait, then run `{recover}`")
    return SyncStop(f"{reason}: run `{recover}`")


def _garmin_reason(exc: garmin.GarminError) -> str:
    """What a Garmin error that is not a login stops on, without a recovery command."""
    reason = sessions._printable(str(exc))
    if isinstance(exc, garmin.RateLimited):
        return f"Garmin is limiting requests ({reason})"
    if isinstance(exc, garmin.GarminUnreachable):
        return f"could not reach Garmin Connect ({reason})"
    return f"Garmin: {reason}"


def _looks_like_page(data: bytes) -> bool:
    """True for a body that starts (after a byte-order mark and white space) with ``<``."""
    head = data[:256]
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    return head.lstrip().startswith(b"<")


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _reply_detail(resp: httpx.Response) -> str:
    body = _body(resp)
    detail = body.get("detail") if isinstance(body, dict) else None
    text = detail if isinstance(detail, str) else resp.text
    return f"API returned {resp.status_code}: {text[:_DETAIL_CHARS]}"


def _classify(resp: httpx.Response) -> tuple[str, str, bool]:
    """``(outcome, detail, switch_applied)`` of an upload reply (reference section 5).

    ``switch_applied`` is False for a 201 or 200 whose body lacks ``filter_applied: true``: an API
    that ignored ``runs_and_captures_only``, which stops the sync.
    """
    status = resp.status_code
    if status == 409:
        return ALREADY_STORED, "", True
    if status not in (200, 201):
        return FAILED, _reply_detail(resp), True
    body = _body(resp)
    applied = isinstance(body, dict) and body.get("filter_applied") is True
    if status == 201:
        return STORED, "", applied
    if not applied:
        return FAILED, f"API returned 200 without filter_applied: {resp.text[:_DETAIL_CHARS]}", False
    reason = body.get("reason") if body.get("skipped") is True else None
    if reason == SKIPPED_SPORT:
        return SKIPPED_SPORT, str(body.get("sport")), True
    if reason == NON_ACTIVITY:
        return NON_ACTIVITY, str(body.get("file_type")), True
    return FAILED, f"API returned 200 with an unknown skip reason {reason!r}", True


def _resolve(doc: dict[str, Any], schema: Any) -> Any:
    """``schema`` with local ``$ref``s followed (a bounded walk)."""
    for _ in range(8):
        if not isinstance(schema, dict) or "$ref" not in schema:
            return schema
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return None
        node: Any = doc
        for part in ref[2:].split("/"):
            node = node.get(part) if isinstance(node, dict) else None
        schema = node
    return None


def _find_operation(doc: Any, op_id: str) -> dict[str, Any] | None:
    """The served operation whose ``operationId`` is ``op_id`` (the last one found), or ``None``."""
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        return None
    operation = None
    for item in doc["paths"].values():
        for op in item.values() if isinstance(item, dict) else []:
            if isinstance(op, dict) and op.get("operationId") == op_id:
                operation = op
    return operation


def _lists_switch(doc: Any) -> bool:
    """True when the served ``POST /sessions`` (``ingestSession``) lists the switch as a form field."""
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        return False
    operation = _find_operation(doc, "ingestSession")
    if operation is None:
        sessions_path = doc["paths"].get("/sessions")
        operation = sessions_path.get("post") if isinstance(sessions_path, dict) else None
    try:
        schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    except (KeyError, TypeError):
        return False
    properties = _resolve(doc, schema)
    properties = properties.get("properties") if isinstance(properties, dict) else None
    return isinstance(properties, dict) and SWITCH_FIELD in properties


@dataclass
class ServedDoc:
    """The API's ``/openapi.json`` as one run read it, so it is read at most once per run: ``read``
    is True once the upload switch check has read it, and ``doc`` is the document it read."""

    doc: Any = None
    read: bool = False


class _Uploader:
    """Uploads entries for one sync, checking the API's switch once, before the first upload."""

    def __init__(self, api_url: str, results: list[ItemResult], served: ServedDoc | None = None) -> None:
        self.api_url = api_url
        self.results = results
        self.served = ServedDoc() if served is None else served
        self.checked = False

    def _check_switch(self, day: date) -> None:
        try:
            resp = api_client.get_openapi(self.api_url)
        except api_client.ApiUnreachableError:
            raise SyncStop(_api_unreachable(self.api_url, day)) from None
        doc = _body(resp) if resp.status_code == 200 else None
        if not _lists_switch(doc):
            raise SyncStop(f"{OLD_API} and restart the API")
        self.served.doc, self.served.read = doc, True
        self.checked = True

    def upload(self, day: date, item: str, name: str, activity_id: int | None, data: bytes) -> None:
        if not self.checked:
            self._check_switch(day)
        try:
            resp = api_client.upload_fit_bytes(self.api_url, data, runs_and_captures_only=True)
        except api_client.ApiUnreachableError as exc:
            if isinstance(exc.__cause__, httpx.ReadTimeout):
                raise SyncStop(_upload_unanswered(self.api_url, item, day)) from None
            raise SyncStop(_api_unreachable(self.api_url, day)) from None
        outcome, detail, applied = _classify(resp)
        self.results.append(
            ItemResult(
                day=day,
                item=item,
                activity_id=activity_id,
                status=resp.status_code,
                outcome=outcome,
                detail=detail,
                name=name,
                # A 400 or 413 rejects the file itself; any other failed reply may pass later.
                retry=resp.status_code not in _API_PERMANENT_STATUSES,
            )
        )
        if not applied:
            raise SyncStop(OLD_API)

    def fail(self, day: date, item: str, activity_id: int | None, detail: str, *, retry: bool) -> None:
        self.results.append(
            ItemResult(
                day=day,
                item=item,
                activity_id=activity_id,
                outcome=FAILED,
                detail=detail,
                name=item,
                retry=retry,
            )
        )


def _upload_zip(uploader: _Uploader, day: date, label: str, data: bytes, activity_id: int | None) -> None:
    """Upload every entry of the zip ``data``, read in memory, in archive order.

    A web page stops the sync. Bytes that are not a zip, an activity download holding no file, an
    entry compressed other than stored or deflated (refused before it is opened, since only those
    two are read in bounded memory), an encrypted entry (refused before it is opened), an entry that
    cannot be read and an entry over ``FIT_ENTRY_CAP`` are ``failed``; an empty snapshot zip is zero
    items. Entry names are never used as paths. The compression, encryption and cap failures are
    permanent; the others are temporary, since a broken transfer can cause them (reference
    section 2).
    """
    if _looks_like_page(data):
        raise SyncStop(WEB_PAGE)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except _ENTRY_READ_ERRORS as exc:
        uploader.fail(day, label, activity_id, f"not a zip: {exc}", retry=True)
        return
    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        if not entries and activity_id is not None:
            uploader.fail(day, label, activity_id, "the download holds no file", retry=True)
            return
        for info in entries:
            item = f"{label}: {info.filename}"
            if info.compress_type not in _SAFE_COMPRESSION:
                method = zipfile.compressor_names.get(info.compress_type, f"method {info.compress_type}")
                uploader.fail(
                    day,
                    item,
                    activity_id,
                    f"the entry is compressed with {method}, which sync does not read "
                    "(only stored or deflated entries)",
                    retry=False,
                )
                continue
            if info.flag_bits & _ZIP_ENCRYPTED:
                uploader.fail(
                    day, item, activity_id, "the entry is encrypted, which sync does not read", retry=False
                )
                continue
            try:
                with archive.open(info) as handle:
                    entry = handle.read(FIT_ENTRY_CAP + 1)
            except _ENTRY_READ_ERRORS as exc:
                uploader.fail(day, item, activity_id, str(exc) or type(exc).__name__, retry=True)
                continue
            if len(entry) > FIT_ENTRY_CAP:
                uploader.fail(
                    day, item, activity_id, f"the entry is over {_CAP_TEXT} and was not uploaded", retry=False
                )
                continue
            uploader.upload(day, item, label if activity_id is not None else item, activity_id, entry)


def _garmin_refused(exc: garmin.GarminError) -> bool:
    """True for a download Garmin answered with a 4xx that refuses the file (400, 410): permanent."""
    status = getattr(exc, "status", None)
    return isinstance(status, int) and 400 <= status <= 499 and status not in _GARMIN_4XX_NOT_REFUSALS


def _download(fetch_bytes: Callable[[Any], Any], arg: Any, day: date) -> bytes | _DownloadFailed:
    """The downloaded bytes (``b""`` for a 404), or the failure of a download error that is not a stop.

    A Garmin 4xx refusal is permanent; a 5xx, a 408, an error with no status and a body that is not
    bytes are temporary (reference section 2).
    """
    try:
        data = fetch_bytes(arg)
    except garmin.NotFound:
        return b""
    except _STOPPING as exc:
        raise _garmin_stop(exc, day) from None
    except garmin.GarminError as exc:
        return _DownloadFailed(f"Garmin: {sessions._printable(str(exc))}", retry=not _garmin_refused(exc))
    if data is None:
        return b""
    if not isinstance(data, bytes | bytearray):
        return _DownloadFailed(f"the download is not bytes but {type(data).__name__}", retry=True)
    return bytes(data)


def fetch(
    client: garmin.GarminClient,
    api_url: str,
    start: date,
    end: date,
    results: list[ItemResult] | None = None,
    *,
    listing_out: list[Any] | None = None,
    served: ServedDoc | None = None,
) -> list[ItemResult]:
    """Download and upload everything for ``start`` to ``end`` (reference sections 3, 5 to 7).

    Appends to ``results`` as it goes (so a ``SyncStop`` leaves what was done in it) and returns it.
    A ``SyncStop`` carries the day it stopped on as ``stopped_on``. ``listing_out``, when given,
    receives the raw activity listing (unreadable summaries included), so the race tags step can
    reuse it; ``served`` receives the ``/openapi.json`` the switch check read.
    """
    results = [] if results is None else results
    uploader = _Uploader(api_url, results, served)
    day = start
    try:
        try:
            listed = client.list_activities(start, end)
        except garmin.GarminError as exc:
            raise _garmin_stop(exc, start) from None
        by_day = _plan_activities(listed, start, end, results)
        if listing_out is not None:
            listing_out.extend(listed)
        while day <= end:
            label = f"snapshot {day.isoformat()}"
            snapshot = _download(client.download_snapshot, day, day)
            if isinstance(snapshot, _DownloadFailed):
                uploader.fail(day, label, None, snapshot.detail, retry=snapshot.retry)
            elif snapshot:
                _upload_zip(uploader, day, label, snapshot, None)
            for _, activity_id, summary in by_day.get(day, []):
                label = f"activity {_describe(summary)}"
                if summary.get("manualActivity") is True:
                    results.append(
                        ItemResult(
                            day=day, item=label, activity_id=activity_id, outcome=NO_FIT_ORIGINAL, name=label
                        )
                    )
                    continue
                original = _download(client.download_original, activity_id, day)
                if isinstance(original, _DownloadFailed):
                    uploader.fail(day, label, activity_id, original.detail, retry=original.retry)
                elif not original:
                    results.append(
                        ItemResult(
                            day=day, item=label, activity_id=activity_id, outcome=NO_FIT_ORIGINAL, name=label
                        )
                    )
                else:
                    _upload_zip(uploader, day, label, original, activity_id)
            day += timedelta(days=1)
    except SyncStop as exc:
        exc.stopped_on = day
        raise
    return results


def summary_lines(results: list[ItemResult], start: date) -> list[str]:
    """The summary after the ``From`` line (reference section 5); every name printed safely.

    A failed item with no day (an unreadable activity summary) reads ``day unknown`` and is
    recovered from ``start``, the first day the activity list covered. A permanent failure's line
    says ``NOT_REVISITED`` before its recover command.
    """
    printable = sessions._printable

    def count(outcome: str) -> list[ItemResult]:
        return [r for r in results if r.outcome == outcome]

    lines = [f"Stored: {len(count(STORED))}", f"Already stored: {len(count(ALREADY_STORED))}"]
    by_sport: dict[str, list[str]] = {}
    for r in count(SKIPPED_SPORT):
        by_sport.setdefault(r.detail, []).append(printable(r.summary_name))
    for sport in sorted(by_sport):
        names = by_sport[sport]
        lines.append(f"Skipped {printable(sport)}: {len(names)} ({', '.join(names)})")
    lines.append(f"Not an activity: {len(count(NON_ACTIVITY))}")
    no_fit = [printable(r.summary_name) for r in count(NO_FIT_ORIGINAL)]
    lines.append(f"No FIT file: {len(no_fit)}" + (f" ({', '.join(no_fit)})" if no_fit else ""))
    for r in count(FAILED):
        day = r.day.isoformat() if r.day is not None else "day unknown"
        since = (r.day or start).isoformat()
        kind = "" if r.retry else f"{NOT_REVISITED}, "
        lines.append(
            f"Failed: {day}, {printable(r.item)}, {printable(r.detail)}, {kind}runcoach sync --since {since}"
        )
    return lines


# Race tags (F023): Connect's event type, sent to ``PUT /race-tags`` (``setRaceTags``), which
# matches each item to a stored run on the server.
RACE_TAGS_OPERATION = "setRaceTags"
# The most items one ``PUT /race-tags`` takes; batches are sent oldest first.
RACE_TAGS_BATCH = 500
# ``sync tags`` without ``--since`` covers today and the 29 days before it.
TAGS_DAYS = 30
RACE_EVENT = "race"
OLD_API_TAGS = "the API does not support race tags: update it and restart the API"
# The outcomes ``PUT /race-tags`` answers with, in the order the counts line prints them.
TAG_OUTCOMES = {
    "tagged": "tagged",
    "cleared": "cleared",
    "unchanged": "unchanged",
    "not_stored": "not stored",
    "mismatch": "mismatch",
    "ambiguous": "ambiguous",
    "other_tag": "other tag",
}
# The outcomes of a ``race`` item that get a line of their own.
_REPORTED_RACE = ("not_stored", "mismatch", "ambiguous")
# ``startTimeGMT`` as Connect lists it: a date and a time to the second, a fraction and a zone optional.
_GMT_FORM = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?"
)


class TagsStop(Exception):
    """Reading or sending race tags cannot go on; ``str(exc)`` is the message to print, its last
    line the one recovery command when there is one. ``lines`` is the report so far, printed
    before it (empty for a stop before anything was sent)."""

    lines: tuple[str, ...] = ()


@dataclass
class TagEntry:
    """One listed activity the tags step handled.

    ``item`` is what is sent to ``PUT /race-tags``, or ``None`` for an activity that is skipped
    (its summary, ``eventType`` or ``startTimeGMT`` could not be read), with ``reason`` saying why.
    ``local`` is the summary's local start, ``None`` only when it could not be read. ``outcome``,
    ``session_id`` and ``candidates`` are the API's answer, once its batch has been answered.
    """

    label: str
    local: datetime | None
    item: dict[str, Any] | None = None
    reason: str = ""
    start_utc: datetime | None = None
    outcome: str | None = None
    session_id: str | None = None
    candidates: list[str] | None = None


def _finite_number(value: Any) -> float | int | None:
    """``value`` when it is a finite number (not a bool), else ``None`` (sent as null)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _read_gmt(value: Any) -> datetime:
    """``startTimeGMT`` as an aware UTC datetime to the second; raises ``ValueError`` naming it.

    A value with no zone is UTC, as Connect lists it. A date alone, or a time with a fraction of a
    second, is refused: the API matches a stored run's start to the second.
    """
    if not isinstance(value, str) or not _GMT_FORM.fullmatch(value):
        raise ValueError(f"startTimeGMT {value!r} is missing or not a date and time")
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"startTimeGMT {value!r} is not a date and time") from None
    if when.microsecond:
        raise ValueError(f"startTimeGMT {value!r} has a fraction of a second")
    if when.tzinfo is None:
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def _event_race(summary: dict[str, Any]) -> bool:
    """True when the summary's ``eventType.typeKey`` is ``race``; raises ``TypeError`` when the
    event type is missing, null or has no ``typeKey`` text (never read as not-race)."""
    event = summary.get("eventType")
    key = event.get("typeKey") if isinstance(event, dict) else None
    if not isinstance(key, str):
        raise TypeError(f"eventType {event!r} is missing or has no typeKey")
    return key == RACE_EVENT


def tag_entries(listed: Any, start: date, end: date) -> tuple[list[TagEntry], list[TagEntry]]:
    """``(to_send, skipped)`` for a Connect listing covering ``start`` to ``end``.

    The fetched-type, non-manual activities whose local start falls in the range (the listing is
    not trusted to filter), each once by ``activityId`` (the first listed wins), sorted oldest
    first by UTC start then id. A summary that cannot be read, or whose ``eventType`` or
    ``startTimeGMT`` cannot, is skipped and never sent, so a listing hiccup cannot clear a tag.
    """
    if not isinstance(listed, list):
        raise TagsStop(f"Garmin's activity list could not be understood\n{_tags_not_read(start)}")
    to_send: list[TagEntry] = []
    skipped: list[TagEntry] = []
    seen: set[int] = set()
    for summary in listed:
        label = f"activity {_describe(summary)}"
        try:
            activity_id, local, activity_type = _read_summary(summary)
        except (TypeError, ValueError) as exc:
            local_text = summary.get("startTimeLocal") if isinstance(summary, dict) else None
            try:
                local = datetime.fromisoformat(local_text) if isinstance(local_text, str) else None
            except ValueError:
                local = None
            skipped.append(TagEntry(label=label, local=local, reason=f"unreadable summary: {exc}"))
            continue
        if not _is_fetched_type(activity_type) or summary.get("manualActivity") is True:
            continue
        if not start <= local.date() <= end or activity_id in seen:
            continue
        seen.add(activity_id)
        try:
            race = _event_race(summary)
            start_utc = _read_gmt(summary.get("startTimeGMT"))
        except (TypeError, ValueError) as exc:
            skipped.append(TagEntry(label=label, local=local, reason=str(exc)))
            continue
        item = {
            "connect_activity_id": activity_id,
            "start_time": start_utc.replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            "distance_m": _finite_number(summary.get("distance")),
            "duration_s": _finite_number(summary.get("duration")),
            "race": race,
        }
        to_send.append(TagEntry(label=label, local=local, item=item, start_utc=start_utc))
    to_send.sort(key=lambda entry: (entry.start_utc, entry.item["connect_activity_id"]))  # type: ignore[index]
    return to_send, skipped


def _tags_not_read(start: date) -> str:
    return f"Race tags not read: run runcoach sync tags --since {start.isoformat()}"


def _tags_stopped(day: date) -> str:
    return f"Race tags stopped at {day.isoformat()}: run runcoach sync tags --since {day.isoformat()}"


def tags_garmin_stop(exc: garmin.GarminError, start: date) -> TagsStop:
    """The stop for a Garmin error before race tags were read: the login message alone, or the
    error's reason without F022's recovery command, then the one ``sync tags`` command."""
    if isinstance(exc, garmin.LoginNeeded):
        return TagsStop(LOGIN_NEEDED)
    return TagsStop(f"{_garmin_reason(exc)}\n{_tags_not_read(start)}")


def read_tags_openapi(api_url: str, start: date) -> Any:
    """The served ``/openapi.json``, checked to list ``setRaceTags``, or raise ``TagsStop``."""
    try:
        resp = api_client.get_openapi(api_url)
    except api_client.ApiUnreachableError:
        raise TagsStop(
            f"could not reach API at {api_url}: start it with `runcoach-api`\n{_tags_not_read(start)}"
        ) from None
    doc = _body(resp) if resp.status_code == 200 else None
    check_race_tags(doc)
    return doc


def check_race_tags(doc: Any) -> None:
    """Raise ``TagsStop`` unless the served document lists ``setRaceTags`` (its presence only)."""
    if _find_operation(doc, RACE_TAGS_OPERATION) is None:
        raise TagsStop(OLD_API_TAGS)


def _apply_answers(batch: list[TagEntry], resp: httpx.Response) -> bool:
    """Record a 200 reply's outcomes on ``batch``; False when the reply does not answer it item by item."""
    body = _body(resp)
    answers = body.get("items") if isinstance(body, dict) else None
    if not isinstance(answers, list) or len(answers) != len(batch):
        return False
    parsed = []
    for entry, answer in zip(batch, answers, strict=True):
        if (
            not isinstance(answer, dict)
            or answer.get("connect_activity_id") != entry.item["connect_activity_id"]  # type: ignore[index]
            or answer.get("outcome") not in TAG_OUTCOMES
        ):
            return False
        session_id = answer.get("session_id")
        candidates = answer.get("candidates")
        parsed.append(
            (
                answer["outcome"],
                str(session_id) if session_id is not None else None,
                [str(c) for c in candidates] if isinstance(candidates, list) else [],
            )
        )
    for entry, (outcome, session_id, candidates) in zip(batch, parsed, strict=True):
        entry.outcome, entry.session_id, entry.candidates = outcome, session_id, candidates
    return True


def send_tags(api_url: str, to_send: list[TagEntry]) -> None:
    """Send ``to_send`` in batches of ``RACE_TAGS_BATCH``, oldest first, recording each answer.

    No items, no request. An unreachable API or a reply that is not a 200 answering every item of
    a batch stops: the earlier batches stand, no later one is sent, and the ``TagsStop`` names the
    first local day of the batch it stopped on.
    """
    for first in range(0, len(to_send), RACE_TAGS_BATCH):
        batch = to_send[first : first + RACE_TAGS_BATCH]
        day = batch[0].local.date()  # type: ignore[union-attr]
        try:
            resp = api_client.put_race_tags(api_url, [entry.item for entry in batch])  # type: ignore[misc]
        except api_client.ApiUnreachableError as exc:
            if isinstance(exc.__cause__, httpx.ReadTimeout):
                reason = (
                    f"the API at {api_url} did not answer within {api_client.LIST_TIMEOUT:g} s "
                    "while matching race tags"
                )
            else:
                reason = f"could not reach API at {api_url}: start it with `runcoach-api`"
            raise TagsStop(f"{reason}\n{_tags_stopped(day)}") from None
        if resp.status_code != 200:
            raise TagsStop(f"{sessions._printable(_reply_detail(resp))}\n{_tags_stopped(day)}")
        if not _apply_answers(batch, resp):
            raise TagsStop(
                f"the API's answer to race tags could not be understood: "
                f"{sessions._printable(resp.text[:_DETAIL_CHARS])}\n{_tags_stopped(day)}"
            )


def tags_report_lines(to_send: list[TagEntry], skipped: list[TagEntry]) -> list[str]:
    """The counts line, then one line per ``race`` item not stored, mismatched or ambiguous and per
    skipped activity, naming the Connect activity, its local start and any session ids. Items not
    yet answered (after a stop) are not counted."""
    printable = sessions._printable
    counts = dict.fromkeys(TAG_OUTCOMES, 0)
    for entry in to_send:
        if entry.outcome is not None:
            counts[entry.outcome] += 1
    words = [f"{TAG_OUTCOMES[key]} {n}" for key, n in counts.items()] + [f"skipped {len(skipped)}"]
    lines = [f"Race tags: {', '.join(words)}"]

    def where(entry: TagEntry) -> str:
        local = entry.local.isoformat(sep=" ") if entry.local is not None else "start unknown"
        return f"{printable(entry.label)}, {local}"

    for entry in to_send:
        if entry.outcome not in _REPORTED_RACE or not entry.item or entry.item["race"] is not True:
            continue
        line = f"Race {TAG_OUTCOMES[entry.outcome]}: {where(entry)}"  # type: ignore[index]
        ids = (
            entry.candidates
            if entry.outcome == "ambiguous"
            else ([entry.session_id] if entry.session_id else [])
        )
        if ids:
            line += f", {'session' if len(ids) == 1 else 'sessions'} {', '.join(printable(i) for i in ids)}"
        lines.append(line)
    for entry in skipped:
        lines.append(f"Skipped: {where(entry)}, {printable(entry.reason)}")
    return lines


def run_tags(
    client: garmin.GarminClient, api_url: str, start: date, end: date, *, listed: list[Any] | None = None
) -> list[str]:
    """List Connect activities for ``start`` to ``end``, send their race tags and return the report
    lines. Downloads nothing; never reads or writes the sync state. The caller has checked the API
    lists ``setRaceTags`` (``read_tags_openapi`` or ``check_race_tags``). ``listed``, when given, is
    a listing already made that covers the range, used instead of a new call.

    A ``TagsStop`` after the listing carries the report so far as ``lines``.
    """
    if listed is None:
        try:
            listed = client.list_activities(start, end)
        except garmin.GarminError as exc:
            raise tags_garmin_stop(exc, start) from None
    to_send, skipped = tag_entries(listed, start, end)
    try:
        send_tags(api_url, to_send)
    except TagsStop as exc:
        exc.lines = tuple(tags_report_lines(to_send, skipped))
        raise
    return tags_report_lines(to_send, skipped)


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
    if ctx.invoked_subcommand == "tags" and since is not None:
        raise typer.BadParameter(
            "the tags command takes its own date: run `runcoach sync tags --since YYYY-MM-DD`",
            param_hint="'--since'",
        )
    if ctx.invoked_subcommand is not None:
        return
    _require_library()
    end = today()
    since_day = _parse_since(since, end) if since is not None else None
    cfg = config.load_config()
    previous, state_line = read_state(end)
    results: list[ItemResult] = []
    listing: list[Any] = []
    served = ServedDoc()
    start: date | None = None
    try:
        start, warning = find_range(cfg.api_url, since_day, end)
        reason = None
        if since_day is None:
            start, reason = apply_state(start, previous)
        _write_utf8(f"From {start.isoformat()} to {end.isoformat()}")
        if reason is not None:
            _write_utf8(reason)
        for line in (warning, state_line):
            if line is not None:
                typer.echo(line, err=True)
        try:
            client = _adapter().open_client()
        except garmin.GarminError as exc:
            raise _garmin_stop(exc, start) from None
        fetch(client, cfg.api_url, start, end, results, listing_out=listing, served=served)
    except SyncStop as exc:
        if start is not None:
            _write_utf8("\n".join(summary_lines(results, start)))
            _save_state(next_state(previous, results, start, end, exc.stopped_on or start))
        raise _fail(str(exc)) from None
    _write_utf8("\n".join(summary_lines(results, start)))
    _save_state(next_state(previous, results, start, end, None))
    _read_tags_after_uploads(client, cfg.api_url, start, end, listing, served)
    if any(r.outcome == FAILED for r in results):
        raise typer.Exit(code=1)


def _read_tags_after_uploads(
    client: garmin.GarminClient, api_url: str, start: date, end: date, listing: list[Any], served: ServedDoc
) -> None:
    """The race tags step of ``sync`` (F023 AC4): read tags for ``[min(start, today - 29 days), end]``.

    The upload pass's listing is reused when it covers that window, else one more listing is made.
    The ``/openapi.json`` the switch check read is checked for ``setRaceTags``; with no upload it
    was never read, and is read here. A stop prints the report so far and its message and exits 1.
    Never reads or writes the sync state.
    """
    tags_start = min(start, end - timedelta(days=TAGS_DAYS - 1))
    try:
        if served.read:
            check_race_tags(served.doc)
        else:
            read_tags_openapi(api_url, tags_start)
        reused = listing if start <= tags_start else None
        lines = run_tags(client, api_url, tags_start, end, listed=reused)
    except TagsStop as exc:
        if exc.lines:
            _write_utf8("\n".join(exc.lines))
        raise _fail(str(exc)) from None
    _write_utf8("\n".join(lines))


def _save_state(state: SyncState) -> None:
    warning = write_state(state)
    if warning is not None:
        typer.echo(warning, err=True)


@sync_app.command(name="tags")
def tags(
    since: str | None = typer.Option(
        None,
        "--since",
        metavar="YYYY-MM-DD",
        help="Read tags from this day instead of 29 days before today (2016-01-01 reads them all).",
    ),
) -> None:
    """Read race tags from Garmin Connect for stored runs; lists activities, downloads nothing.

    An activity whose event type is Race in Connect marks the stored run it matches as a race; any
    other event type clears a race tag. The sync state is not read or written.
    """
    _require_library()
    end = today()
    start = _parse_since(since, end) if since is not None else end - timedelta(days=TAGS_DAYS - 1)
    cfg = config.load_config()
    _write_utf8(f"From {start.isoformat()} to {end.isoformat()}")
    try:
        read_tags_openapi(cfg.api_url, start)
        try:
            client = _adapter().open_client()
        except garmin.GarminError as exc:
            raise tags_garmin_stop(exc, start) from None
        lines = run_tags(client, cfg.api_url, start, end)
    except TagsStop as exc:
        if exc.lines:
            _write_utf8("\n".join(exc.lines))
        raise _fail(str(exc)) from None
    _write_utf8("\n".join(lines))


@sync_app.command(name="login")
def login() -> None:
    """Log in to Garmin Connect and save the token (replacing any saved one)."""
    _require_library()
    email = ask_text("Garmin email")
    password = ask_secret("Garmin password")
    prompt_mfa: Callable[[], str] = lambda: ask_text("Garmin MFA code")
    try:
        _adapter().login(email, password, prompt_mfa)
    except garmin.TokenNotSaved as exc:
        raise _fail(
            f"Garmin accepted the login, but the token could not be saved in {garmin.token_dir()}: "
            f"{sessions._printable(str(exc))}. Any saved token is unchanged."
        ) from None
    except garmin.GarminError as exc:
        raise _fail(f"{_login_failure(exc)}. Any saved token is unchanged.") from None
    _write_utf8(f"Logged in. Token saved in {garmin.token_dir()}")


def _login_failure(exc: garmin.GarminError) -> str:
    """What went wrong with a ``sync login``, worded by the kind of Garmin error."""
    reason = sessions._printable(str(exc))
    if isinstance(exc, garmin.LoginNeeded):
        return f"Garmin refused the login: {reason}"
    if isinstance(exc, garmin.GarminUnreachable):
        return f"could not reach Garmin Connect: {reason}"
    if isinstance(exc, garmin.RateLimited):
        return f"Garmin is limiting requests ({reason}): wait, then run `runcoach sync login`"
    return f"Garmin: {reason}"


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
