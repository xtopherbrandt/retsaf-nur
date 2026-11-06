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
- **Step 0, absent rows.** Before any row is matched, every stored, not-yet-marked
  ``strava_activities`` row whose ``Activity ID`` this archive does not give (parsed or not) is
  marked ``deleted_in_strava`` and unlinked (``match`` and ``warnings`` cleared, ``outcome``
  ``deleted_in_strava``, every cell kept), each with its report row (``entry``
  ``activities.csv#<Activity ID>``, ``seq`` after the CSV's rows, in ``Activity ID`` order) in one
  transaction. A row already marked and still absent is neither changed nor reported.
- **Step 0, stored rows** (``_Run.already``), for a row whose ``Activity ID`` is stored and not
  marked: a live link is kept with its ``match``, reading ``unchanged`` (nothing written) when every
  cell equals the stored one in header order, else ``updated`` (cells replaced, ``distance_differs``
  re-checked, ``import_id`` and ``updated_at`` this import's). A linked row stored ``failed`` is
  processed again whether or not its cells changed: ``updated``, its link and ``match`` kept, its
  stored outcome the one its link gives (``_linked_outcome``) and ``distance_differs`` re-checked
  (athlete ruling 2026-11-06). A link to a session no longer stored,
  or an unlinked row whose cells changed, is matched from step 1 and the row rewritten. An unlinked
  stored ``failed`` row is matched from step 1 whether or not its cells changed, its file read
  again, and reports what it gives (``failed`` again when it still fails). Every other unlinked,
  unchanged row re-runs step 1 on the sessions at its CSV start, reading no file: it links when
  step 1 finds one free session (a session stored since, a tie resolved), else reads ``unchanged``,
  nothing decoded or written (athlete rulings 2026-11-06). A marked row present again is matched
  from step 1, which clears the mark. A row that did not parse is ``failed`` whatever its stored
  row holds, never ``unchanged`` (a row one cell longer than the header keyed by the header reads
  equal to its stored cells, and is still a row that did not parse); a live link survives it,
  keeping the fields its row parsed and replacing only ``columns``. A row whose ``Activity ID``
  does not parse cannot be found by it, so its stored row reads absent and is marked
  ``deleted_in_strava``.
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
  An exception while a row is decided or written fails that row only: ``failed``, a stored live
  link kept with its ``match`` and warnings; on a row that did not parse, its ``detail`` is the
  parse failure and the exception's, and no ``strava_activities`` row is written.
- **The report.** ``imports.source`` ``strava``; each ``files[]`` row's ``entry`` is the
  ``Filename`` (``activities.csv#<Activity ID>`` with none; ``activities.csv line <n>`` for a row
  that did not parse), ``time_created`` the ``Activity Date``, ``sport`` the ``Activity Type``,
  ``detail`` the decision's (``candidates: ...``, ``warnings: distance_differs``, the failure).
  ``counts.strava`` counts each outcome and the ``distance_differs`` rows; the top-level
  ``stored``, ``already_stored`` and ``failed`` count the rows with those outcomes and ``not_fit``
  the GPX and TCX runs by kind; the rest stay 0.
- **The load reconcile (F031).** After every row has committed, ``_reconcile_load`` runs
  ``db.reconcile_strava_load`` in the job's zone: each stored session whose saved load disagrees
  with the Strava rule is recomputed in its own transaction, ``counts.strava``'s
  ``excluded_from_load`` and ``restored_to_load`` count the bodies that moved into or out of
  ``strava_not_running``, and ``imports.load_changes`` lists them (served as
  ``Import.load_changes``). A job interrupted after its rows commit but before the reconcile runs
  leaves those loads as they were; the next Strava import's reconcile, which reads every stored
  session and not only that import's rows, recomputes them.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from runcoach_api import db
from runcoach_api.imports import archive, job, strava_csv, strava_files, strava_match
from runcoach_api.imports.strava_types import (
    ALREADY_STORED,
    AMBIGUOUS,
    ATTACHED,
    ATTACHED_BY_FILE_START,
    DELETED_IN_STRAVA,
    DESCRIPTION_COLUMN,
    DISTANCE_DIFFERS,
    FAILED,
    MATCH_CSV_START,
    MATCH_FILE_START,
    MATCH_STORED,
    NOT_FIT,
    PRIVATE_NOTE_COLUMN,
    STORED,
    STORED_RETYPED,
    STRAVA_OUTCOMES,
    UNCHANGED,
    UPDATED,
    CsvRow,
    Decision,
    FileError,
)
from runcoach_api.ingestion import pipeline
from runcoach_api.ingestion.exceptions import DuplicateSessionError, http_status_detail
from runcoach_api.models import Session

# The largest value SQLite's INTEGER holds: an Activity ID past it cannot be stored.
SQLITE_INTEGER_MAX = 2**63 - 1
DUPLICATE_ACTIVITY_ID = "duplicate_activity_id"
# counts.strava's load keys, written once the rows have committed (``_reconcile_load``).
EXCLUDED_FROM_LOAD = "excluded_from_load"
RESTORED_TO_LOAD = "restored_to_load"
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


def _same_cells(stored: Mapping, columns: Mapping[str, str]) -> bool:
    """Every stored cell equals the new row's, in the same header order."""
    return list((stored["columns"] or {}).items()) == list(columns.items())


def _session_exists(conn: sqlite3.Connection, session_id: str) -> bool:
    return conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone() is not None


def _absent_ids(conn: sqlite3.Connection, present: Collection[int]) -> list[int]:
    """The stored, not-yet-marked rows whose ``Activity ID`` this archive does not give, by ID."""
    rows = conn.execute(
        f"SELECT activity_id FROM {db.STRAVA_ACTIVITIES_TABLE} WHERE deleted_in_strava = 0 ORDER BY activity_id"
    )
    return [row[0] for row in rows if row[0] not in present]


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


# The outcome a live link's ``match`` gives, for a linked row stored ``failed`` that is processed
# again; ``stored`` reads its outcome from the report row that made the link when there is one.
_OUTCOME_OF_MATCH = {MATCH_CSV_START: ATTACHED, MATCH_FILE_START: ATTACHED_BY_FILE_START, MATCH_STORED: STORED}
_LINKING_OUTCOMES = (ATTACHED, ATTACHED_BY_FILE_START, STORED, STORED_RETYPED, ALREADY_STORED)


def _linked_outcome(conn: sqlite3.Connection, activity_id: int, session_id: str, match: str | None) -> str:
    """The outcome of the row's link to ``session_id``: the latest report row that linked them
    with a linking outcome (so ``stored_retyped`` and ``already_stored`` are kept apart from
    ``stored``), else the one ``match`` gives (``attached`` when ``match`` is unknown)."""
    placeholders = ", ".join("?" for _ in _LINKING_OUTCOMES)
    found = conn.execute(
        "SELECT outcome FROM import_files WHERE strava_activity_id = ? AND stored_session_id = ?"
        f" AND outcome IN ({placeholders}) ORDER BY rowid DESC LIMIT 1",
        (activity_id, session_id, *_LINKING_OUTCOMES),
    ).fetchone()
    if found is not None:
        return found[0]
    return _OUTCOME_OF_MATCH.get(match, ATTACHED)  # type: ignore[arg-type]


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
            if not self.already(row, base):
                self.decide(row, base)
        except Exception as exc:  # noqa: BLE001 -- any per-row error fails that row only
            status, detail = http_status_detail(exc)
            self.fail(row, base, status, detail)

    def fail(self, row: CsvRow, base: dict, status: int | None, detail: str) -> None:
        """A row an exception failed while it was decided or written: ``failed``, its cells
        replaced, and a stored live link kept with its ``match`` and warnings."""
        with closing(self.work.connect()) as conn:
            stored = db.read_strava_row(conn, row.activity_id)
            link = None if stored is None or stored["deleted_in_strava"] else stored["linked_session_id"]
            live = link is not None and _session_exists(conn, link)
        if live:
            record = self.record(row, FAILED, link, stored["match"], stored["warnings"])  # type: ignore[index]
            file_row = job.FileRow(**base, outcome=FAILED, session_id=link, status=status, detail=detail)
        else:
            record = self.record(row, FAILED)
            file_row = job.FileRow(**base, outcome=FAILED, status=status, detail=detail)
        self.finish(record, file_row)

    def already(self, row: CsvRow, base: dict) -> bool:
        """Step 0 for a row whose ``Activity ID`` is stored. True when it is settled here (the
        report row written); False when the row is matched from step 1 (``decide``), which then
        replaces the stored row whole, a ``deleted_in_strava`` mark and a stale link included."""
        # Read with the connection closed before any write (``finish`` opens its own).
        live = retry = False
        outcome = None
        warnings: tuple[str, ...] = ()
        candidates: list[tuple[str, float | None]] = []
        with closing(self.work.connect()) as conn:
            stored = db.read_strava_row(conn, row.activity_id)
            if stored is None or stored["deleted_in_strava"]:
                return False
            same = _same_cells(stored, row.columns)
            link = stored["linked_session_id"]
            if link is not None:
                live = _session_exists(conn, link)
                if live:
                    # A linked row stored failed is processed again, its cells changed or not
                    # (ruling 2026-11-06): its outcome from its link, its warning checked again.
                    retry = stored["outcome"] == FAILED
                    warnings = tuple(stored["warnings"]) if same and not retry else _warnings(conn, link, row)
                    outcome = _linked_outcome(conn, row.activity_id, link, stored["match"]) if retry else stored["outcome"]
            elif same and stored["outcome"] != FAILED:
                candidates = _candidates(conn, row.start_time)
        if link is not None:
            if not live:
                # A link to a deleted session is cleared: matched again from step 1.
                if self.links.get(link) == row.activity_id:
                    del self.links[link]
                return False
            if same and not retry:
                file_row = job.FileRow(**base, outcome=UNCHANGED, session_id=link, detail=_warning_detail(warnings))
                self.finish(None, file_row, warnings)
                return True
            # A live link is kept whatever steps 1-3 would now find; the cells are replaced.
            record = self.record(row, outcome, link, stored["match"], warnings)  # type: ignore[arg-type]
            file_row = job.FileRow(**base, outcome=UPDATED, session_id=link, detail=_warning_detail(warnings))
            self.finish(record, file_row, warnings)
            return True
        if not same or stored["outcome"] == FAILED:
            # Changed cells, or a failed row (ruling 2026-11-06): matched again from step 1, file read.
            return False
        detail = None
        if candidates:
            # An unchanged unlinked row: step 1 again, no file read (ruling 2026-11-06); it links when
            # step 1 now finds one free session, as a tie that is resolved.
            decision = strava_match._link(
                candidates, row, _LinkedByOthers(self.links, row.activity_id), ATTACHED, MATCH_CSV_START
            )
            if decision.outcome != AMBIGUOUS:
                self.conclude(row, base, decision, None)
                return True
            detail = decision.detail
        self.finish(None, job.FileRow(**base, outcome=UNCHANGED, detail=detail))
        return True

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
        self.conclude(row, base, decision, read.get("error"))

    def conclude(self, row: CsvRow, base: dict, decision: Decision, error: object) -> None:
        """A decision that stores no session: the row's record and report row."""
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
        try:
            self.unparsed(seq, failed)
        except Exception as exc:  # noqa: BLE001 -- any per-row error fails that row only
            status, detail = http_status_detail(exc)
            activity_id = failed.activity_id
            if activity_id is not None and activity_id > SQLITE_INTEGER_MAX:
                activity_id = None
            file_row = job.FileRow(
                seq,
                failed.entry,
                None,
                FAILED,
                status=status,
                detail=f"{failed.detail}; {detail}",
                strava_activity_id=activity_id,
            )
            self.finish(None, file_row)

    def unparsed(self, seq: int, failed: strava_csv.FailedRow) -> None:
        """A row that did not parse: ``failed`` whatever its stored row holds (reference section 2
        item 4); a stored live link is kept with the fields its row parsed before."""

        def cell(key: str) -> str | None:
            index = self.keys.index(key) if key in self.keys else len(failed.cells)
            return failed.cells[index] if index < len(failed.cells) else None

        activity_id = failed.activity_id
        if activity_id is not None and activity_id > SQLITE_INTEGER_MAX:
            activity_id = None
        start_time = strava_csv.parse_activity_date(cell(strava_csv.ACTIVITY_DATE) or "")
        record = None
        file_row = job.FileRow(
            seq,
            failed.entry,
            start_time,
            FAILED,
            sport=cell(strava_csv.ACTIVITY_TYPE),
            detail=failed.detail,
            strava_activity_id=activity_id,
        )
        if activity_id is not None and activity_id not in self.seen:
            self.seen.add(activity_id)
            # The cells keyed by the header, as many as both have: a row longer than the header loses
            # its extra cells here, which is why such a row is never compared with its stored cells
            # (it would read equal to them); it is failed, as every row that did not parse is.
            columns = dict(zip(self.keys, failed.cells, strict=False))
            # Step 0: a live link survives a row that no longer parses.
            with closing(self.work.connect()) as conn:
                stored = db.read_strava_row(conn, activity_id)
                link = None if stored is None else stored["linked_session_id"]
                live = link is not None and _session_exists(conn, link)
            parsed = {
                "start_time": start_time,
                "activity_type": cell(strava_csv.ACTIVITY_TYPE),
                "name": cell(strava_csv.ACTIVITY_NAME),
                "description": columns.get(DESCRIPTION_COLUMN),
                "private_note": columns.get(PRIVATE_NOTE_COLUMN),
                "filename": cell(strava_csv.FILENAME),
            }
            if live:
                # The cells are misaligned or missing: a live link keeps what its row parsed (the
                # type F031's load rule reads, the session's text); only the raw cells are replaced.
                parsed = {key: stored[key] for key in parsed}  # type: ignore[index]
            record = {
                "activity_id": activity_id,
                **parsed,
                "columns": columns,
                "linked_session_id": link if live else None,
                "match": stored["match"] if live else None,  # type: ignore[index]
                "outcome": FAILED,
                "warnings": list(stored["warnings"]) if live else [],  # type: ignore[index]
                "deleted_in_strava": False,
                "import_id": self.work.import_id,
                "updated_at": _now(),
            }
            if live:
                file_row = replace(file_row, session_id=link)
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
        rows_total = len(result.rows) + len(result.failed)
        present = {row.activity_id for row in result.rows} | {
            failed.activity_id for failed in result.failed if failed.activity_id is not None
        }
        with closing(work.connect()) as conn:
            absent = _absent_ids(conn, present)
        total = rows_total + len(absent)
        work.update(
            "UPDATE imports SET status = 'running', source = 'strava', activities_total = ?, counts = ?"
            " WHERE import_id = ?",
            (total, json.dumps(work.counts), work.import_id),
        )
        # Step 0's first part, before any row is matched, so a re-upload can take a freed link.
        for offset, activity_id in enumerate(absent, start=1):
            _mark_deleted(work, activity_id, rows_total + offset)
        with closing(work.connect()) as conn:
            links = _read_links(conn)
        run = _Run(work, zf, prefix, result.keys, links=links)
        seq = 0
        for row in result.rows:
            seq += 1
            run.row(seq, row)
        for failed in result.failed:
            seq += 1
            run.failed(seq, failed)
        if absent:
            work.update(
                "UPDATE imports SET activities_processed = ?, counts = ? WHERE import_id = ?",
                (total, json.dumps(work.counts), work.import_id),
            )
    _reconcile_load(work)


def _reconcile_load(work: job._Job) -> None:
    """After every row has committed: ``db.reconcile_strava_load`` in the job's zone, its counts
    into ``counts.strava`` and its changes into ``imports.load_changes`` (``Import.load_changes``)."""
    result = db.reconcile_strava_load(work.connect, work.zone)
    counts = json.loads(json.dumps(work.counts))
    counts["strava"][EXCLUDED_FROM_LOAD] = result.excluded
    counts["strava"][RESTORED_TO_LOAD] = result.restored
    work.update(
        "UPDATE imports SET counts = ?, load_changes = ? WHERE import_id = ?",
        (json.dumps(counts), json.dumps(list(result.changes)), work.import_id),
    )
    work.counts = counts


def _mark_deleted(work: job._Job, activity_id: int, seq: int) -> None:
    """A stored row absent from this archive: marked ``deleted_in_strava`` and unlinked, its cells
    kept, and its report row (``seq`` after the CSV's rows) written in the same transaction."""
    counts = _bumped(work.counts, DELETED_IN_STRAVA)
    with closing(work.connect()) as conn, conn:
        stored = db.read_strava_row(conn, activity_id)
        if stored is None:  # gone since it was listed: nothing to mark
            return
        record = {
            **stored,
            "linked_session_id": None,
            "match": None,
            "outcome": DELETED_IN_STRAVA,
            "warnings": [],
            "deleted_in_strava": True,
            "import_id": work.import_id,
            "updated_at": _now(),
        }
        db.write_strava_row(conn, record)
        file_row = job.FileRow(
            seq,
            f"{strava_csv.CSV_NAME}#{activity_id}",
            stored["start_time"],
            DELETED_IN_STRAVA,
            sport=stored["activity_type"],
            strava_activity_id=activity_id,
        )
        job._insert_file_row(conn, work.import_id, file_row)
        conn.execute("UPDATE imports SET counts = ? WHERE import_id = ?", (json.dumps(counts), work.import_id))
    work.counts = counts
