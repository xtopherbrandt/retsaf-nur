"""Tests for the CLI config read path (``load_config``).

``load_config`` is the guard every non-``init`` command calls first: if no
config file exists it must print guidance and exit non-zero *before*
constructing any HTTP client. We assert that invariant by patching
``httpx.Client.__init__`` to fail the test if it's ever called, then
confirming the missing-config path still raises ``typer.Exit`` cleanly
(rather than blowing up from the spy).
"""

import httpx
import pytest
import tomli_w
import typer

from runcoach_cli import config


def test_load_config_missing_exits_before_network_call(monkeypatch, capsys) -> None:
    # Spy on httpx.Client construction: if load_config's failure path ever
    # constructs an HTTP client, this fails the test immediately instead of
    # letting typer.Exit mask the ordering bug.
    def _fail_if_constructed(*args, **kwargs):
        raise AssertionError("httpx.Client was constructed before the config guard exited")

    monkeypatch.setattr(httpx.Client, "__init__", _fail_if_constructed)

    with pytest.raises(typer.Exit) as exc_info:
        config.load_config()

    assert exc_info.value.exit_code == 1

    captured = capsys.readouterr()
    assert "runcoach init" in captured.err


def test_load_config_present_and_well_formed_returns_config(isolated_config_path) -> None:
    config_path = isolated_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(tomli_w.dumps({"api_url": "http://example.test"}).encode())

    result = config.load_config()

    assert isinstance(result, config.Config)
    assert result.api_url == "http://example.test"


def test_load_config_malformed_toml_exits_cleanly(isolated_config_path, capsys) -> None:
    config_path = isolated_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("this is not [ valid toml", encoding="utf-8")

    with pytest.raises(typer.Exit) as exc_info:
        config.load_config()

    assert exc_info.value.exit_code == 1

    captured = capsys.readouterr()
    assert str(config_path) in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_load_config_missing_api_url_field_exits_cleanly(isolated_config_path, capsys) -> None:
    config_path = isolated_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(tomli_w.dumps({"other_field": "value"}).encode())

    with pytest.raises(typer.Exit) as exc_info:
        config.load_config()

    assert exc_info.value.exit_code == 1

    captured = capsys.readouterr()
    assert "api_url" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
