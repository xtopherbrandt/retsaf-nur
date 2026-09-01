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


def test_get_health_does_not_swallow_non_2xx_response(monkeypatch) -> None:
    # Non-2xx responses are returned unchanged, not translated into
    # ApiUnreachableError - status-code handling is the caller's job (T010).
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    _install_mock_client(error_handler, monkeypatch)
    response = api_client.get_health("http://example.test")
    assert response.status_code == 500
    assert response.json() == {"detail": "boom"}
