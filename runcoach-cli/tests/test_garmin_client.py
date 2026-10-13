"""Tests for the CLI's Garmin port (``runcoach_cli.garmin``) and the bytes upload it feeds.

The port holds no library code: the token directory, the CLI's own exceptions and the client
Protocol. ``upload_fit_bytes`` is the ``api_client`` door every downloaded zip entry goes through.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import httpx

from runcoach_cli import api_client, config, garmin


def test_token_dir_follows_the_config_home(isolated_config_path: Path, monkeypatch) -> None:
    # The autouse fixture has pointed CONFIG_PATH at a tmp home; the token dir sits beside it.
    assert garmin.token_dir() == isolated_config_path.parent / "garmin"

    # Evaluated at call time, not import time: a later repoint is followed.
    moved = isolated_config_path.parent.parent / "elsewhere" / "cli.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", moved)
    assert garmin.token_dir() == moved.parent / "garmin"

    # Resolving the path creates nothing.
    assert not (isolated_config_path.parent / "garmin").exists()
    assert not moved.parent.exists()


def test_exceptions_share_one_base() -> None:
    for exc in (garmin.LoginNeeded, garmin.RateLimited, garmin.GarminUnreachable, garmin.NotFound):
        assert issubclass(exc, garmin.GarminError)
    assert len({garmin.LoginNeeded, garmin.RateLimited, garmin.GarminUnreachable, garmin.NotFound}) == 4


def test_a_fake_satisfies_the_client_protocol() -> None:
    class Fake:
        def list_activities(self, start: date, end: date) -> list[dict]:
            return []

        def download_original(self, activity_id: int) -> bytes:
            return b""

        def download_snapshot(self, day: date) -> bytes:
            return b""

    assert isinstance(Fake(), garmin.GarminClient)
    assert not isinstance(object(), garmin.GarminClient)


def _parse_upload(request: httpx.Request) -> tuple[dict[str, str], str, bytes]:
    """Split a multipart POST into (form fields, file part filename, file part bytes)."""
    content_type = request.headers["content-type"]
    boundary = content_type.split("boundary=", 1)[1].encode()
    fields: dict[str, str] = {}
    filename = ""
    payload = b""
    for part in request.content.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        body = body[: -len(b"\r\n")] if body.endswith(b"\r\n") else body
        name = re.search(rb'name="([^"]*)"', head).group(1).decode()
        file_match = re.search(rb'filename="([^"]*)"', head)
        if file_match:
            filename = file_match.group(1).decode()
            payload = body
        else:
            fields[name] = body.decode()
    return fields, filename, payload


def test_upload_fit_bytes_sends_the_switch_and_a_generated_name(install_mock_client) -> None:
    seen: list[tuple[str, dict[str, str], str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fields, filename, payload = _parse_upload(request)
        seen.append((request.url.path, fields, filename, payload))
        return httpx.Response(201, json={"id": "x"})

    install_mock_client(handler)
    data = b"\x0e\x10entry-bytes named 12345_ACTIVITY.fit"
    response = api_client.upload_fit_bytes("http://example.test", data)
    assert response.status_code == 201
    response = api_client.upload_fit_bytes("http://example.test", data, runs_and_captures_only=False)
    assert response.status_code == 201

    (path1, fields1, name1, body1), (path2, fields2, name2, body2) = seen
    assert path1 == path2 == "/sessions"
    assert fields1 == {"runs_and_captures_only": "true"}
    assert fields2 == {"runs_and_captures_only": "false"}
    assert body1 == body2 == data
    # The name is generated per call, never taken from the bytes or an entry name.
    for name in (name1, name2):
        assert re.fullmatch(r"garmin-[0-9a-f]{32}\.fit", name), name
        assert "ACTIVITY" not in name and "12345" not in name
    assert name1 != name2


def test_upload_fit_bytes_maps_a_transport_failure(install_mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    install_mock_client(handler)
    try:
        api_client.upload_fit_bytes("http://example.test", b"x")
    except api_client.ApiUnreachableError as exc:
        assert exc.base_url == "http://example.test"
    else:
        raise AssertionError("expected ApiUnreachableError")
