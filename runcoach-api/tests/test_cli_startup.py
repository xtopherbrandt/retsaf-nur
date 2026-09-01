"""Tests for runcoach_api.cli: serve() startup behavior.

Covers T004 (happy-path server start) and T014 (config-error
handling for the three typed failure modes raised by
``load_config``: missing file, corrupt TOML, and invalid field
value). Port-conflict handling (T005) is out of scope here.
"""

from __future__ import annotations

import errno
import socket
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from runcoach_api import cli
from runcoach_api.config import AppConfig, ConfigCorruptError, ConfigNotFoundError


@dataclass
class _FakeConfig:
    host: str
    port: int


def _make_validation_error() -> ValidationError:
    """Build a real pydantic ValidationError with a 'port' field error."""
    try:
        AppConfig(host="localhost", port=99999, data_dir=Path("/tmp"))
    except ValidationError as exc:
        return exc
    raise AssertionError("expected AppConfig(...) to raise ValidationError")


def test_serve_starts_uvicorn_with_configured_host_port(monkeypatch, capsys):
    fake_config = _FakeConfig(host="127.0.0.1", port=8123)
    monkeypatch.setattr(cli, "load_config", lambda: fake_config)

    calls = []

    def fake_run(app, host, port):
        calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.serve()

    assert len(calls) == 1
    assert calls[0]["app"] is cli.app
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8123

    captured = capsys.readouterr()
    assert "127.0.0.1" in captured.out
    assert "8123" in captured.out


def test_serve_missing_config_exits_nonzero_without_traceback(monkeypatch, capsys):
    def fake_load_config():
        raise ConfigNotFoundError(
            "No config file found at /home/user/.runcoach/api.toml."
        )

    monkeypatch.setattr(cli, "load_config", fake_load_config)

    calls = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *a, **k: calls.append((a, k))
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.serve()

    assert exc_info.value.code == 1
    assert not calls

    captured = capsys.readouterr()
    assert "runcoach-api init" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_serve_invalid_config_exits_nonzero_names_port(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "load_config", lambda: (_ for _ in ()).throw(_make_validation_error())
    )

    calls = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *a, **k: calls.append((a, k))
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.serve()

    assert exc_info.value.code == 1
    assert not calls

    captured = capsys.readouterr()
    assert "port" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_serve_corrupt_config_exits_nonzero_names_file_path(monkeypatch, capsys):
    bad_path = "/home/user/.runcoach/api.toml"

    def fake_load_config():
        raise ConfigCorruptError(
            f"Config file at {bad_path} could not be parsed as TOML: bad syntax"
        )

    monkeypatch.setattr(cli, "load_config", fake_load_config)

    calls = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *a, **k: calls.append((a, k))
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.serve()

    assert exc_info.value.code == 1
    assert not calls

    captured = capsys.readouterr()
    assert bad_path in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_cli_port_in_use_exits_nonzero_without_traceback(monkeypatch, capsys):
    # Bind a real socket on a free port and keep it listening for the
    # duration of the test, so the port is *genuinely* in use. serve()'s
    # pre-flight probe (a plain socket bind attempted before uvicorn.run is
    # ever called) must detect this real conflict and exit cleanly --
    # uvicorn.run must never be reached.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        fake_config = _FakeConfig(host="127.0.0.1", port=port)
        monkeypatch.setattr(cli, "load_config", lambda: fake_config)

        calls = []
        monkeypatch.setattr(
            cli.uvicorn, "run", lambda *a, **k: calls.append((a, k))
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.serve()

        assert exc_info.value.code == 1
        assert not calls

        captured = capsys.readouterr()
        assert str(port) in captured.err
        assert "already in use" in captured.err.lower()
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out
    finally:
        sock.close()


def test_cli_other_oserror_from_uvicorn_run_propagates(monkeypatch, capsys):
    fake_config = _FakeConfig(host="127.0.0.1", port=8123)
    monkeypatch.setattr(cli, "load_config", lambda: fake_config)

    def fake_run(app, host, port):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    with pytest.raises(OSError) as exc_info:
        cli.serve()

    assert exc_info.value.errno == errno.EACCES
