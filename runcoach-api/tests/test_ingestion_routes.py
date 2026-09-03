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


def test_startup_closes_the_connection_it_opens(monkeypatch) -> None:
    # Regression test: on_startup() previously opened a connection via
    # db.get_connection() and passed it to db.init_schema() but never
    # closed it, leaking a connection/file-descriptor handle for the
    # app's lifetime. Wrap the real connection in a spy that records
    # whether .close() was called, and assert it was -- by the time
    # TestClient(app)'s __enter__ (which drives the lifespan startup
    # event synchronously, per starlette.testclient.TestClient.__enter__
    # calling portal.call(self.wait_startup)) returns.
    real_get_connection = db.get_connection
    opened_connections: list = []

    class ConnectionSpy:
        def __init__(self, real_conn) -> None:
            self._real_conn = real_conn
            self.closed = False

        def close(self) -> None:
            self.closed = True
            self._real_conn.close()

        def __getattr__(self, name: str):
            return getattr(self._real_conn, name)

    def spy_get_connection():
        spy = ConnectionSpy(real_get_connection())
        opened_connections.append(spy)
        return spy

    monkeypatch.setattr(db, "get_connection", spy_get_connection)

    with TestClient(app):
        pass

    assert len(opened_connections) == 1
    assert opened_connections[0].closed is True
