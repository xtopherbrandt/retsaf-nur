"""Ingestion orchestration: FIT bytes in, a persisted session out.

Pure orchestration -- every step below raises one of
``ingestion.exceptions``'s typed exceptions on failure; this module
never raises ``HTTPException`` itself (that translation happens only
in ``main.py``). This module and ``main.py`` are the only two files
later F003 tasks should never need to touch again -- each of them
fills in exactly one of the stub modules called below.
"""

from __future__ import annotations

from dataclasses import dataclass

from runcoach_api import db
from runcoach_api.ingestion import (
    fit_parser,
    hrv_classification,
    mapping,
    quality_gates,
    quarantine,
    rr_reconstruction,
)


@dataclass
class IngestResult:
    session_id: str
    quality_flags: list[str]


def ingest_fit_bytes(raw: bytes) -> IngestResult:
    messages = fit_parser.decode(raw)
    session, records = mapping.to_canonical(messages)
    # rr_reconstruction runs before quality_gates.apply() so the
    # chest-strap hr_source set during mapping (T024) is already in
    # place before apply()'s "default to wrist_ppg when not already
    # set" guard runs -- see code-review Fix 5.
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
    hrv_classification.classify(messages, session, rr_intervals)
    quality_gates.apply(session, records)
    quarantine_values = quarantine.extract(messages)

    conn = db.get_connection()
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
        db.persist(conn, session, records, rr_intervals, quarantine_values)
    finally:
        conn.close()

    return IngestResult(session_id=session.session_id, quality_flags=session.quality_flags)
