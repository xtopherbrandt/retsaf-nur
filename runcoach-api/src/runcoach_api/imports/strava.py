"""Run an import job over a Strava bulk export (F030 reference sections 2-3).

``run_strava(work, path, prefix)`` is the job's second branch: ``_Job.run`` takes it when
``strava_csv.recognise`` finds ``activities.csv`` at the archive's root or in its one folder
(``prefix``), before the Garmin scan. It works synchronously:

- **The CSV.** ``strava_csv.read_activities`` reads ``activities.csv``. A job-level failure (past
  the 50 MB cap, not UTF-8, a required header key missing) raises ``StravaCsvError`` before any row
  is written, and the job fails with ``error`` naming the cause. An error reading the entry's own
  bytes out of the zip (a bad CRC, a stream that does not inflate: ``archive._ENTRY_READ_ERRORS``)
  is turned into the same ``StravaCsvError``, naming it.
- **Order.** The rows that parsed are processed oldest first (``Activity Date``, ties by
  ``Activity ID``, the reader's order), then the rows that did not parse, in file order. ``seq``
  counts both.
- **Each row** goes through ``strava_match.decide`` with the stored sessions on its second
  (``db.sessions_at_second``, projected to ``(session_id, distance_m)``), a file reader
  (``strava_files.read_activity_file`` then ``session_starts``, each start passed as its
  ``isoformat()``, ``None`` kept; the ``FileError`` is kept so a size cap's 413 reaches
  ``files[].status``) and the sessions other rows of the store already link (one row per session).
  A row a step would link to a session another row holds is ``ambiguous``.
- **Storing** (``decide``'s ``store``): ``pipeline.ingest_fit_bytes`` with a
  ``pipeline.SportOverride`` naming the row's ``Activity Name`` on every store, ``keep`` the Garmin
  import's ``keep_session``, and ``after_write`` writing the ``strava_activities`` row, the
  ``import_files`` row, the session's ``import_id`` and the job's counts inside the session's own
  transaction. The outcome is ``stored_retyped`` when the override applied (the session carries
  its flag), else ``stored``. ``DuplicateSessionError`` (the store's ``(source_device,
  start_time)`` key) is ``already_stored``, linked to that session with ``match`` ``stored``,
  unless another row links it (then ``ambiguous``, linking nothing). Any other exception fails
  the row with the status and detail ``POST /sessions`` gives.
- **Every other row** writes its ``strava_activities`` row and its ``import_files`` row in one
  short transaction. A row whose ``Activity ID`` this archive already gave is ``failed``
  (``duplicate_activity_id``) and writes no ``strava_activities`` row. An ``Activity ID`` past
  SQLite's 64-bit ``INTEGER`` is a row that does not parse: ``failed``, ``entry`` ``activities.csv
  line <n>``, no ``strava_activities`` row and a null ``strava_activity_id``. A row that did not
  parse writes its ``strava_activities`` row when its ``Activity ID`` parsed and was not seen.
  An exception while a row is decided or written fails that row only.
- **The report.** ``imports.source`` ``strava``; each ``files[]`` row's ``entry`` is the
  ``Filename`` (``activities.csv#<Activity ID>`` with none; ``activities.csv line <n>`` for a row
  that did not parse), ``time_created`` the ``Activity Date``, ``sport`` the ``Activity Type``,
  ``detail`` the decision's (``candidates: ...``, ``warnings: distance_differs``, the failure).
  ``counts.strava`` counts each outcome and the ``distance_differs`` rows; the top-level
  ``stored``, ``already_stored`` and ``failed`` count the rows with those outcomes and ``not_fit``
  the GPX and TCX runs by kind; the rest stay 0.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from runcoach_api import db
from runcoach_api.imports import archive, job, strava_csv, strava_files, strava_match
from runcoach_api.imports.strava_types import (
    ALREADY_STORED,
    AMBIGUOUS,
    DESCRIPTION_COLUMN,
    DISTANCE_DIFFERS,
    FAILED,
    MATCH_STORED,
    NOT_FIT,
    PRIVATE_NOTE_COLUMN,
    STORED,
    STORED_RETYPED,
    STRAVA_OUTCOMES,
    CsvRow,
    FileError,
)
from runcoach_api.ingestion import pipeline
from runcoach_api.ingestion.exceptions import DuplicateSessionError, http_status_detail
from runcoach_api.models import Session

# The largest value SQLite's INTEGER holds: an Activity ID past it cannot be stored.
SQLITE_INTEGER_MAX = 2**63 - 1
DUPLICATE_ACTIVITY_ID = "duplicate_activity_id"
# The top-level counts a Strava row's outcome also moves (the Garmin import's names).
_TOP_LEVEL = frozenset({STORED, ALREADY_STORED, FAILED})

_SESSION_DISTANCE_SQL = (
    "SELECT CASE WHEN json_valid(summary) THEN json_extract(summary, '$.distance_m') END"
    " FROM sessions WHERE session_id = ?"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


class _LinkedByOthers(Collection[str]):
    """The sessions a row other than ``activity_id`` links (``links``: session id -> Activity ID)."""

    def __init__(self, links: Mapping[str, int], activity_id: int | None) -> None:
        self._links = links
        self._activity_id = activity_id

    def __contains__(self, session_id: object) -> bool:
        holder = self._links.get(session_id)  # type: ignore[arg-type]
        return holder is not None and holder != self._activity_id

    def __iter__(self) -> Iterator[str]:
        return (sid for sid, holder in self._links.items() if holder != self._activity_id)

    def __len__(self) -> int:
        return sum(1 for _ in self)


def _read_links(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT linked_session_id, activity_id FROM {db.STRAVA_ACTIVITIES_TABLE}"
        " WHERE linked_session_id IS NOT NULL"
    )
    return {row[0]: row[1] for row in rows}


def _candidates(conn: sqlite3.Connection, second: str) -> list[tuple[str, float | None]]:
    """The stored sessions on ``second`` as ``decide`` takes them: ``(session_id, distance_m)``."""
    return [(session_id, distance) for session_id, _sport, distance in db.sessions_at_second(conn, second)]


def _warnings(conn: sqlite3.Connection, session_id: str, row: CsvRow) -> tuple[str, ...]:
    found = conn.execute(_SESSION_DISTANCE_SQL, (session_id,)).fetchone()
    distance = None if found is None else found[0]
    return (DISTANCE_DIFFERS,) if strava_match._differs(distance, row.distance_m) else ()


def _warning_detail(warnings: Sequence[str]) -> str | None:
    return f"warnings: {', '.join(warnings)}" if warnings else None


def _empty_strava_counts() -> dict[str, int]:
    return {**dict.fromkeys(STRAVA_OUTCOMES, 0), DISTANCE_DIFFERS: 0}


def _bumped(counts: dict, outcome: str, warnings: Sequence[str] = (), not_fit_kind: str | None = None) -> dict:
    new = json.loads(json.dumps(counts))
    new["strava"][outcome] += 1
    if DISTANCE_DIFFERS in warnings:
        new["strava"][DISTANCE_DIFFERS] += 1
    if outcome in _TOP_LEVEL:
        new[outcome] += 1
    if not_fit_kind is not None:
        new["not_fit"][not_fit_kind] = new["not_fit"].get(not_fit_kind, 0) + 1
    return new


def _write_row(
    conn: sqlite3.Connection,
    import_id: str,
    record: Mapping | None,
    file_row: job.FileRow,
    counts: dict,
    *,
    stored: bool = False,
) -> None:
    """One row's writes, inside the caller's transaction: its ``strava_activities`` row (when it
    has one), its ``import_files`` row and the job's progress; for a stored session also the
    session's ``import_id``."""
    if record is not None:
        db.write_strava_row(conn, record)
    if stored:
        job._write_stored_row(conn, file_row, import_id, counts)
    else:
        job._insert_file_row(conn, import_id, file_row)
        job._write_progress(conn, import_id, file_row.seq, counts)


@dataclass
class _Run:
    work: job._Job
    zf: zipfile.ZipFile
    prefix: str
    keys: tuple[str, ...]
    links: dict[str, int] = field(default_factory=dict)
    seen: set[int] = field(default_factory=set)

    def record(
        self,
        row: CsvRow,
        outcome: str,
        session_id: str | None = None,
        match: str | None = None,
        warnings: Sequence[str] = (),
    ) -> dict:
        return {
            "activity_id": row.activity_id,
            "start_time": row.start_time,
            "activity_type": row.activity_type,
            "name": row.name,
            "description": row.description,
            "private_note": row.private_note,
            "filename": row.filename,
            "columns": dict(row.columns),
            "linked_session_id": session_id,
            "match": match,
            "outcome": outcome,
            "warnings": list(warnings),
            "deleted_in_strava": False,
            "import_id": self.work.import_id,
            "updated_at": _now(),
        }

    def finish(
        self,
        record: Mapping | None,
        file_row: job.FileRow,
        warnings: Sequence[str] = (),
        not_fit_kind: str | None = None,
    ) -> None:
        """A row that stores no session: its writes and the progress in one short transaction."""
        counts = _bumped(self.work.counts, file_row.outcome, warnings, not_fit_kind)
        with closing(self.work.connect()) as conn, conn:
            _write_row(conn, self.work.import_id, record, file_row, counts)
        self.work.counts = counts
        if record is not None and record["linked_session_id"] is not None:
            self.links[record["linked_session_id"]] = record["activity_id"]

    # --- rows that parsed ---------------------------------------------------------------------

    def row(self, seq: int, row: CsvRow) -> None:
        entry = row.filename or f"{strava_csv.CSV_NAME}#{row.activity_id}"
        base = {"seq": seq, "entry": entry, "time_created": row.start_time, "sport": row.activity_type}
        if row.activity_id > SQLITE_INTEGER_MAX:
            detail = (
                f"{strava_csv.ACTIVITY_ID} is past the 64-bit integer range: "
                f"{strava_csv.ACTIVITY_ID} {_quote(row.columns[strava_csv.ACTIVITY_ID])}, "
                f"{strava_csv.ACTIVITY_DATE} {_quote(row.columns[strava_csv.ACTIVITY_DATE])}, "
                f"{strava_csv.ACTIVITY_NAME} {_quote(row.name)}"
            )
            base["entry"] = f"{strava_csv.CSV_NAME} line {row.line}"
            self.finish(None, job.FileRow(**base, outcome=FAILED, detail=detail))
            return
        base["strava_activity_id"] = row.activity_id
        if row.activity_id in self.seen:
            self.finish(None, job.FileRow(**base, outcome=FAILED, detail=DUPLICATE_ACTIVITY_ID))
            return
        self.seen.add(row.activity_id)
        try:
            self.decide(row, base)
        except Exception as exc:  # noqa: BLE001 -- any per-row error fails that row only
            status, detail = http_status_detail(exc)
            self.finish(self.record(row, FAILED), job.FileRow(**base, outcome=FAILED, status=status, detail=detail))

    def decide(self, row: CsvRow, base: dict) -> None:
        read: dict[str, object] = {}

        def read_file() -> list[str | None] | FileError:
            data = strava_files.read_activity_file(self.zf, self.prefix, row.filename)
            if isinstance(data, FileError):
                read["error"] = data
                return data
            starts = strava_files.session_starts(data)
            if isinstance(starts, FileError):
                read["error"] = starts
                return starts
            read["bytes"] = data
            return [None if start is None else start.isoformat() for start in starts]

        with closing(self.work.connect()) as conn:
            decision = strava_match.decide(
                row,
                _candidates(conn, row.start_time),
                read_file,
                lambda second: _candidates(conn, second),
                _LinkedByOthers(self.links, row.activity_id),
            )
        if decision.store:
            self.store(row, base, read["bytes"])  # type: ignore[arg-type]
            return
        error = read.get("error")
        status = error.status if decision.outcome == FAILED and isinstance(error, FileError) else None
        kind = None
        if decision.outcome == NOT_FIT:
            kind = strava_files.file_kind(row.filename) or archive._extension(row.filename)
        record = self.record(row, decision.outcome, decision.session_id, decision.match, decision.warnings)
        file_row = job.FileRow(
            **base, outcome=decision.outcome, session_id=decision.session_id, status=status, detail=decision.detail
        )
        self.finish(record, file_row, decision.warnings, kind)

    def store(self, row: CsvRow, base: dict, raw: bytes) -> None:
        """Step 3: the row's file through ``POST /sessions``' steps, the override on every store."""
        work = self.work
        override = pipeline.SportOverride(name=row.name)
        seen: dict[str, Session] = {}
        pending: dict[str, object] = {}

        def keep(session: Session) -> bool:
            seen["session"] = session
            return job.keep_session(session)

        def after_write(conn: sqlite3.Connection) -> None:
            session = seen["session"]
            outcome = STORED_RETYPED if override.flag in session.quality_flags else STORED
            warnings = _warnings(conn, session.session_id, row)
            record = self.record(row, outcome, session.session_id, MATCH_STORED, warnings)
            file_row = job.FileRow(
                **base,
                outcome=outcome,
                session_id=session.session_id,
                activity_tag=session.activity_tag,
                quality_flags=list(session.quality_flags),
                detail=_warning_detail(warnings),
            )
            counts = _bumped(work.counts, outcome, warnings)
            _write_row(conn, work.import_id, record, file_row, counts, stored=True)
            pending["counts"] = counts

        try:
            result = pipeline.ingest_fit_bytes(
                raw,
                keep=keep,
                data_dir=work.data_dir,
                profile_names=work.profile_names,
                zone=work.zone,
                timeout=job.STORE_TIMEOUT_S,
                after_write=after_write,
                override=override,
            )
        except DuplicateSessionError as exc:
            self.already_stored(row, base, exc.existing_session_id)
            return
        except Exception as exc:  # noqa: BLE001 -- a file POST /sessions refuses fails that row only
            status, detail = http_status_detail(exc)
            self.finish(self.record(row, FAILED), job.FileRow(**base, outcome=FAILED, status=status, detail=detail))
            return
        if result.skipped:
            detail = f"not kept: sport {job._skip_label(seen['session'])}"
            self.finish(self.record(row, FAILED), job.FileRow(**base, outcome=FAILED, detail=detail))
            return
        work.counts = pending["counts"]  # type: ignore[assignment]
        self.links[result.session_id] = row.activity_id

    def already_stored(self, row: CsvRow, base: dict, session_id: str) -> None:
        """A store refused by the ``(source_device, start_time)`` key attaches to that session,
        unless another row links it (one row per session)."""
        if session_id in _LinkedByOthers(self.links, row.activity_id):
            record = self.record(row, AMBIGUOUS)
            self.finish(record, job.FileRow(**base, outcome=AMBIGUOUS, detail=f"candidates: {session_id}"))
            return
        with closing(self.work.connect()) as conn:
            warnings = _warnings(conn, session_id, row)
        record = self.record(row, ALREADY_STORED, session_id, MATCH_STORED, warnings)
        file_row = job.FileRow(
            **base, outcome=ALREADY_STORED, session_id=session_id, detail=_warning_detail(warnings)
        )
        self.finish(record, file_row, warnings)

    # --- rows that did not parse --------------------------------------------------------------

    def failed(self, seq: int, failed: strava_csv.FailedRow) -> None:
        def cell(key: str) -> str | None:
            index = self.keys.index(key) if key in self.keys else len(failed.cells)
            return failed.cells[index] if index < len(failed.cells) else None

        activity_id = failed.activity_id
        if activity_id is not None and activity_id > SQLITE_INTEGER_MAX:
            activity_id = None
        start_time = strava_csv.parse_activity_date(cell(strava_csv.ACTIVITY_DATE) or "")
        record = None
        if activity_id is not None and activity_id not in self.seen:
            self.seen.add(activity_id)
            columns = dict(zip(self.keys, failed.cells, strict=False))
            record = {
                "activity_id": activity_id,
                "start_time": start_time,
                "activity_type": cell(strava_csv.ACTIVITY_TYPE),
                "name": cell(strava_csv.ACTIVITY_NAME),
                "description": columns.get(DESCRIPTION_COLUMN),
                "private_note": columns.get(PRIVATE_NOTE_COLUMN),
                "filename": cell(strava_csv.FILENAME),
                "columns": columns,
                "linked_session_id": None,
                "match": None,
                "outcome": FAILED,
                "warnings": [],
                "deleted_in_strava": False,
                "import_id": self.work.import_id,
                "updated_at": _now(),
            }
        file_row = job.FileRow(
            seq,
            failed.entry,
            start_time,
            FAILED,
            sport=cell(strava_csv.ACTIVITY_TYPE),
            detail=failed.detail,
            strava_activity_id=activity_id,
        )
        self.finish(record, file_row)


def _read_csv(zf: zipfile.ZipFile, prefix: str) -> strava_csv.CsvResult:
    """``read_activities``, with an error reading the entry's bytes raised as ``StravaCsvError``."""
    try:
        return strava_csv.read_activities(zf, prefix)
    except strava_csv.StravaCsvError:
        raise
    except archive._ENTRY_READ_ERRORS as exc:
        raise strava_csv.StravaCsvError(
            f"{prefix}{strava_csv.CSV_NAME} could not be read: {strava_files._error_name(exc)}: {exc}"
        ) from exc


def run_strava(work: job._Job, path: Path, prefix: str) -> None:
    """Import the Strava archive at ``path`` whose ``activities.csv`` sits under ``prefix``."""
    with zipfile.ZipFile(path) as zf:
        result = _read_csv(zf, prefix)
        work.counts["strava"] = _empty_strava_counts()
        total = len(result.rows) + len(result.failed)
        with closing(work.connect()) as conn:
            links = _read_links(conn)
        work.update(
            "UPDATE imports SET status = 'running', source = 'strava', activities_total = ?, counts = ?"
            " WHERE import_id = ?",
            (total, json.dumps(work.counts), work.import_id),
        )
        run = _Run(work, zf, prefix, result.keys, links=links)
        seq = 0
        for row in result.rows:
            seq += 1
            run.row(seq, row)
        for failed in result.failed:
            seq += 1
            run.failed(seq, failed)
