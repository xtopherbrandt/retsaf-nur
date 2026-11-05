"""The matching rules for one Strava row (F030 reference section 3, steps 1-4). Pure: no I/O, no
store; the caller hands in the candidates and the file reader.

``decide(row, at_csv_start, read_file, at_file_start, linked)``:

- ``at_csv_start``: the stored sessions on the row's ``Activity Date`` second, as
  ``(session_id, distance_m)`` in the order the store lists them (upload order); an ambiguous row's
  ``candidates:`` detail names them in that order.
- ``read_file()``: the row's file's ``session`` starts (the store's UTC ISO spelling, ``None`` for a
  session whose start could not be read), or a ``FileError``. Called at most once, only by step 2:
  a running-family row with no session at its CSV start and a FIT file. A non-running row's file is
  never read.
- ``at_file_start(second)``: the stored sessions starting on that second, as ``at_csv_start``.
- ``linked``: the sessions another row already links (one row per session).

Step 0 (a row whose ``Activity ID`` is already stored) needs the store and is the job's. Step 3
returns ``store=True`` with outcome ``stored`` and ``match`` ``stored``; the job stores the file and
reports ``stored``, ``stored_retyped`` or ``already_stored`` from what the store did.

**Within 1 %** is F023's form, ``|s - d| <= 0.01 * d``, false when either distance is absent or
``d`` is 0; a non-finite distance counts as absent.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Sequence

from runcoach_api.imports.strava_types import (
    AMBIGUOUS,
    ATTACHED,
    ATTACHED_BY_FILE_START,
    DISTANCE_DIFFERS,
    FAILED,
    MATCH_CSV_START,
    MATCH_FILE_START,
    MATCH_STORED,
    MULTISPORT_NOT_STORED,
    NO_FILE,
    NOT_FIT,
    NOT_RUNNING,
    STORED,
    CsvRow,
    Decision,
    FileError,
)

# Strava's running-family activity types (only ``Run`` occurs on the athlete's archive).
RUNNING_FAMILY = frozenset({"Run", "Trail Run", "Virtual Run"})

# The file-name endings a FIT activity file carries in the archive, plain or gzipped.
_FIT_SUFFIXES = (".fit", ".fit.gz")

# The reason a FIT file with no ``session`` message fails with.
NO_SESSION_MESSAGE = "no_session_message"

Candidate = tuple[str, float | None]


def _distance(value: float | None) -> float | None:
    """``value`` when it is a finite number, else ``None`` (absent)."""
    if value is None or not math.isfinite(value):
        return None
    return value


def within_one_percent(s: float | None, d: float | None) -> bool:
    """``|s - d| <= 0.01 * d``; false when either is absent (or non-finite) or ``d`` is 0."""
    s, d = _distance(s), _distance(d)
    if s is None or d is None or d == 0:
        return False
    return abs(s - d) <= 0.01 * d


def _differs(s: float | None, d: float | None) -> bool:
    """The warning's test: both distances present, ``d`` not 0, and not within 1 %."""
    s, d = _distance(s), _distance(d)
    return s is not None and d is not None and d != 0 and not within_one_percent(s, d)


def _ambiguous(candidates: Sequence[Candidate]) -> Decision:
    return Decision(outcome=AMBIGUOUS, detail="candidates: " + ", ".join(sid for sid, _ in candidates))


def _link(candidates: Sequence[Candidate], row: CsvRow, linked: Collection[str], outcome: str,
          match: str) -> Decision:
    """Step 1's rule on a non-empty candidate list: one attaches; two or more, the single one within
    1 %, else ambiguous. A pick another row already links is ambiguous too, linking nothing."""
    if len(candidates) == 1:
        pick = candidates[0]
    else:
        close = [c for c in candidates if within_one_percent(c[1], row.distance_m)]
        if len(close) != 1:
            return _ambiguous(candidates)
        pick = close[0]
    session_id, distance = pick
    if session_id in linked:
        return _ambiguous(candidates)
    if _differs(distance, row.distance_m):
        return Decision(outcome=outcome, session_id=session_id, match=match, warnings=(DISTANCE_DIFFERS,),
                        detail=f"warnings: {DISTANCE_DIFFERS}")
    return Decision(outcome=outcome, session_id=session_id, match=match)


def _failed(error: FileError) -> Decision:
    return Decision(outcome=FAILED, detail=f"{error.reason}: {error.detail}" if error.detail else error.reason)


def decide(
    row: CsvRow,
    at_csv_start: Sequence[Candidate],
    read_file: Callable[[], Sequence[str | None] | FileError],
    at_file_start: Callable[[str], Sequence[Candidate]],
    linked: Collection[str],
) -> Decision:
    """The outcome of one row by steps 1-4 of F030 reference section 3 (module docstring)."""
    # Step 1: match by the CSV start, any activity type.
    if at_csv_start:
        return _link(at_csv_start, row, linked, ATTACHED, MATCH_CSV_START)
    # Step 4 for every row step 2 does not read.
    if row.activity_type not in RUNNING_FAMILY:
        return Decision(outcome=NOT_RUNNING)
    if not row.filename:
        return Decision(outcome=NO_FILE)
    if not row.filename.lower().endswith(_FIT_SUFFIXES):
        return Decision(outcome=NOT_FIT)
    # Step 2: match by the file's own start.
    starts = read_file()
    if isinstance(starts, FileError):
        return _failed(starts)
    if len(starts) > 1:
        return Decision(outcome=MULTISPORT_NOT_STORED)
    if not starts:
        return _failed(FileError(NO_SESSION_MESSAGE))
    start = starts[0]
    candidates = at_file_start(start) if start is not None else ()
    if candidates:
        return _link(candidates, row, linked, ATTACHED_BY_FILE_START, MATCH_FILE_START)
    # Step 3: no session at either start; the job stores the file.
    return Decision(outcome=STORED, match=MATCH_STORED, store=True)
