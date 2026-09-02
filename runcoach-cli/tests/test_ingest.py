"""Tests for the ``runcoach ingest`` command.

Only the CLI's own responsibilities are covered here: uploading whatever
path it is given via api_client.upload_fit and rendering the API's
response. No FIT-parsing logic exists on the CLI side -- the API owns
that contract (POST /sessions multipart -> 201 {session_id, quality_flags}).
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


@pytest.fixture
def fit_file(tmp_path):
    """A dummy file on disk to pass as the ingest path -- contents are never inspected."""
    path = tmp_path / "activity.fit"
    path.write_bytes(b"not-a-real-fit-file")
    return path


def _install_mock_client(handler, monkeypatch) -> None:
    """Swap the module-level api_client for one backed by a MockTransport."""
    mock_client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    monkeypatch.setattr(api_client, "client", mock_client)


def test_ingest_happy_path_renders_session_id_and_quality_flags(monkeypatch, fit_file) -> None:
    def created_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions"
        assert request.method == "POST"
        return httpx.Response(
            201,
            json={"session_id": "abc-123", "quality_flags": ["low_gps_accuracy"]},
        )

    _install_mock_client(created_handler, monkeypatch)

    result = runner.invoke(app, ["ingest", str(fit_file)])

    assert result.exit_code == 0
    assert "abc-123" in result.output
    assert "low_gps_accuracy" in result.output


def test_ingest_happy_path_with_no_quality_flags(monkeypatch, fit_file) -> None:
    def created_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"session_id": "xyz-789", "quality_flags": []})

    _install_mock_client(created_handler, monkeypatch)

    result = runner.invoke(app, ["ingest", str(fit_file)])

    assert result.exit_code == 0
    assert "xyz-789" in result.output
    assert "none" in result.output


def test_ingest_unreachable_api_exits_cleanly(monkeypatch, fit_file) -> None:
    def refusing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_mock_client(refusing_handler, monkeypatch)

    result = runner.invoke(app, ["ingest", str(fit_file)])

    assert result.exit_code != 0
    assert "could not reach API" in result.output
    assert "http://example.test" in result.output


def test_ingest_non2xx_response_exits_cleanly(monkeypatch, fit_file) -> None:
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid FIT file")

    _install_mock_client(error_handler, monkeypatch)

    result = runner.invoke(app, ["ingest", str(fit_file)])

    assert result.exit_code != 0
    assert "400" in result.output
    assert "invalid FIT file" in result.output


def test_ingest_missing_path_exits_nonzero(monkeypatch, tmp_path) -> None:
    missing_path = tmp_path / "does-not-exist.fit"

    result = runner.invoke(app, ["ingest", str(missing_path)])

    assert result.exit_code != 0
