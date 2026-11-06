"""Recognise a Strava bulk export and read its ``activities.csv`` (F030 reference section 2, items
1-4 and 6). Pure: reads the open ``ZipFile`` it is given, opens no store, makes no HTTP call.

- ``recognise(zf)``: the folder prefix of ``activities.csv`` (``""`` at the root, ``"<folder>/"``
  when the only top-level entry other than ``__MACOSX/`` is one folder holding it), or ``None``
  when the archive is not a Strava export and the Garmin rules apply. Entry names are labels,
  never paths: the prefix is built with ``"/"`` and nothing reaches a file system call. Explicit
  directory entries count as their folder, not as extra entries.
- ``read_activities(zf, prefix)``: the rows of ``activities.csv`` as ``CsvRow`` ordered by
  ``start_time`` then ``activity_id``, and the rows that failed as ``FailedRow`` in file order.
  The entry is opened by ``ZipInfo`` (the first entry of that name) and read stopping at
  ``archive.FIT_ENTRY_CAP + 1`` bytes, the cap read at call time. Three failures are the job's,
  raised as ``StravaCsvError`` before any row is returned: the file past the cap (naming the
  limit), bytes that are not UTF-8 (naming the byte offset in the file, a BOM counted), and a
  header without one of ``REQUIRED_KEYS`` (naming each one missing). The ``csv`` module's field
  limit is raised to the cap first, so no single cell of a file within the cap fails the file.
- Parsing: ``csv.reader`` over ``io.StringIO(text, newline="")``. Not ``DictReader``, which keeps
  only one cell of a repeated header name, and not default newline translation, which would turn
  a CR inside a quoted cell into LF. A repeated name's later copies are keyed ``<name> (2)``,
  ``<name> (3)``. Every cell is kept as text, an empty cell as ``""``.
- ``Activity Date`` reads ``Apr 18, 2022, 2:26:14 PM`` in UTC, through an English month table and
  a regular expression, never ``strptime`` (whose ``%b`` and ``%p`` follow the locale). 12 AM is
  hour 0, 12 PM hour 12; no time zone or DST arithmetic happens. ``start_time`` is
  ``datetime.isoformat()`` with ``+00:00``, 25 characters, the store's spelling.
- A row whose cell count differs from the header's, or whose ``Activity ID`` (ASCII digits) or
  ``Activity Date`` does not parse, is a ``FailedRow``: ``entry`` ``activities.csv line <n>``,
  ``<n>`` the physical line it starts on (``reader.line_num`` before reading it, plus 1, so the
  lines of an earlier multi-line cell are counted), ``detail`` quoting the raw ``Activity ID``,
  ``Activity Date`` and ``Activity Name`` cells (``""`` for a cell a short row lacks). The next
  row is read. A record with no cells at all (a blank line) is not a row and is skipped. An
  ``Activity ID`` of more than 19 significant digits reads as ``PAST_64_BITS`` without being
  converted, so the job fails that row as past the 64-bit range.
- ``read_athlete_id(zf, prefix)``: the ``Athlete ID`` of ``profile.csv`` beside ``activities.csv``,
  read under the same cap, UTF-8 with an optional BOM, or ``None`` when there is none to read.
- ``distance_m`` is ``Distance (2)`` in metres; empty, ``0.0`` or a cell that is not a finite
  number reads as no distance (``None``).
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from runcoach_api.imports import archive
from runcoach_api.imports.strava_types import CsvRow

CSV_NAME = "activities.csv"
PROFILE_NAME = "profile.csv"
ATHLETE_ID = "Athlete ID"
MACOSX = "__MACOSX"

ACTIVITY_ID = "Activity ID"
ACTIVITY_DATE = "Activity Date"
ACTIVITY_NAME = "Activity Name"
ACTIVITY_TYPE = "Activity Type"
FILENAME = "Filename"
DISTANCE = "Distance"
DISTANCE_2 = "Distance (2)"
REQUIRED_KEYS = (ACTIVITY_ID, ACTIVITY_DATE, ACTIVITY_NAME, ACTIVITY_TYPE, FILENAME, DISTANCE, DISTANCE_2)

_BOM = b"\xef\xbb\xbf"
_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1
    )
}
_DATE = re.compile(
    r"(?P<month>[A-Z][a-z]{2}) (?P<day>[0-9]{1,2}), (?P<year>[0-9]{4}), "
    r"(?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2}) (?P<ampm>AM|PM)"
)
_ID = re.compile(r"[0-9]+")
# 2**63 - 1, the largest 64-bit integer, has 19 digits; an ID with more significant digits is past it.
_MAX_SIGNIFICANT_DIGITS = 19
# The value an Activity ID past 19 significant digits reads as: past SQLite's INTEGER range.
PAST_64_BITS = 2**63


class StravaCsvError(ValueError):
    """``activities.csv`` cannot be read as a whole: the import job fails with this message."""


class StravaAthleteError(ValueError):
    """The archive's ``profile.csv`` names another athlete than the store's Strava imports: the
    import job fails with this message before any row is written."""


@dataclass(frozen=True)
class FailedRow:
    """A row that did not parse. ``activity_id`` is the parsed ``Activity ID`` when that cell
    parsed, else ``None``; ``cells`` the row's raw cells as the CSV holds them."""

    line: int
    entry: str
    detail: str
    activity_id: int | None
    cells: tuple[str, ...]


@dataclass(frozen=True)
class CsvResult:
    """The parsed rows in processing order, the failed rows in file order, and the column keys."""

    keys: tuple[str, ...]
    rows: tuple[CsvRow, ...]
    failed: tuple[FailedRow, ...]


def recognise(zf: zipfile.ZipFile) -> str | None:
    """The folder prefix of a Strava archive's ``activities.csv``, or ``None`` for any other archive."""
    names = [info.filename for info in zf.infolist()]
    if CSV_NAME in names:
        return ""
    tops: set[str] = set()
    for name in names:
        top, slash, _rest = name.partition("/")
        if top == MACOSX and slash:
            continue
        if not slash:
            return None  # a file at the root beside the folder
        tops.add(top)
    if len(tops) != 1:
        return None
    (folder,) = tops
    prefix = f"{folder}/"
    return prefix if folder and f"{prefix}{CSV_NAME}" in names else None


def keys_of(header: list[str] | tuple[str, ...]) -> list[str]:
    """The column keys of ``header``: a repeated name's later copies keyed ``<name> (2)``, ``(3)``."""
    seen: dict[str, int] = {}
    keys = []
    for name in header:
        seen[name] = seen.get(name, 0) + 1
        keys.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return keys


def parse_activity_date(text: str) -> str | None:
    """``Apr 18, 2022, 2:26:14 PM`` (UTC) as ``2022-04-18T14:26:14+00:00``; ``None`` when it does not parse."""
    match = _DATE.fullmatch(text)
    if match is None or match["month"] not in _MONTHS:
        return None
    hour = int(match["hour"])
    if not 1 <= hour <= 12:
        return None
    hour = hour % 12 + (12 if match["ampm"] == "PM" else 0)
    try:
        moment = datetime(
            int(match["year"]),
            _MONTHS[match["month"]],
            int(match["day"]),
            hour,
            int(match["minute"]),
            int(match["second"]),
            tzinfo=UTC,
        )
    except ValueError:
        return None
    return moment.isoformat()


def _distance(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or value == 0.0:
        return None
    return value


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _read_bytes(zf: zipfile.ZipFile, prefix: str) -> bytes:
    name = f"{prefix}{CSV_NAME}"
    info = next((i for i in zf.infolist() if i.filename == name), None)
    if info is None:
        raise StravaCsvError(f"{name} is not in the archive")
    cap = archive.FIT_ENTRY_CAP
    with zf.open(info) as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise StravaCsvError(f"{CSV_NAME} exceeds the {cap} byte limit")
    return data


def _decode(data: bytes) -> str:
    skip = len(_BOM) if data.startswith(_BOM) else 0
    try:
        return data[skip:].decode("utf-8")
    except UnicodeDecodeError as err:
        raise StravaCsvError(f"{CSV_NAME} is not UTF-8 at byte {err.start + skip}") from err


def read_activities(zf: zipfile.ZipFile, prefix: str) -> CsvResult:
    """Read ``<prefix>activities.csv``: its rows in processing order and its failed rows."""
    text = _decode(_read_bytes(zf, prefix))
    # The byte cap is the only size bound: no cell of a file within it is past the field limit.
    # ``field_size_limit`` is the ``csv`` module's own, process-wide; it is only ever raised here.
    csv.field_size_limit(max(csv.field_size_limit(), archive.FIT_ENTRY_CAP + 1))
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        try:
            header = next(reader)
        except StopIteration:
            header = []
        keys = keys_of(header)
        missing = [key for key in REQUIRED_KEYS if key not in keys]
        if missing:
            raise StravaCsvError(f"{CSV_NAME} header lacks {', '.join(missing)}")
        at = {key: keys.index(key) for key in REQUIRED_KEYS}
        rows: list[CsvRow] = []
        failed: list[FailedRow] = []
        while True:
            line = reader.line_num + 1
            try:
                cells = next(reader)
            except StopIteration:
                break
            if not cells:
                continue
            outcome = _row(keys, at, line, cells)
            (rows if isinstance(outcome, CsvRow) else failed).append(outcome)
    except csv.Error as err:
        raise StravaCsvError(f"{CSV_NAME} line {reader.line_num}: {err}") from err
    rows.sort(key=lambda r: (r.start_time, r.activity_id))
    return CsvResult(keys=tuple(keys), rows=tuple(rows), failed=tuple(failed))


def read_athlete_id(zf: zipfile.ZipFile, prefix: str) -> str | None:
    """The ``Athlete ID`` of ``<prefix>profile.csv`` (beside ``activities.csv``), stripped, or
    ``None`` when there is none to read: no such entry, an entry past ``archive.FIT_ENTRY_CAP`` or
    whose bytes do not read, not UTF-8 (a BOM allowed), not CSV, no ``Athlete ID`` column, no row
    under the header, or an empty cell. ``None`` blocks no import (athlete ruling 2026-11-06)."""
    name = f"{prefix}{PROFILE_NAME}"
    info = next((i for i in zf.infolist() if i.filename == name), None)
    if info is None:
        return None
    cap = archive.FIT_ENTRY_CAP
    try:
        with zf.open(info) as handle:
            data = handle.read(cap + 1)
    except archive._ENTRY_READ_ERRORS:
        return None
    if len(data) > cap:
        return None
    try:
        text = data[len(_BOM) if data.startswith(_BOM) else 0 :].decode("utf-8")
        reader = csv.reader(io.StringIO(text, newline=""))
        header = next(reader, [])
        row = next(reader, [])
    except (UnicodeDecodeError, csv.Error):
        return None
    if ATHLETE_ID not in header:
        return None
    at = header.index(ATHLETE_ID)
    value = row[at].strip() if at < len(row) else ""
    return value or None


def _activity_id(raw: str) -> int | None:
    """``raw`` (ASCII digits) as an int, or ``None``. Past 19 significant digits the value is past
    any 64-bit integer, so ``PAST_64_BITS`` stands for it without converting the digits (CPython
    refuses to convert more than 4300): the job fails such a row as past the 64-bit range. Only
    the significant digits are converted, so leading zeros of any count read by their value."""
    if not _ID.fullmatch(raw):
        return None
    digits = raw.lstrip("0")
    return PAST_64_BITS if len(digits) > _MAX_SIGNIFICANT_DIGITS else int(digits or "0")


def _row(keys: list[str], at: dict[str, int], line: int, cells: list[str]) -> CsvRow | FailedRow:
    def raw(key: str) -> str:
        index = at[key]
        return cells[index] if index < len(cells) else ""

    raw_id, raw_date, raw_name = raw(ACTIVITY_ID), raw(ACTIVITY_DATE), raw(ACTIVITY_NAME)
    activity_id = _activity_id(raw_id)
    start_time = parse_activity_date(raw_date)
    if len(cells) != len(keys):
        reason = f"row has {len(cells)} cells, the header {len(keys)}"
    elif activity_id is None:
        reason = f"{ACTIVITY_ID} does not parse"
    elif start_time is None:
        reason = f"{ACTIVITY_DATE} does not parse"
    else:
        columns = dict(zip(keys, cells, strict=True))
        return CsvRow(
            line=line,
            activity_id=activity_id,
            start_time=start_time,
            activity_type=columns[ACTIVITY_TYPE],
            name=columns[ACTIVITY_NAME],
            filename=columns[FILENAME],
            distance_m=_distance(columns[DISTANCE_2]),
            columns=columns,
        )
    detail = (
        f"{reason}: {ACTIVITY_ID} {_quote(raw_id)}, {ACTIVITY_DATE} {_quote(raw_date)}, "
        f"{ACTIVITY_NAME} {_quote(raw_name)}"
    )
    return FailedRow(
        line=line,
        entry=f"{CSV_NAME} line {line}",
        detail=detail,
        activity_id=activity_id,
        cells=tuple(cells),
    )
