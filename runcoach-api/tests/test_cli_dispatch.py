"""Tests for runcoach_api.cli.main() top-level argparse dispatch (T051).

Before T051 ``main()`` hand-parsed ``sys.argv[1] == "init"`` and let every
other argument fall through to ``serve()`` -- so ``runcoach-api --help``
started a real uvicorn server and blocked forever instead of printing usage.

These tests pin the dispatch table ratified in the task spec::

    runcoach-api            -> serve()
    runcoach-api serve      -> serve()
    runcoach-api init ...   -> init_cmd.main(rest)
    runcoach-api --help     -> usage, exit 0
    runcoach-api foo        -> error, exit 2
"""

from __future__ import annotations

import pytest

from runcoach_api import cli


@pytest.fixture
def no_serve(monkeypatch):
    """Record serve() calls; fail loudly if a path reaches it when it should not."""
    calls: list[tuple] = []

    def _record() -> None:
        calls.append(())

    monkeypatch.setattr(cli, "serve", _record)
    return calls


@pytest.fixture
def spy_init(monkeypatch):
    """Capture the argv forwarded to init_cmd.main."""
    from runcoach_api import init_cmd

    forwarded: list[list[str]] = []
    monkeypatch.setattr(init_cmd, "main", lambda argv: forwarded.append(list(argv)))
    return forwarded


def test_help_prints_usage_and_exits_zero_without_serving(no_serve, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0

    out = capsys.readouterr().out
    assert out.startswith("usage: runcoach-api"), out
    assert "init" in out
    assert "serve" in out
    assert no_serve == [], "--help must not start the server"


def test_unknown_command_errors_without_serving(no_serve, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["foo"])

    # argparse exits 2 on an invalid choice, not 1.
    assert exc_info.value.code == 2
    assert no_serve == [], "an unrecognised argument must not start the server"

    err = capsys.readouterr().err
    assert "foo" in err


def test_bare_invocation_still_serves(no_serve):
    cli.main([])

    assert len(no_serve) == 1


def test_bare_invocation_reads_sys_argv(no_serve, monkeypatch):
    monkeypatch.setattr("sys.argv", ["runcoach-api"])

    cli.main()

    assert len(no_serve) == 1


def test_explicit_serve_subcommand_serves(no_serve):
    cli.main(["serve"])

    assert len(no_serve) == 1


def test_init_subcommand_forwards_remaining_argv(no_serve, spy_init):
    cli.main(
        [
            "init",
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--data-dir",
            "/tmp/x",
            "--force",
        ]
    )

    assert spy_init == [
        ["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x", "--force"]
    ]
    assert no_serve == [], "init must not fall through to serve()"


def test_unknown_flag_on_serve_errors_without_serving(no_serve):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["serve", "--bogus"])

    assert exc_info.value.code == 2
    assert no_serve == []
