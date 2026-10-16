"""Ingestion orchestration: FIT bytes in, a persisted session out.

Pure orchestration -- every step below raises one of
``ingestion.exceptions``'s typed exceptions on failure; this module
never raises ``HTTPException`` itself (that translation happens only
in ``main.py``). This module and ``main.py`` are the only two files
later F003 tasks should never need to touch again -- each of them
fills in exactly one of the stub modules called below.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from runcoach_api import db
from runcoach_api.ingestion import (
    fit_parser,
    hrv_classification,
    mapping,
    profile_values,
    quality_gates,
    quarantine,
    rr_reconstruction,
)
from runcoach_api.models import Session


@dataclass
class IngestResult:
    session_id: str
    quality_flags: list[str]
    # True when ``keep`` refused the session: nothing was written and no
    # connection was opened; ``session_id`` is the unstored session's id.
    skipped: bool = False


def ingest_fit_bytes(
    raw: bytes,
    *,
    resting_capture_override: bool = False,
    keep: Callable[[Session], bool] | None = None,
    data_dir: Path | None = None,
    profile_names: list[str] | None = None,
    zone: ZoneInfo | None = None,
    timeout: float | None = None,
    after_write: Callable[[sqlite3.Connection], None] | None = None,
) -> IngestResult:
    """Ingest one FIT file's bytes and persist the session it describes.

    ``resting_capture_override`` is F004's upload-time Tier-1 declaration --
    the athlete saying *this* file was a resting capture, for a file recorded
    on an activity profile the config does not name. It is threaded down from
    ``POST /sessions``'s form field and defaults to ``False``, so the CLI
    ingest path, a batch import and F004's own acceptance probe all keep their
    existing meaning.

    The other keyword arguments are F020's archive-import seam; each defaults
    to None, which is the one-file upload's behaviour:

    - ``keep(session)`` runs on the classified session before any connection
      is opened; False returns a result with ``skipped`` True and no store
      touched.
    - ``data_dir`` and ``timeout`` go to ``db.get_connection`` (the data dir
      the import captured at job start; the lock wait in seconds).
    - ``profile_names`` replaces the cached config's
      ``resting_hrv_profile_names``.
    - ``zone`` and ``after_write`` go to ``db.persist``: ``after_write(conn)``
      runs inside the session's transaction, so a fault in it stores nothing.
    """
    messages = fit_parser.decode(raw)
    session, records = mapping.to_canonical(messages)
    # F025: the file's laps and workout steps, measured from the instant
    # record t is; db.persist stores them in the session's transaction.
    laps = mapping.to_laps(messages)
    # F016: the profile settings the file carried ride on the session row,
    # so db.persist writes them in the same transaction as the session.
    for field_name, value in profile_values.extract(messages).items():
        setattr(session, field_name, value)
    # rr_reconstruction runs before quality_gates.apply() so the chest_strap
    # that mapping._infer_hr_source set (from RR, or from a connected
    # heart-rate sensor with HR) is already in place before apply()'s
    # "default to wrist_ppg when not already set" guard -- code-review Fix 5.
    rr_intervals = rr_reconstruction.reconstruct(messages)
    if rr_intervals:
        # §2.2.3/§2.4.3 quality weight -- left None (not 0.0) for a
        # session with no RR stream at all, so "no beats" and "beats,
        # none survived" stay distinguishable downstream.
        session.rr_valid_fraction = rr_reconstruction.valid_fraction(rr_intervals)
    # Unconditional, and deliberately OUTSIDE the `if rr_intervals:`
    # above: a Garmin Health Snapshot carries zero beats, so putting
    # this inside the branch would make the entire Tier-2 resting-HRV
    # path dead code. `rr_intervals` being [] is an input to classify(),
    # not a reason to skip it. Pinned by
    # tests/test_hrv_pipeline_wiring.py. Runs before quality_gates.apply
    # so any flag it raises is already on the session when apply() runs.
    #
    # The athlete's Tier-1 declaration is handed over as explicit keyword
    # arguments (F004's stated seam, T063): `hrv_classification` must not
    # import config, which is what keeps the `test_resting_hrv_*` suites able
    # to drive `classify()` directly. `_load_config_cached` is
    # `lru_cache(maxsize=1)`, so editing `api.toml` needs a server restart to
    # take effect -- documented in F004's amendment as the athlete workflow,
    # not a defect of this call site. A caller that captured the list once
    # (an archive import, per job) passes it as ``profile_names`` instead.
    if profile_names is None:
        profile_names = db._load_config_cached().resting_hrv_profile_names
    hrv_classification.classify(
        messages,
        session,
        rr_intervals,
        profile_names=profile_names,
        resting_capture_override=resting_capture_override,
    )
    quality_gates.apply(session, records)
    quarantine_values = quarantine.extract(messages)

    if keep is not None and not keep(session):
        return IngestResult(session_id=session.session_id, quality_flags=session.quality_flags, skipped=True)

    # Only the arguments given are passed, so a one-file upload calls
    # get_connection() and persist() exactly as before this seam existed.
    connection_kwargs: dict = {}
    if data_dir is not None:
        connection_kwargs["data_dir"] = data_dir
    if timeout is not None:
        connection_kwargs["timeout"] = timeout
    persist_kwargs: dict = {}
    if after_write is not None:
        persist_kwargs["after_write"] = after_write
    if zone is not None:
        persist_kwargs["zone"] = zone

    conn = db.get_connection(**connection_kwargs)
    try:
        # Idempotent, and the reason it is here rather than only in
        # main.py's lifespan: the lifespan runs on *server startup*, so
        # any caller that drives ingestion without booting the ASGI app
        # -- the CLI ingest path, a batch import, F004's acceptance
        # probe -- hits a data dir whose sessions table was never
        # created and fails with a bare "no such table: sessions". It
        # also means a column added to _SCHEMA_DDL (F004 adds three) is
        # reconciled onto a pre-existing database by the first upload
        # after the upgrade, not only by the next server restart.
        db.init_schema(conn)
        # db.persist() is the single place a raw sqlite3 constraint
        # violation is translated into DuplicateSessionError (see
        # code-review Fix 2) -- no local except clause here duplicating
        # that translation (see code-review Fix 6).
        db.persist(conn, session, records, rr_intervals, quarantine_values, laps=laps, **persist_kwargs)
    finally:
        conn.close()

    return IngestResult(session_id=session.session_id, quality_flags=session.quality_flags)
