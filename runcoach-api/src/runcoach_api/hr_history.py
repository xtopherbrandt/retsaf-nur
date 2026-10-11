"""The HR setting in effect on a run's own date (F021 AC2, with AC4's source rule).

Pure: ``borrow`` reads plain tuples and F016's resolved field and anchor, and touches no store
and no config.

For each of ``resting_hr_bpm``, ``max_hr_bpm`` and ``threshold_hr_bpm`` that a run's own file does
not carry, the candidates are:

- stored files carrying the field whose ``start_time`` is at or before the run's (for max and
  threshold only sessions stored as ``running``, as F016 counts them), the latest by
  ``(start_time, upload_order)``;
- dated entries whose ``effective_from`` is on or before the run's local date, the date of its
  start in the configured ``athlete_timezone``.

The candidate with the later local date wins; a file and an entry on the same local date: the
file wins, as in F016. With no candidate the run uses F016's effective anchor, and
``out_of_period`` is true only when that anchor is available and came from a file (which, with no
file at or before the run, is a file dated after it); an undated ``PATCH /me`` entry or an
unavailable anchor borrows nothing later, so it is false.

The block has the keys of ``metrics.session_load.EMPTY_VALUE`` plus ``entry_id``,
``effective_from`` and ``out_of_period``. When the winner is the very file F016's resolution names,
the block is the anchor block F017 saves (``source`` ``anchor`` with its ``anchor_version``, or the
empty block carrying the anchor's ``missing`` / ``order_conflict`` reason), so a run uploaded after
every stored file is saved as before. Another file is ``session_file_dated`` with its
``session_id``; an entry is ``history`` with its ``entry_id`` and ``effective_from``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from runcoach_api import profile
from runcoach_api.metrics.hrv_trend import local_day
from runcoach_api.metrics.session_load import EMPTY_VALUE

# The fields a dated entry can set, and the only fields ``borrow`` looks up.
HR_FIELDS: tuple[str, ...] = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm")

SOURCE_HISTORY = "history"
SOURCE_SESSION_FILE_DATED = "session_file_dated"

# ``(session_id, start_time, upload_order, sport, value)``
FileRow = Sequence[Any]
# ``(entry_id, effective_from, value)``
EntryRow = Sequence[Any]


def _block(**values: Any) -> dict:
    return {**EMPTY_VALUE, "entry_id": None, "effective_from": None, "out_of_period": False, **values}


def _anchor_block(anchor: profile.Anchor, *, out_of_period: bool) -> dict:
    """F017's anchor block (``db._from_anchor``'s shape) with the three F021 keys."""
    if anchor.reason is not None:
        return _block(anchor_unavailable=anchor.reason, out_of_period=out_of_period)
    return _block(
        value=anchor.value, source="anchor", anchor_version=anchor.version, out_of_period=out_of_period
    )


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"effective_from {value!r} is a datetime; a dated entry carries a date")
    return value if isinstance(value, date) else date.fromisoformat(value)


def _latest_file(
    field: str, run_instant: datetime, zone: ZoneInfo, files: Iterable[FileRow]
) -> tuple[FileRow, date] | None:
    best: tuple[tuple[datetime, int], FileRow, date] | None = None
    for row in files:
        session_id, start_time, upload_order, sport, value = row
        if value is None:
            continue
        if field in profile.RUNNING_ONLY_FIELDS and sport != profile.RUNNING_SPORT:
            continue
        local_date, instant = local_day(session_id, start_time, zone)
        if instant > run_instant:
            continue
        key = (instant, upload_order or 0)
        if best is None or key > best[0]:
            best = (key, row, local_date)
    return None if best is None else (best[1], best[2])


def _latest_entry(run_date: date, entries: Iterable[EntryRow]) -> tuple[EntryRow, date] | None:
    best: tuple[tuple[date, int], EntryRow, date] | None = None
    for row in entries:
        entry_id, effective_from, _value = row
        effective = _as_date(effective_from)
        if effective > run_date:
            continue
        key = (effective, entry_id)
        if best is None or key > best[0]:
            best = (key, row, effective)
    return None if best is None else (best[1], best[2])


def borrow(
    field: str,
    run_start_utc: str,
    zone: ZoneInfo,
    files: Iterable[FileRow],
    entries: Iterable[EntryRow],
    resolved: profile.FieldValue,
    anchor: profile.Anchor,
) -> dict:
    """The ``inputs.<field>`` block for an HR field the run's own file lacks (see the module docstring).

    ``run_start_utc`` is the run's stored ``start_time`` (aware ISO 8601); ``files`` are the stored
    sessions carrying candidates for ``field``; ``entries`` are that field's dated entries;
    ``resolved`` and ``anchor`` are F016's resolution and served anchor for ``field``.
    """
    if field not in HR_FIELDS:
        raise ValueError(f"no dated HR history for field {field!r}; only {', '.join(HR_FIELDS)}")

    run_date, run_instant = local_day("run", run_start_utc, zone)
    file_hit = _latest_file(field, run_instant, zone, files)
    entry_hit = _latest_entry(run_date, entries)

    if file_hit is not None and (entry_hit is None or file_hit[1] >= entry_hit[1]):
        (session_id, _start, _order, _sport, value), _ = file_hit
        if resolved.source == "fit" and resolved.session_id == session_id:
            return _anchor_block(anchor, out_of_period=False)
        return _block(value=value, source=SOURCE_SESSION_FILE_DATED, session_id=session_id)

    if entry_hit is not None:
        (entry_id, effective_from, value), _ = entry_hit
        return _block(value=value, source=SOURCE_HISTORY, entry_id=entry_id, effective_from=effective_from)

    out_of_period = resolved.source == "fit" and anchor.reason is None
    return _anchor_block(anchor, out_of_period=out_of_period)
