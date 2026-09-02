"""Tests for T017's walking-skeleton: routes, exception-to-status mapping,
startup wiring, and 500-traceback-leak protection.

No parsing/mapping/gating behavior is under test here -- that's the
scope of later F003 tasks, each of which fills in exactly one stub
module. This file only proves the skeleton (routes exist, exceptions
map to the right HTTP status, startup actually creates the DB) is
wired correctly.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.ingestion.exceptions import (
    DuplicateSessionError,
    FitParseFailure,
    NotAFitFileError,
    OversizedUploadError,
)
from runcoach_api.main import app


def test_sessions_routes_are_registered() -> None:
    paths = {r.path for r in app.routes}
    assert "/sessions" in paths
    assert "/sessions/{session_id}" in paths


def test_app_is_not_running_with_debug_enabled() -> None:
    # debug=True would leak raw tracebacks through FastAPI's default 500
    # handler -- the unhandled-exception test below depends on this being
    # False in production config, not just in the test harness.
    assert app.debug is False


def _post_fit(client: TestClient):
    return client.post(
        "/sessions", files={"file": ("run.fit", io.BytesIO(b"fake-fit-bytes"), "application/octet-stream")}
    )


def test_not_a_fit_file_maps_to_400(monkeypatch) -> None:
    def raise_it(raw: bytes):
        raise NotAFitFileError("missing FIT header")

    monkeypatch.setattr("runcoach_api.main.ingest_fit_bytes", raise_it)

    with TestClient(app) as client:
        response = _post_fit(client)

    assert response.status_code == 400


def test_fit_parse_failure_maps_to_400(monkeypatch) -> None:
    def raise_it(raw: bytes):
        raise FitParseFailure("corrupt record")

    monkeypatch.setattr("runcoach_api.main.ingest_fit_bytes", raise_it)

    with TestClient(app) as client:
        response = _post_fit(client)

    assert response.status_code == 400


def test_duplicate_session_maps_to_409(monkeypatch) -> None:
    def raise_it(raw: bytes):
        raise DuplicateSessionError(existing_session_id="existing-123")

    monkeypatch.setattr("runcoach_api.main.ingest_fit_bytes", raise_it)

    with TestClient(app) as client:
        response = _post_fit(client)

    assert response.status_code == 409
    assert "existing-123" in response.text


def test_oversized_upload_maps_to_413(monkeypatch) -> None:
    def raise_it(raw: bytes):
        raise OversizedUploadError("file too large")

    monkeypatch.setattr("runcoach_api.main.ingest_fit_bytes", raise_it)

    with TestClient(app) as client:
        response = _post_fit(client)

    assert response.status_code == 413


def test_unplanned_exception_does_not_leak_traceback(monkeypatch) -> None:
    def raise_it(raw: bytes):
        raise RuntimeError("something unexpected broke")

    monkeypatch.setattr("runcoach_api.main.ingest_fit_bytes", raise_it)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = _post_fit(client)

    assert response.status_code == 500
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text
    assert "something unexpected broke" not in response.text


def test_startup_creates_data_dir_and_db_file(isolated_data_dir) -> None:
    assert not isolated_data_dir.exists()

    with TestClient(app):
        pass

    assert isolated_data_dir.exists()
    assert (isolated_data_dir / db.DB_FILENAME).exists()
