"""Tests for T020: an oversized FIT upload is rejected with a clean 413,
and no FIT parsing is attempted on the oversized path.

Synthetic byte strings (not real FIT content) are used deliberately --
this exercises the byte-count boundary guard in ``main.py``'s
``create_session`` route, not FIT-parsing correctness.
"""

from __future__ import annotations

import io
from unittest.mock import Mock

from fastapi.testclient import TestClient

from runcoach_api.main import MAX_UPLOAD_BYTES, app


def _post_bytes(client: TestClient, size: int):
    payload = b"0" * size
    return client.post(
        "/sessions",
        files={"file": ("big.fit", io.BytesIO(payload), "application/octet-stream")},
    )


def test_oversized_upload_returns_413_with_size_limit_message() -> None:
    with TestClient(app) as client:
        response = _post_bytes(client, MAX_UPLOAD_BYTES + 1)

    assert response.status_code == 413
    assert str(MAX_UPLOAD_BYTES) in response.text


def test_just_under_limit_upload_is_not_rejected_by_size_guard() -> None:
    # It may still fail downstream (e.g. as an invalid FIT file, or -- at
    # this stage of the codebase -- fit_parser.decode's NotImplementedError
    # stub) -- that's expected and out of scope here. raise_server_exceptions
    # is disabled so a downstream failure surfaces as a response rather than
    # propagating out of the test. Only the 413 size guard is under test.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = _post_bytes(client, MAX_UPLOAD_BYTES - 1)

    assert response.status_code != 413


def test_oversized_upload_never_reaches_fit_parsing(monkeypatch) -> None:
    spy = Mock(
        side_effect=AssertionError(
            "ingest_fit_bytes should never be called for an oversized upload"
        )
    )
    monkeypatch.setattr("runcoach_api.main.ingest_fit_bytes", spy)

    with TestClient(app) as client:
        response = _post_bytes(client, MAX_UPLOAD_BYTES + 1)

    assert response.status_code == 413
    spy.assert_not_called()
