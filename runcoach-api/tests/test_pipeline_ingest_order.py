"""Regression (code-review Fixes 5 & 6): ``pipeline.ingest_fit_bytes``
call order and its dead ``IntegrityError`` handler.

Fix 5: ``quality_gates.apply(session, records)`` used to run BEFORE
``rr_reconstruction.reconstruct(...)``. ``quality_gates.apply()`` sets
``session.hr_source = "wrist_ppg"`` as a default whenever ``hr_source``
isn't already set -- so any future chest-strap detection in
``rr_reconstruction`` (currently a stub returning ``[]``) could never
run first and would be permanently unreachable. ``reconstruct()`` must
run before ``apply()`` so the "don't overwrite if already set" guard in
``apply()`` can ever see a chest-strap ``hr_source`` set upstream.

Fix 6: ``db.persist()`` already catches ``sqlite3.IntegrityError`` and
translates it to ``DuplicateSessionError`` internally (see Fix 2/
``test_duplicate_upload.py``) -- so ``pipeline.py``'s own
``except sqlite3.IntegrityError`` clause around ``db.persist(...)`` is
unreachable dead code, and its fallback (``DuplicateSessionError(session.session_id)``)
would misreport the newly-attempted session's own id as the "existing"
one if it ever did fire. It must not exist.
"""

from __future__ import annotations

import inspect
from unittest.mock import Mock, patch

from runcoach_api.ingestion import pipeline


def test_rr_reconstruction_runs_before_quality_gates_apply(monkeypatch) -> None:
    call_order: list[str] = []

    def fake_decode(raw):
        return []

    def fake_to_canonical(messages):
        from runcoach_api.models import Session

        session = Session(
            session_id="s-order-1",
            sport="running",
            source_vendor="garmin",
            start_time="2026-01-01T00:00:00+00:00",
        )
        return session, []

    def fake_apply(session, records):
        call_order.append("quality_gates.apply")

    def fake_reconstruct(messages):
        call_order.append("rr_reconstruction.reconstruct")
        return []

    def fake_extract(messages):
        return {}

    def fake_persist(conn, session, records, rr_intervals, quarantine_values):
        return None

    monkeypatch.setattr(pipeline.fit_parser, "decode", fake_decode)
    monkeypatch.setattr(pipeline.mapping, "to_canonical", fake_to_canonical)
    monkeypatch.setattr(pipeline.quality_gates, "apply", fake_apply)
    monkeypatch.setattr(pipeline.rr_reconstruction, "reconstruct", fake_reconstruct)
    monkeypatch.setattr(pipeline.quarantine, "extract", fake_extract)
    monkeypatch.setattr(pipeline.db, "get_connection", lambda: Mock(close=Mock()))
    # Third db interaction to stub, alongside get_connection/persist:
    # ingest_fit_bytes reconciles the schema on the connection it opens
    # (so a caller that never boots the ASGI app still gets its tables),
    # and the Mock connection above can't answer PRAGMA table_info.
    monkeypatch.setattr(pipeline.db, "init_schema", lambda conn: None)
    monkeypatch.setattr(pipeline.db, "persist", fake_persist)

    pipeline.ingest_fit_bytes(b"irrelevant")

    assert call_order == ["rr_reconstruction.reconstruct", "quality_gates.apply"]


def test_pipeline_has_no_dead_integrity_error_handler() -> None:
    source = inspect.getsource(pipeline.ingest_fit_bytes)

    assert "IntegrityError" not in source
