"""``tests/support/check_real_config.py`` loads a config pair through the release's models.

The script is the read-only check run against the athlete's real
``~/.runcoach/api.toml`` and ``~/.runcoach/cli.toml`` before a release: it
loads each through the loader the server and the CLI use at startup and prints
``api.toml: OK`` / ``api.toml: ERROR`` (and the same for ``cli.toml``) with the
text the athlete would see. Every config here lives in ``tmp_path`` and is
handed to the script by ``--api`` / ``--cli``; nothing here reads the real files.

The script module is loaded at collection, before the autouse
``isolated_data_dir`` fixture replaces ``runcoach_api.config.load_config`` with
a fake, so the script holds the real loader; the remediation test below would
fail against the fake, which returns a valid config for any path.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from runcoach_api import db as db_module

_SPEC = importlib.util.spec_from_file_location(
    "check_real_config", Path(__file__).parent / "support" / "check_real_config.py"
)
assert _SPEC is not None and _SPEC.loader is not None
check_real_config = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_real_config)

_VALID_API = """\
host = "127.0.0.1"
port = 8000
data_dir = "{data_dir}"
resting_hrv_profile_names = ["HRV Snapshot"]
threshold_workout_names = ["Threshold"]
athlete_timezone = "Europe/Berlin"
"""

_VALID_CLI = 'api_url = "http://127.0.0.1:8000"\n'


@pytest.fixture(autouse=True)
def _no_runcoach_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear any ``RUNCOACH_*`` variable the developer's shell carries."""
    import os

    for name in list(os.environ):
        if name.upper().startswith("RUNCOACH_"):
            monkeypatch.delenv(name)


def _write_pair(tmp_path: Path, api_text: str | None = None, cli_text: str | None = None) -> tuple[Path, Path]:
    data_dir = (tmp_path / "store").as_posix()
    api = tmp_path / "api.toml"
    cli = tmp_path / "cli.toml"
    api.write_text(api_text if api_text is not None else _VALID_API.format(data_dir=data_dir), encoding="utf-8")
    cli.write_text(cli_text if cli_text is not None else _VALID_CLI, encoding="utf-8")
    return api, cli


def _run(api: Path, cli: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = check_real_config.main(["--api", str(api), "--cli", str(cli)])
    out = capsys.readouterr().out
    return code, out


def test_a_valid_pair_prints_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api, cli = _write_pair(tmp_path)
    code, out = _run(api, cli, capsys)
    lines = out.splitlines()
    assert any(line.startswith("api.toml: OK") for line in lines), out
    assert any(line.startswith("cli.toml: OK") for line in lines), out
    assert "ERROR" not in out
    assert code == 0


def test_a_missing_threshold_workout_names_prints_the_remediation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = (tmp_path / "store").as_posix()
    without = "\n".join(
        line
        for line in _VALID_API.format(data_dir=data_dir).splitlines()
        if not line.startswith("threshold_workout_names")
    )
    api, cli = _write_pair(tmp_path, api_text=without + "\n")
    code, out = _run(api, cli, capsys)
    assert any(line.startswith("api.toml: ERROR") for line in out.splitlines()), out
    # The remediation block `serve` prints, not pydantic's bare "Field required".
    assert "Error: config field 'threshold_workout_names' is missing, and it has no default." in out
    assert "threshold_workout_names = []" in out
    assert any(line.startswith("cli.toml: OK") for line in out.splitlines()), out
    assert code == 1


def test_a_missing_api_url_prints_the_cli_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api, cli = _write_pair(tmp_path, cli_text='other = "x"\n')
    code, out = _run(api, cli, capsys)
    assert any(line.startswith("api.toml: OK") for line in out.splitlines()), out
    assert any(line.startswith("cli.toml: ERROR") for line in out.splitlines()), out
    assert "missing required field 'api_url'" in out
    assert str(cli) in out
    assert code == 1


def test_a_set_runcoach_env_var_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNCOACH_THRESHOLD_WORKOUT_NAMES", '["Secret Name"]')
    api, cli = _write_pair(tmp_path)
    code, out = _run(api, cli, capsys)
    assert "RUNCOACH_THRESHOLD_WORKOUT_NAMES" in out
    assert "overrides" in out
    # The variable's name is reported, never its value.
    assert "Secret Name" not in out
    assert code == 0


def test_no_runcoach_env_var_reports_none(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api, cli = _write_pair(tmp_path)
    _, out = _run(api, cli, capsys)
    assert "RUNCOACH_* environment variables: none set" in out


@pytest.mark.parametrize("cli_text", [_VALID_CLI, 'other = "x"\n'], ids=["ok", "error"])
def test_check_cli_restores_the_cli_config_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cli_text: str
) -> None:
    original = check_real_config.cli_config.CONFIG_PATH
    _, cli = _write_pair(tmp_path, cli_text=cli_text)
    assert cli != original
    ok = check_real_config.check_cli(cli)
    capsys.readouterr()
    assert ok is (cli_text == _VALID_CLI)
    assert check_real_config.cli_config.CONFIG_PATH is original


def test_no_store_is_opened(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the config check opened a store")

    monkeypatch.setattr(db_module, "get_connection", _refuse)
    monkeypatch.setattr(sqlite3, "connect", _refuse)
    api, cli = _write_pair(tmp_path)
    code, out = _run(api, cli, capsys)
    assert code == 0, out
    assert not (tmp_path / "store").exists()
