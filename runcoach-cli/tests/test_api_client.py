"""Tests for the httpx GET /health wrapper.

Network failures (connect error / timeout) are simulated via httpx.MockTransport
handlers that raise directly - never by pointing at a genuinely slow or
unreachable endpoint. This keeps the suite fast and deterministic while still
proving get_health() converts the underlying httpx exceptions correctly.
"""

import time

import httpx
import pytest

from runcoach_cli import api_client


def _install_mock_client(handler, monkeypatch) -> None:
    """Swap the module-level client for one backed by a MockTransport."""
    mock_client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    monkeypatch.setattr(api_client, "client", mock_client)


def test_get_health_returns_response_and_raises_on_unreachable(monkeypatch) -> None:
    # 1. Server responds 200 with a well-formed body -> returned unchanged.
    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0"})

    _install_mock_client(ok_handler, monkeypatch)
    response = api_client.get_health("http://example.test")
    assert isinstance(response, httpx.Response)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}

    # 2. Connection refused / no listener -> ApiUnreachableError naming base_url.
    def connect_error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_mock_client(connect_error_handler, monkeypatch)
    with pytest.raises(api_client.ApiUnreachableError) as exc_info:
        api_client.get_health("http://example.test")
    assert exc_info.value.base_url == "http://example.test"
    assert "http://example.test" in str(exc_info.value)

    # 3. Simulated timeout -> ApiUnreachableError, raised immediately (bounded,
    #    not an indefinite hang) - the mock handler raises directly so no real
    #    5s wait ever elapses.
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    _install_mock_client(timeout_handler, monkeypatch)
    started = time.monotonic()
    with pytest.raises(api_client.ApiUnreachableError) as exc_info:
        api_client.get_health("http://example.test")
    elapsed = time.monotonic() - started
    assert exc_info.value.base_url == "http://example.test"
    assert elapsed < 1.0  # must not actually block near the 5s timeout


def test_get_health_uses_bounded_five_second_timeout() -> None:
    # The module-level client applies a single bare-float timeout uniformly
    # to connect/read/write/pool, per the "bounded 5s total" requirement -
    # not a per-phase httpx.Timeout(...) object that could total up to 20s.
    timeout = api_client.client.timeout
    assert timeout.connect == 5.0
    assert timeout.read == 5.0
    assert timeout.write == 5.0
    assert timeout.pool == 5.0


def test_get_health_translates_other_transport_errors(monkeypatch) -> None:
    # A transport-layer failure other than connect/timeout (e.g. the API
    # process crashes mid-response) must also become ApiUnreachableError,
    # not propagate as a raw httpx.TransportError subclass.
    def read_error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("boom", request=request)

    _install_mock_client(read_error_handler, monkeypatch)
    with pytest.raises(api_client.ApiUnreachableError) as exc_info:
        api_client.get_health("http://example.test")
    assert exc_info.value.base_url == "http://example.test"


def test_get_health_does_not_swallow_non_2xx_response(monkeypatch) -> None:
    # Non-2xx responses are returned unchanged, not translated into
    # ApiUnreachableError - status-code handling is the caller's job (T010).
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    _install_mock_client(error_handler, monkeypatch)
    response = api_client.get_health("http://example.test")
    assert response.status_code == 500
    assert response.json() == {"detail": "boom"}


def test_upload_fit_posts_multipart_to_sessions_and_returns_response(tmp_path, monkeypatch) -> None:
    # A well-formed 201 response is returned unchanged; the file is sent as
    # multipart form data under the "file" field, named after the path.
    fit_path = tmp_path / "activity.fit"
    fit_path.write_bytes(b"binary-fit-content")

    def created_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions"
        assert request.method == "POST"
        assert b'name="file"' in request.content
        assert b"activity.fit" in request.content
        assert b"binary-fit-content" in request.content
        return httpx.Response(201, json={"session_id": "abc-123", "quality_flags": []})

    _install_mock_client(created_handler, monkeypatch)
    response = api_client.upload_fit("http://example.test", fit_path)
    assert isinstance(response, httpx.Response)
    assert response.status_code == 201
    assert response.json() == {"session_id": "abc-123", "quality_flags": []}


def test_upload_fit_raises_api_unreachable_on_connect_error(tmp_path, monkeypatch) -> None:
    fit_path = tmp_path / "activity.fit"
    fit_path.write_bytes(b"binary-fit-content")

    def connect_error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_mock_client(connect_error_handler, monkeypatch)
    with pytest.raises(api_client.ApiUnreachableError) as exc_info:
        api_client.upload_fit("http://example.test", fit_path)
    assert exc_info.value.base_url == "http://example.test"


def test_upload_fit_uses_longer_timeout_than_health_check(tmp_path, monkeypatch) -> None:
    # FIT uploads can be up to 50MB (the API's own cap); the 5s timeout tuned
    # for the tiny /health payload is nowhere near enough for a large
    # multipart upload on a slow connection. upload_fit must use a longer,
    # upload-appropriate timeout on its POST call - not silently inherit the
    # health-check client's 5.0s default. We capture the timeout actually
    # threaded through to the transport (httpx resolves it into
    # request.extensions["timeout"] before the transport ever sees the
    # request) rather than just asserting "no exception raised", since a 5s
    # timeout never actually elapses against a fast local MockTransport.
    fit_path = tmp_path / "activity.fit"
    fit_path.write_bytes(b"binary-fit-content")

    captured_timeouts: list[dict] = []

    def created_handler(request: httpx.Request) -> httpx.Response:
        captured_timeouts.append(request.extensions["timeout"])
        return httpx.Response(201, json={"session_id": "abc-123", "quality_flags": []})

    _install_mock_client(created_handler, monkeypatch)
    api_client.upload_fit("http://example.test", fit_path)

    assert len(captured_timeouts) == 1
    upload_timeout = captured_timeouts[0]
    assert upload_timeout["read"] > 5.0
    assert upload_timeout["write"] > 5.0


def test_upload_fit_does_not_swallow_non_2xx_response(tmp_path, monkeypatch) -> None:
    fit_path = tmp_path / "activity.fit"
    fit_path.write_bytes(b"binary-fit-content")

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid FIT file")

    _install_mock_client(error_handler, monkeypatch)
    response = api_client.upload_fit("http://example.test", fit_path)
    assert response.status_code == 400
    assert response.text == "invalid FIT file"
