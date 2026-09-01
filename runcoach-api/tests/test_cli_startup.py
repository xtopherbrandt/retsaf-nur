"""Tests for runcoach_api.cli: serve() happy-path server start.

Covers the T004 scope only: given a valid config, serve() must call
uvicorn.run with the configured host/port and print a readiness line
to stdout. Config-load error handling (T014) and port-conflict
handling (T005) are out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass

from runcoach_api import cli


@dataclass
class _FakeConfig:
    host: str
    port: int


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
