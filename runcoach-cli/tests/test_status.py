"""Tests for the ``runcoach status`` command's happy path.

Only the well-formed-response path is covered here: valid config plus a
reachable API returning a 200 with a well-formed body. Request-failure
handling (unreachable/non-2xx) and malformed-body handling are separate
tasks (T016, T011) layered onto the same call site afterward.
"""

import httpx
import pytest
import tomli_w
from typer.testing import CliRunner

from runcoach_cli import api_client, config
from runcoach_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_config_path(tmp_path, monkeypatch):
    """Point CONFIG_PATH at a throwaway location with a pre-written config.

    Never touches the real ``~/.runcoach/`` directory.
    """
    config_path = tmp_path / ".runcoach" / "cli.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(tomli_w.dumps({"api_url": "http://example.test"}).encode())
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    return config_path


def _install_mock_client(handler, monkeypatch) -> None:
    """Swap the module-level api_client for one backed by a MockTransport."""
    mock_client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    monkeypatch.setattr(api_client, "client", mock_client)


def test_status_happy_path_renders_ok_and_version(monkeypatch) -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "version": "1.0.0"})

    _install_mock_client(ok_handler, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "ok" in result.output
    assert "1.0.0" in result.output


def test_status_unreachable_and_non2xx_exit_cleanly(monkeypatch) -> None:
    def refusing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_mock_client(refusing_handler, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "http://example.test" in result.output
    assert "runcoach-api" in result.output


def test_status_non2xx_response_exits_cleanly(monkeypatch) -> None:
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    _install_mock_client(error_handler, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "500" in result.output
    assert "internal server error" in result.output
