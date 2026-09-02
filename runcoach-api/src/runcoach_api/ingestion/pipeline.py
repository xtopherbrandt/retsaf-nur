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
from dataclasses import dataclass

from runcoach_api import db
from runcoach_api.ingestion import (
    fit_parser,
    mapping,
    quality_gates,
    quarantine,
    rr_reconstruction,
)
from runcoach_api.ingestion.exceptions import DuplicateSessionError


@dataclass
class IngestResult:
    session_id: str
    quality_flags: list[str]


def ingest_fit_bytes(raw: bytes) -> IngestResult:
    messages = fit_parser.decode(raw)
    session, records = mapping.to_canonical(messages)
    quality_gates.apply(session, records)
    rr_intervals = rr_reconstruction.reconstruct(messages)
    quarantine_values = quarantine.extract(messages)

    conn = db.get_connection()
    try:
        db.persist(conn, session, records, rr_intervals, quarantine_values)
    except sqlite3.IntegrityError as exc:
        raise DuplicateSessionError(session.session_id) from exc
    finally:
        conn.close()

    return IngestResult(session_id=session.session_id, quality_flags=session.quality_flags)
