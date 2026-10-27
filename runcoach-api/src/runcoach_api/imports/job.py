"""Run an import job over a scanned archive (F020 reference sections 3-5).

A job is one ``imports`` row and its ``import_files`` report rows; every SQL
statement that reads or writes them lives here.

- **Creating.** ``create_job`` checks for an active job (``queued``,
  ``scanning`` or ``running``) and inserts the new ``queued`` row in one
  ``BEGIN IMMEDIATE`` transaction, so two concurrent requests cannot both
  start; an active job raises ``ImportActive`` naming it. The archive is then
  placed at ``archive_path(data_dir, import_id)``, inside the job's temp dir
  ``<data dir>/imports/<import_id>/``.
- **Running.** ``run_job`` works synchronously: ``scanning`` (the archive
  reader screens every FIT file, spilling activities into the temp dir), then
  ``running`` (each activity in processing order through
  ``pipeline.ingest_fit_bytes``), then ``completed``. The data dir, the
  resting-HRV profile names and the athlete's zone are captured once by the
  caller and handed to every per-file call with the module's
  ``STORE_TIMEOUT_S``; the job never re-reads the config, so a worker cannot
  write to a store other than the one it started with. Each file opens its own
  connection.
- **Keep or skip (reference section 3).** ``keep_session``: canonical sport
  ``running``, or a recognised resting capture (``activity_tag``
  ``resting_hrv_check`` or ``health_snapshot``). A skipped file writes nothing
  to the store but its report row, which names the sport as the FIT file gives
  it (``provenance["raw_sport_value"]``; a number becomes its string).
- **Outcomes (reference section 4).** A stored file's report row is written
  by ``after_write``, inside the session's own transaction, together with the
  session's ``import_id`` and the job's progress and counts, so neither the
  session nor its row exists without the other. ``DuplicateSessionError`` is
  ``already_stored`` with the existing id; any other exception fails that row
  only, with ``ingestion.exceptions.http_status_detail``'s status and detail
  ("database is locked" for a store held past the timeout). Those rows, and
  their progress, are written in their own short transaction.
- **Nights (F028 reference section 6).** An HRV_STATUS file (an
  ``archive.Activity`` of ``kind`` ``"night"``) goes through
  ``overnight.map_night`` and ``db.persist_night`` instead of the pipeline,
  counted in the total and the progress like an activity. Its row is
  ``stored`` or ``replaced`` (a re-summary that replaced a stored night),
  written by ``persist_night``'s ``after_write`` inside the night's
  transaction; ``no_night`` for a file with no night; ``already_stored`` and
  ``failed`` as above. A night's row names ``sport`` ``other`` and
  ``activity_tag`` ``overnight_hrv``, as a ``no_night`` row does.
- **Counts.** ``replaced`` and ``no_night`` count those rows; a job stored
  before they existed holds counts without them, which ``ImportCounts``
  serves as 0.
- **Ending.** Anything not handled per file marks the job ``failed`` with
  ``error``. The temp dir is removed before the terminal status is written,
  in every outcome. If even that write fails, the job stays active until the
  next startup's ``recover_at_startup`` marks it ``interrupted`` and removes
  its temp dir; that runs only from the app's startup, never from
  ``db.init_schema``, which every file's ingest calls.
- **The worker.** ``start_worker`` runs ``run_job`` on a daemon thread
  (stopping the API stops the import; the file in progress rolls back) and
  keeps it in a module registry; ``wait_idle`` joins every registered worker
  within a timeout.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from runcoach_api import db
from runcoach_api.imports import archive
from runcoach_api.ingestion import overnight, pipeline
from runcoach_api.ingestion.exceptions import DuplicateSessionError, http_status_detail
from runcoach_api.models import Session

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "scanning", "running")
KEEP_TAGS = frozenset({"resting_hrv_check", "health_snapshot"})
# A night's report row names it as the stored night does (``overnight.map_night``).
NIGHT_SPORT = "other"
NIGHT_TAG = overnight.OVERNIGHT_TAG
# Seconds a store connection waits on another connection's lock before
# "database is locked" (sqlite3's own default); tests lower it.
STORE_TIMEOUT_S = 5.0
# fit_files_scanned is written to the job row every this many candidates while scanning.
SCAN_PROGRESS_EVERY = 100
ARCHIVE_FILENAME = "archive.zip"

_ACTIVE = ", ".join(f"'{s}'" for s in ACTIVE_STATUSES)


class ImportActive(Exception):
    """A job is already ``queued``, ``scanning`` or ``running``; ``import_id`` names it."""

    def __init__(self, import_id: str) -> None:
        super().__init__(f"import {import_id} is active")
        self.import_id = import_id


def job_dir(data_dir: Path, import_id: str) -> Path:
    """The job's temp dir, ``<data dir>/imports/<import_id>``."""
    return Path(data_dir) / "imports" / import_id


def archive_path(data_dir: Path, import_id: str) -> Path:
    """Where the uploaded archive is placed for the job to read."""
    return job_dir(data_dir, import_id) / ARCHIVE_FILENAME


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _empty_counts() -> dict:
    return {
        "stored": 0,
        "replaced": 0,
        "already_stored": 0,
        "no_night": 0,
        "failed": 0,
        "skipped_sport": {},
        "non_activity": {},
        "not_fit": {},
        "unreadable_archives": [],
    }


def create_job(conn: sqlite3.Connection, archive_name: str, archive_bytes: int | None) -> str:
    """Insert a ``queued`` job and return its id; ``ImportActive`` when one is active.

    The check and the insert are one ``BEGIN IMMEDIATE`` transaction: the
    write lock is taken before the check, so a second caller waits and then
    sees the first job.
    """
    import_id = uuid.uuid4().hex
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        active = conn.execute(
            f"SELECT import_id FROM imports WHERE status IN ({_ACTIVE}) ORDER BY created_at LIMIT 1"
        ).fetchone()
        if active is not None:
            raise ImportActive(active[0])
        conn.execute(
            "INSERT INTO imports (import_id, archive_name, archive_bytes, status, created_at,"
            " fit_files_scanned, activities_total, activities_processed, counts)"
            " VALUES (?, ?, ?, 'queued', ?, 0, 0, 0, ?)",
            (import_id, archive_name, archive_bytes, _now(), json.dumps(_empty_counts())),
        )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return import_id


def discard_job(conn: sqlite3.Connection, data_dir: Path, import_id: str) -> None:
    """Remove a job that never started (a refused upload): its rows and its temp dir.

    The dir is removed even when the DELETEs fail, so a full disk still frees the partial archive.
    """
    try:
        with conn:
            conn.execute("DELETE FROM import_files WHERE import_id = ?", (import_id,))
            conn.execute("DELETE FROM imports WHERE import_id = ?", (import_id,))
    finally:
        shutil.rmtree(job_dir(data_dir, import_id), ignore_errors=True)


def _import_body(row: sqlite3.Row) -> dict:
    return {
        "import_id": row["import_id"],
        "archive_name": row["archive_name"],
        "archive_bytes": row["archive_bytes"],
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "progress": {
            "fit_files_scanned": row["fit_files_scanned"],
            "activities_total": row["activities_total"],
            "activities_processed": row["activities_processed"],
        },
        "counts": json.loads(row["counts"]) if row["counts"] else _empty_counts(),
    }


def _file_body(row: sqlite3.Row) -> dict:
    return {
        "seq": row["seq"],
        "entry": row["entry"],
        "time_created": row["time_created"],
        "outcome": row["outcome"],
        "session_id": row["stored_session_id"],
        "sport": row["sport"],
        "activity_tag": row["activity_tag"],
        "quality_flags": json.loads(row["quality_flags"]) if row["quality_flags"] is not None else None,
        "status": row["status"],
        "detail": row["detail"],
    }


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """A cursor yielding ``sqlite3.Row``, whatever the connection's own row factory."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor.execute(sql, params)


def read_import(conn: sqlite3.Connection, import_id: str, *, with_files: bool = True) -> dict | None:
    """The job as ``getImport`` serves it (with ``files`` in ``seq`` order), or None."""
    row = _rows(conn, "SELECT * FROM imports WHERE import_id = ?", (import_id,)).fetchone()
    if row is None:
        return None
    body = _import_body(row)
    if with_files:
        rows = _rows(conn, "SELECT * FROM import_files WHERE import_id = ? ORDER BY seq", (import_id,))
        body["files"] = [_file_body(r) for r in rows]
    return body


def list_imports(conn: sqlite3.Connection) -> list[dict]:
    """Every job, newest first, without ``files``."""
    rows = _rows(conn, "SELECT * FROM imports ORDER BY created_at DESC, rowid DESC")
    return [_import_body(row) for row in rows]


def recover_at_startup(conn: sqlite3.Connection, data_dir: Path) -> list[str]:
    """Mark every active job ``interrupted`` (``finished_at`` now) and remove its temp dir.

    Called once from the app's startup, after the schema exists: at that
    moment no worker of this process can be running, so an active job is one
    the previous process left. Its report rows and the sessions it stored stay.
    Returns the ids marked.
    """
    with conn:
        ids = [row[0] for row in conn.execute(f"SELECT import_id FROM imports WHERE status IN ({_ACTIVE})")]
        conn.execute(
            f"UPDATE imports SET status = 'interrupted', finished_at = ? WHERE status IN ({_ACTIVE})",
            (_now(),),
        )
    for import_id in ids:
        shutil.rmtree(job_dir(data_dir, import_id), ignore_errors=True)
    return ids


def keep_session(session: Session) -> bool:
    """Reference section 3: a run, or a recognised resting capture."""
    return session.sport == "running" or session.activity_tag in KEEP_TAGS


def _skip_label(session: Session) -> str:
    """The sport as the FIT file names it (a number as its string)."""
    provenance = session.context.provenance if session.context is not None else {}
    raw = provenance.get("raw_sport_value")
    return str(raw) if raw is not None else session.sport


@dataclass
class FileRow:
    """One ``import_files`` row."""

    seq: int
    entry: str
    time_created: str | None
    outcome: str
    session_id: str | None = None
    sport: str | None = None
    activity_tag: str | None = None
    quality_flags: list[str] | None = None
    status: int | None = None
    detail: str | None = None


def _insert_file_row(conn: sqlite3.Connection, import_id: str, row: FileRow) -> None:
    conn.execute(
        "INSERT INTO import_files (import_id, seq, entry, time_created, outcome, stored_session_id,"
        " sport, activity_tag, quality_flags, status, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            import_id,
            row.seq,
            row.entry,
            row.time_created,
            row.outcome,
            row.session_id,
            row.sport,
            row.activity_tag,
            None if row.quality_flags is None else json.dumps(row.quality_flags),
            row.status,
            row.detail,
        ),
    )


def _write_progress(conn: sqlite3.Connection, import_id: str, processed: int, counts: dict) -> None:
    conn.execute(
        "UPDATE imports SET activities_processed = ?, counts = ? WHERE import_id = ?",
        (processed, json.dumps(counts), import_id),
    )


def _write_stored_row(conn: sqlite3.Connection, row: FileRow, import_id: str, counts: dict) -> None:
    """Inside the session's transaction: its report row, its ``import_id`` and the job's progress."""
    _insert_file_row(conn, import_id, row)
    conn.execute("UPDATE sessions SET import_id = ? WHERE session_id = ?", (import_id, row.session_id))
    _write_progress(conn, import_id, row.seq, counts)


def _bumped(counts: dict, outcome: str, sport: str | None = None) -> dict:
    new = json.loads(json.dumps(counts))
    if outcome == "skipped_sport":
        new["skipped_sport"][sport] = new["skipped_sport"].get(sport, 0) + 1
    else:
        new[outcome] += 1
    return new


@dataclass
class _Job:
    import_id: str
    data_dir: Path
    profile_names: list[str]
    zone: ZoneInfo
    counts: dict = field(default_factory=_empty_counts)

    def connect(self) -> sqlite3.Connection:
        return db.get_connection(self.data_dir, timeout=STORE_TIMEOUT_S)

    def update(self, sql: str, params: Iterable) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute(sql, tuple(params))

    def run(self) -> None:
        self.update(
            "UPDATE imports SET status = 'scanning', started_at = ? WHERE import_id = ?",
            (_now(), self.import_id),
        )
        work = job_dir(self.data_dir, self.import_id)
        scan = archive.scan(archive_path(self.data_dir, self.import_id), work, progress=self.scanned)
        order = scan.processing_order()
        self.counts["non_activity"] = dict(scan.non_activity)
        self.counts["not_fit"] = dict(scan.not_fit)
        self.counts["unreadable_archives"] = [
            {"label": u.label, "reason": u.reason} for u in scan.unreadable_archives
        ]
        self.update(
            "UPDATE imports SET status = 'running', fit_files_scanned = ?, activities_total = ?, counts = ?"
            " WHERE import_id = ?",
            (scan.fit_files_scanned, len(order), json.dumps(self.counts), self.import_id),
        )
        for seq, item in enumerate(order, start=1):
            if isinstance(item, archive.Failed):
                row = FileRow(seq, item.label, None, "failed", status=item.status, detail=item.detail)
                self.finish_file(row)
            else:
                self.ingest(seq, item)

    def scanned(self, n: int) -> None:
        if n % SCAN_PROGRESS_EVERY == 0:
            self.update("UPDATE imports SET fit_files_scanned = ? WHERE import_id = ?", (n, self.import_id))

    def finish_file(self, row: FileRow, sport: str | None = None) -> None:
        """A row that is not ``stored``: the row and the progress in one short transaction."""
        counts = _bumped(self.counts, row.outcome, sport)
        with closing(self.connect()) as conn, conn:
            _insert_file_row(conn, self.import_id, row)
            _write_progress(conn, self.import_id, row.seq, counts)
        self.counts = counts

    def ingest(self, seq: int, activity: archive.Activity) -> None:
        raw = activity.path.read_bytes()
        activity.path.unlink(missing_ok=True)
        stamp = activity.time_created_utc
        time_created = None if stamp is None else stamp.isoformat()
        if activity.kind == "night":
            self.ingest_night(seq, activity.label, time_created, raw)
            return
        seen: dict[str, Session] = {}
        pending: dict[str, dict] = {}

        def keep(session: Session) -> bool:
            seen["session"] = session
            return keep_session(session)

        def after_write(conn: sqlite3.Connection) -> None:
            session = seen["session"]
            row = FileRow(
                seq,
                activity.label,
                time_created,
                "stored",
                session_id=session.session_id,
                sport=session.sport,
                activity_tag=session.activity_tag,
                quality_flags=list(session.quality_flags),
            )
            counts = _bumped(self.counts, "stored")
            _write_stored_row(conn, row, self.import_id, counts)
            pending["counts"] = counts

        try:
            result = pipeline.ingest_fit_bytes(
                raw,
                keep=keep,
                data_dir=self.data_dir,
                profile_names=self.profile_names,
                zone=self.zone,
                timeout=STORE_TIMEOUT_S,
                after_write=after_write,
            )
        except DuplicateSessionError as exc:
            row = FileRow(
                seq, activity.label, time_created, "already_stored", session_id=exc.existing_session_id
            )
            self.finish_file(row)
            return
        except Exception as exc:  # noqa: BLE001 -- any per-file error fails that row only
            status, detail = http_status_detail(exc)
            self.finish_file(
                FileRow(seq, activity.label, time_created, "failed", status=status, detail=detail)
            )
            return
        if result.skipped:
            session = seen["session"]
            label = _skip_label(session)
            row = FileRow(
                seq,
                activity.label,
                time_created,
                "skipped_sport",
                sport=label,
                activity_tag=session.activity_tag,
            )
            self.finish_file(row, sport=label)
            return
        self.counts = pending["counts"]

    def ingest_night(self, seq: int, label: str, time_created: str | None, raw: bytes) -> None:
        """An HRV_STATUS file: ``overnight.map_night``, then ``db.persist_night`` (F028 reference section 6).

        A stored or replaced night's row is written by ``after_write`` inside
        the night's transaction, as a stored activity's is; a file with no
        night is ``no_night``, a duplicate ``already_stored`` and any other
        exception ``failed``, each in its own short transaction.
        """
        pending: dict[str, dict] = {}

        def after_write(conn: sqlite3.Connection, write: db.NightWrite) -> None:
            outcome = "stored" if write.replaced_session_id is None else "replaced"
            row = FileRow(
                seq,
                label,
                time_created,
                outcome,
                session_id=write.session_id,
                sport=NIGHT_SPORT,
                activity_tag=NIGHT_TAG,
                quality_flags=list(night.session.quality_flags),
            )
            counts = _bumped(self.counts, outcome)
            _write_stored_row(conn, row, self.import_id, counts)
            pending["counts"] = counts

        try:
            night = overnight.map_night(raw)
            if isinstance(night, overnight.NoNight):
                row = FileRow(
                    seq, label, time_created, "no_night", sport=NIGHT_SPORT, activity_tag=NIGHT_TAG
                )
                self.finish_file(row)
                return
            # As ingest_fit_bytes opens its connection: the captured data dir and the store timeout.
            with closing(db.get_connection(self.data_dir, timeout=STORE_TIMEOUT_S)) as conn:
                db.init_schema(conn)
                db.persist_night(conn, night, after_write=after_write)
        except DuplicateSessionError as exc:
            row = FileRow(seq, label, time_created, "already_stored", session_id=exc.existing_session_id)
            self.finish_file(row)
            return
        except Exception as exc:  # noqa: BLE001 -- any per-file error fails that row only
            status, detail = http_status_detail(exc)
            self.finish_file(FileRow(seq, label, time_created, "failed", status=status, detail=detail))
            return
        self.counts = pending["counts"]


def run_job(import_id: str, data_dir: Path, profile_names: list[str], zone: ZoneInfo) -> None:
    """Run the job to ``completed`` or ``failed``; its temp dir is removed either way.

    ``data_dir``, ``profile_names`` and ``zone`` are the settings captured at
    job start; nothing here reads the config.
    """
    data_dir = Path(data_dir)
    work = _Job(import_id, data_dir, list(profile_names), zone)
    try:
        work.run()
        status, error = "completed", None
    except Exception as exc:
        # The top-level handler: anything not handled per file fails the job with its cause.
        logger.exception("import %s failed", import_id)
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    shutil.rmtree(job_dir(data_dir, import_id), ignore_errors=True)
    try:
        work.update(
            "UPDATE imports SET status = ?, error = ?, finished_at = ? WHERE import_id = ?",
            (status, error, _now(), import_id),
        )
    except Exception:
        # Left active: the next startup's recover_at_startup marks it interrupted.
        logger.exception("import %s: the terminal status could not be written", import_id)


_workers: list[threading.Thread] = []
_workers_lock = threading.Lock()


def start_worker(
    import_id: str, data_dir: Path, profile_names: list[str], zone: ZoneInfo
) -> threading.Thread:
    """Run ``run_job`` on a daemon thread, registered for ``wait_idle``; returns the started thread.

    The thread is registered only once ``start()`` has returned, so a start that raises leaves
    nothing for ``wait_idle`` to join (joining an unstarted thread raises).
    """
    worker = threading.Thread(
        target=run_job,
        args=(import_id, Path(data_dir), list(profile_names), zone),
        name=f"import-{import_id}",
        daemon=True,
    )
    worker.start()
    with _workers_lock:
        _workers.append(worker)
    return worker


def wait_idle(timeout: float) -> bool:
    """Join every registered worker within ``timeout`` seconds in all; True when none is alive."""
    deadline = time.monotonic() + timeout
    with _workers_lock:
        workers = list(_workers)
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    with _workers_lock:
        _workers[:] = [w for w in _workers if w.is_alive()]
        return not _workers


def set_archive_bytes(conn: sqlite3.Connection, import_id: str, archive_bytes: int) -> None:
    """Record the size of the archive once it has been copied into the job's temp dir.

    ``create_job`` runs before the copy (the active-job check comes first), so
    the row is inserted with the size unknown and given it here.
    """
    with conn:
        conn.execute("UPDATE imports SET archive_bytes = ? WHERE import_id = ?", (archive_bytes, import_id))
