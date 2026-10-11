"""``runcoach sessions`` -- list the stored sessions, newest first, from ``GET /sessions``.

One line per session: the display name, the sport, the distance in km to 2 decimals, the duration as
``h:mm:ss`` (``0:mm:ss`` under an hour), the pace as ``m:ss/km`` and the session id, with ``-`` for a
null value. The display name is the stored ``name`` followed by the start date and time
(``YYYY-MM-DD HH:MM``) in the computer's local zone; with no name it is the sport followed by the date
and time. Names print as stored, untruncated, except that each control character (C0, DEL and C1)
prints as ``?`` so each session stays one line and a name cannot send terminal escapes; the stored
and served name is unchanged. The output is written as UTF-8 whatever the console's own encoding.

Durations and paces are rounded to whole seconds **half up** before they are split into minutes and
seconds (298.5 s/km is "4:59", 299.5 is "5:00", never "4:60"; 3599.5 s is "1:00:00"). Python's
``round`` is half-even and would print "4:58" for 298.5, so it is not used.

The default is the 50 newest. ``--limit N`` (N >= 1) prints N, fetched in pages of at most 200;
``--all`` fetches pages of 200 until ``next_cursor`` is null and wins over ``--limit``. Each page is
printed as it arrives, so on a later page's error the lines already printed stay printed. The
cursor is opaque: whatever ``next_cursor`` says is what ``since`` carries back. A 200 body without
``items``, a row missing a field, or a ``next_cursor`` equal to the one just followed, is reported as
an unparseable body (the same line ``runcoach profile`` prints), and the CLI exits 1.

The local zone is ``LOCAL_ZONE`` when set, else the computer's, applied per instant by
``astimezone()`` so each start time prints in the offset in force on its own date (not today's).
``LOCAL_ZONE`` is a test seam, a module attribute rather than a read of ``TZ`` because ``TZ`` does
not change ``astimezone()`` on Windows; it takes any ``tzinfo``, a fixed offset or a ``ZoneInfo``.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone, tzinfo
from typing import Any

import httpx
import typer

from runcoach_cli import config
from runcoach_cli.api_client import ApiUnreachableError, list_sessions
from runcoach_cli.commands.profile import _fail

DEFAULT_LIMIT = 50
PAGE_SIZE = 200

# The zone the start times print in; ``None`` means the computer's local zone.
LOCAL_ZONE: tzinfo | None = None

NULL = "-"


# C0 controls, DEL and C1 controls: each prints as "?" so a name cannot move the cursor, retitle the
# window or start a second line.
_CONTROLS = {code: "?" for code in [*range(0x20), 0x7F, *range(0x80, 0xA0)]}


def _printable(name: str) -> str:
    """``name`` with each control character replaced by ``?``."""
    return name.translate(_CONTROLS)


def _half_up_seconds(value: float) -> int:
    """``value`` (non-negative seconds) rounded to a whole number, halves up."""
    return int(math.floor(value + 0.5))


def format_duration(seconds: float | None) -> str:
    """``h:mm:ss`` from seconds rounded half up; ``-`` for a null."""
    if seconds is None:
        return NULL
    total = _half_up_seconds(float(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def format_pace(seconds_per_km: float | None) -> str:
    """``m:ss/km`` from the total seconds rounded half up first; ``-`` for a null."""
    if seconds_per_km is None:
        return NULL
    total = _half_up_seconds(float(seconds_per_km))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}/km"


def format_distance(distance_m: float | None) -> str:
    """Kilometres to 2 decimals; ``-`` for a null."""
    if distance_m is None:
        return NULL
    return f"{float(distance_m) / 1000:.2f}"


def _local_stamp(start_time: str, zone: tzinfo | None) -> str:
    """``YYYY-MM-DD HH:MM`` of a stored (UTC) ISO start time in ``zone``.

    ``None`` is the computer's zone, whose rules ``astimezone()`` applies to this instant, so a
    winter start prints in winter time and a summer start in summer time whatever today's offset.
    """
    when = datetime.fromisoformat(start_time)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def render_line(row: dict[str, Any], zone: tzinfo | None) -> str:
    """One session's line, or raise ``KeyError``/``TypeError``/``ValueError``/``OverflowError``
    on a malformed row, or ``OSError`` for a start time the computer's zone cannot convert (Windows
    refuses an instant before 1970 or after 3000)."""
    name = row["name"]
    sport = row["sport"]
    stamp = _local_stamp(row["start_time"], zone)
    display = f"{_printable(str(name if name is not None else sport))} {stamp}"
    return "  ".join(
        [
            display,
            str(sport),
            format_distance(row["distance_m"]),
            format_duration(row["duration_s"]),
            format_pace(row["avg_pace_s_per_km"]),
            str(row["session_id"]),
        ]
    )


def _write_utf8(text: str) -> None:
    """Write ``text`` and a newline to stdout as UTF-8 bytes, whatever the console encoding."""
    out = sys.stdout
    buffer = getattr(out, "buffer", None)
    if buffer is None:
        typer.echo(text)
        return
    out.flush()
    buffer.write((text + "\n").encode("utf-8"))
    buffer.flush()


def _unparseable(detail: str) -> typer.Exit:
    return _fail(f"the API's response could not be understood: {detail}")


def _fetch_page(api_url: str, since: str | None, limit: int) -> dict[str, Any]:
    """One ``GET /sessions`` page as a dict with ``items`` a list, or report to stderr and exit 1."""
    try:
        resp: httpx.Response = list_sessions(api_url, since=since, limit=limit)
    except ApiUnreachableError:
        raise _fail(
            f"could not reach API at {api_url}. Is it running? Start it with `runcoach-api`."
        ) from None
    if resp.status_code != 200:
        raise _fail(f"API returned {resp.status_code}: {resp.text}")
    try:
        body = resp.json()
    except json.JSONDecodeError as exc:
        raise _unparseable(str(exc)) from None
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise _unparseable("the page has no items list") from None
    return body


def sessions(
    limit: int = typer.Option(
        DEFAULT_LIMIT,
        "--limit",
        min=1,
        help="How many of the newest sessions to print (1 or more).",
    ),
    all_sessions: bool = typer.Option(
        False, "--all", help="Print every stored session, page by page; wins over --limit."
    ),
) -> None:
    """List the stored sessions, newest first, one line each."""
    cfg = config.load_config()
    zone = LOCAL_ZONE
    wanted: int | None = None if all_sessions else limit
    since: str | None = None
    printed = 0
    while True:
        remaining = PAGE_SIZE if wanted is None else min(wanted - printed, PAGE_SIZE)
        body = _fetch_page(cfg.api_url, since, remaining)
        items = body["items"]
        if wanted is not None:
            items = items[: wanted - printed]
        try:
            lines = [render_line(row, zone) for row in items]
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
            raise _unparseable(f"missing or malformed {exc}") from None
        for line in lines:
            _write_utf8(line)
        printed += len(lines)
        next_cursor = body.get("next_cursor")
        if next_cursor is None or (wanted is not None and printed >= wanted):
            break
        if not isinstance(next_cursor, str) or next_cursor == since:
            raise _unparseable("next_cursor repeats the page just read") from None
        since = next_cursor
    if printed == 0:
        typer.echo("No sessions stored.")
