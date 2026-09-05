"""Tests for the ``runcoach ingest`` command.

Only the CLI's own responsibilities are covered here: uploading whatever
path it is given via api_client.upload_fit and rendering the API's
response. No FIT-parsing logic exists on the CLI side -- the API owns
that contract (POST /sessions multipart -> 201 {session_id, quality_flags}).
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


@pytest.fixture
def fit_file(tmp_path):
    """A dummy file on disk to pass as the ingest path -- contents are never inspected."""
    path = tmp_path / "activity.fit"
    path.write_bytes(b"not-a-real-fit-file")
    return path


def test_ingest_happy_path_renders_session_id_and_quality_flags(install_mock_client, fit_file) -> None:
    def created_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions"
        assert request.method == "POST"
        return httpx.Response(
            201,
            json={"session_id": "abc-123", "quality_flags": ["low_gps_accuracy"]},
        )

    install_mock_client(created_handler)

    result = runner.invoke(app, ["ingest", str(fit_file)])

    assert result.exit_code == 0
    assert "abc-123" in result.output
    assert "low_gps_accuracy" in result.output


def test_ingest_happy_path_with_no_quality_flags(install_mock_client, fit_file) -> None:
    def created_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"session_id": "xyz-789", "quality_flags": []})

    install_mock_client(created_handler)

    result = runner.invoke(app, ["ingest", str(fit_file)])

    assert result.exit_code == 0
    assert "xyz-789" in result.output
    assert "none" in result.output


def test_ingest_unreachable_api_exits_cleanly(install_mock_client, fit_file) -> None:
    def refusing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    install_mock_client(refusing_handler)

    result = runner.invoke(app, ["ingest", str(fit_file)])

    assert result.exit_code != 0
    assert "could not reach API" in result.output
    assert "http://example.test" in result.output


def test_ingest_non2xx_response_exits_cleanly(install_mock_client, fit_file) -> None:
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid FIT file")

    install_mock_client(error_handler)

    result = runner.invoke(app, ["ingest", str(fit_file)])

    assert result.exit_code != 0
    assert "400" in result.output
    assert "invalid FIT file" in result.output


def test_ingest_missing_path_exits_nonzero(tmp_path) -> None:
    missing_path = tmp_path / "does-not-exist.fit"

    result = runner.invoke(app, ["ingest", str(missing_path)])

    assert result.exit_code != 0
