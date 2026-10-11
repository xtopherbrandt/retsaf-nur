"""The session list's pure rules (F019): the session name, the cursor and the summary row.

**Pure, by design**, on the pattern of ``metrics/gap.py``: it imports neither
``config`` nor ``db`` nor ``fastapi``. ``ingestion.mapping`` calls
``session_name`` at upload to fill ``sessions.name``, and the list's read side
calls it again for a session stored before that column existed (whose saved
workout name is unknown, so only the profile name stored in
``context.provenance.sport_profile_name`` can apply), so the whitespace rule
is written once and the two paths cannot disagree.

**The name** (F019 AC2; user rulings 2026-10-08 and 2026-10-09) is the FIT
``workout.wkt_name``, else ``session.sport_profile_name``. A source that is
absent, empty or whitespace-only (``str.isspace``) falls through to the next;
when neither has one the name is ``None``. A name that is not blank is kept
verbatim, unstripped: the athlete typed it, and a leading space or a doubled
one is theirs to see. Only ``str.isspace`` whitespace is blank, so a
zero-width space (U+200B, a format character, not whitespace) is a name. The
uploaded file's name is never a source (user ruling 2026-10-08).

**The cursor** (AC3, AC4) is URL-safe base64 of the last served row's stored
``start_time`` and ``session_id`` joined by ``CURSOR_SEPARATOR``: a keyset,
so a session inserted or deleted between two pages repeats or skips nothing.
``decode_cursor`` refuses, with ``ValueError``, anything that is not that
shape: not base64 (``validate=True``, so a missing padding or a character
outside the alphabet is refused; the alphabet is the URL-safe one, and with
``altchars`` the standard ``+`` and ``/`` decode too), not UTF-8, not exactly two parts, or a ``start_time``
that is not an aware UTC timestamp. A ``Z`` time is aware UTC and is accepted,
normalised to the stored ``+00:00`` spelling before the comparison; ``+02:00``
is refused. A forged cursor of the right shape is served, not refused: the
page it names is a correct answer to the question it asks.

**The summary numbers** (AC1, AC5) are F013's recorded time ``T`` and
distance ``D`` over the counted segments (``segments.recorded_totals``, the
one segment rule), mapped as the features route maps them: ``duration_s``
null iff ``T`` is 0; ``distance_m`` and ``avg_pace_s_per_km`` null iff ``D``
is 0; pace ``1000 * T / D``.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from runcoach_api.metrics.segments import recorded_totals

__all__ = [
    "CURSOR_SEPARATOR",
    "NO_NUMBERS",
    "decode_cursor",
    "encode_cursor",
    "page",
    "recorded_totals",
    "session_name",
    "summary_numbers",
    "summary_row",
]


def session_name(workout_name: str | None, profile_name: str | None) -> str | None:
    """The first of ``workout_name``, ``profile_name`` that is a non-blank string; else ``None``.

    ``fitdecode`` decodes a FIT string field to a ``str`` or ``None``. A value
    of any other type is not a name and falls through like an absent one: the
    undeclared-capture note already records a non-string claimed profile name
    exactly as read (``hrv_classification``), and this rule must not refuse a
    file that path accepts.
    """
    for value in (workout_name, profile_name):
        if isinstance(value, str) and value and not value.isspace():
            return value
    return None


# The character between the cursor's two parts. A session id is 32 hex
# characters and a stored start_time an ISO 8601 timestamp, so neither
# carries it; a cursor whose text splits into more than two parts is refused.
CURSOR_SEPARATOR = "|"

_UTC = timedelta(0)


def encode_cursor(start_time: str, session_id: str) -> str:
    """The ``next_cursor`` for a page whose last served row is ``(start_time, session_id)``."""
    text = f"{start_time}{CURSOR_SEPARATOR}{session_id}".encode("utf-8")
    return base64.b64encode(text, altchars=b"-_").decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """``(start_time, session_id)`` of a ``since`` value, or ``ValueError`` saying what is wrong.

    ``start_time`` comes back as ``datetime.isoformat()`` of the parsed aware
    UTC value, which is the stored spelling (``mapping.py``), so a ``Z`` input
    compares correctly against the ``+00:00`` rows. ``session_id`` is returned
    as sent: its shape is not checked, because a forged id of any text still
    names a position in the order.
    """
    try:
        text = base64.b64decode(cursor, altchars=b"-_", validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(f"since is not URL-safe base64 of UTF-8 text ({exc})") from exc
    parts = text.split(CURSOR_SEPARATOR)
    if len(parts) != 2:
        raise ValueError(
            f"since must decode to a start_time and a session_id joined by {CURSOR_SEPARATOR!r}, "
            f"not {len(parts)} part(s)"
        )
    start_time, session_id = parts
    try:
        when = datetime.fromisoformat(start_time)
    except ValueError as exc:
        raise ValueError(f"since carries a start_time that is not an ISO 8601 timestamp: {start_time!r}") from exc
    if when.utcoffset() != _UTC:
        raise ValueError(f"since carries a start_time that is not an aware UTC timestamp: {start_time!r}")
    return when.isoformat(), session_id


def page(rows: Sequence[Mapping[str, object]], limit: int) -> tuple[list[Mapping[str, object]], str | None]:
    """The rows to serve and the ``next_cursor``, from up to ``limit + 1`` rows read in list order.

    The extra row, when present, proves a next page exists and is not served;
    the cursor then names the last served row. With ``limit`` rows or fewer
    the cursor is null, so a store holding an exact multiple of ``limit`` never
    ends on an empty page.
    """
    served = list(rows[:limit])
    if len(rows) > limit:
        last = served[-1]
        return served, encode_cursor(str(last["start_time"]), str(last["session_id"]))
    return served, None


# The three numbers of a session the list does not compute: not stored as
# running, no records, or a row whose records could not be read (AC5).
NO_NUMBERS: Mapping[str, float | None] = {"duration_s": None, "distance_m": None, "avg_pace_s_per_km": None}


def summary_numbers(T: float, D: float) -> dict[str, float | None]:
    """``duration_s``, ``distance_m`` and ``avg_pace_s_per_km`` from recorded time and distance.

    The same expressions as ``session_features.compute_session_features``:
    ``T`` is served when it is above 0, ``D`` when it is above 0, and the pace
    is ``1000 * T / D`` when ``D`` is above 0; otherwise null, where the
    features route serves ``no_counted_segments`` or ``no_distance``.
    """
    return {
        "duration_s": T if T > 0.0 else None,
        "distance_m": D if D > 0.0 else None,
        "avg_pace_s_per_km": 1000.0 * T / D if D > 0.0 else None,
    }


def summary_row(row: Mapping[str, object], numbers: Mapping[str, float | None]) -> dict[str, object]:
    """One ``SessionSummary`` body from a session row and its three numbers.

    ``row`` carries ``session_id``, ``start_time``, ``sport``, ``activity_tag``,
    ``name``, ``quality_flags`` (a list) and ``context`` (a mapping), as
    ``db.read_session_page`` decodes them. The name is the stored ``name``;
    when that is null (a session stored before the column existed) it is
    ``session_name`` over the profile name in ``context.provenance``, so the
    whitespace rule applies to the fallback exactly as it did at upload.
    ``workout_id`` is always null: no planned workout is linked.
    """
    name = row.get("name")
    if name is None:
        context = row.get("context") or {}
        provenance = context.get("provenance") or {} if isinstance(context, Mapping) else {}
        profile_name = provenance.get("sport_profile_name") if isinstance(provenance, Mapping) else None
        name = session_name(None, profile_name)
    return {
        "session_id": row["session_id"],
        "start_time": row["start_time"],
        "sport": row["sport"],
        "activity_tag": row.get("activity_tag"),
        "name": name,
        "distance_m": numbers["distance_m"],
        "duration_s": numbers["duration_s"],
        "avg_pace_s_per_km": numbers["avg_pace_s_per_km"],
        "quality_flags": list(row.get("quality_flags") or []),
        "workout_id": None,
    }
