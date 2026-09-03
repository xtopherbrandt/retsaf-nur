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
    # rr_reconstruction runs before quality_gates.apply() so a future
    # chest-strap hr_source detection (rr_reconstruction is currently a
    # stub) can set session.hr_source before apply()'s "default to
    # wrist_ppg when not already set" guard runs -- see code-review
    # Fix 5.
    rr_intervals = rr_reconstruction.reconstruct(messages)
    quality_gates.apply(session, records)
    quarantine_values = quarantine.extract(messages)

    conn = db.get_connection()
    try:
        # db.persist() is the single place a raw sqlite3 constraint
        # violation is translated into DuplicateSessionError (see
        # code-review Fix 2) -- no local except clause here duplicating
        # that translation (see code-review Fix 6).
        db.persist(conn, session, records, rr_intervals, quarantine_values)
    finally:
        conn.close()

    return IngestResult(session_id=session.session_id, quality_flags=session.quality_flags)
