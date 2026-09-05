"""Tests for the ``runcoach status`` command's happy path.

Only the well-formed-response path is covered here: valid config plus a
reachable API returning a 200 with a well-formed body. Request-failure
handling (unreachable/non-2xx) and malformed-body handling are separate
tasks (T016, T011) layered onto the same call site afterward.
"""

import httpx
import pytest
from typer.testing import CliRunner

from runcoach_cli.main import app

runner = CliRunner()

# Every command exercised here calls ``load_config()`` first, so it needs real
# config content on disk -- the bare autouse ``isolated_config_path`` only
# redirects the path. ``prewritten_config`` (see conftest.py) layers the
# content on; applied module-wide because every test below needs it.
pytestmark = pytest.mark.usefixtures("prewritten_config")


def test_status_happy_path_renders_ok_and_version(install_mock_client) -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "version": "1.0.0"})

    install_mock_client(ok_handler)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "ok" in result.output
    assert "1.0.0" in result.output


def test_status_unreachable_and_non2xx_exit_cleanly(install_mock_client) -> None:
    def refusing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    install_mock_client(refusing_handler)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "http://example.test" in result.output
    assert "runcoach-api" in result.output


def test_status_non2xx_response_exits_cleanly(install_mock_client) -> None:
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    install_mock_client(error_handler)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "500" in result.output
    assert "internal server error" in result.output


def test_status_malformed_response_fails_cleanly(install_mock_client) -> None:
    def invalid_json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all", headers={"content-type": "application/json"})

    install_mock_client(invalid_json_handler)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "could not be understood" in result.output


def test_status_missing_version_field_fails_cleanly(install_mock_client) -> None:
    def missing_version_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    install_mock_client(missing_version_handler)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "could not be understood" in result.output


def test_status_non_string_version_field_fails_cleanly(install_mock_client) -> None:
    def non_string_version_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "version": 123})

    install_mock_client(non_string_version_handler)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "could not be understood" in result.output
