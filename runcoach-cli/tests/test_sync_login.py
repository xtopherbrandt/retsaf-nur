"""``runcoach sync login`` / ``sync logout``, the not-installed message and the ``--help`` disclosure.

No test reaches Garmin: ``sync._adapter`` is replaced by a fake whose ``login`` writes a token the
way the real adapter does, and the token directory sits under the autouse ``isolated_config_path``
(a per-test ``tmp_path``). The prompts are the module-level ``sync.ask_text`` / ``sync.ask_secret``
seams, so no test reads stdin.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from runcoach_cli import garmin
from runcoach_cli.commands import sync
from runcoach_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

SENTINEL_PASSWORD = "s3ntinel-PASSWORD-7f2c"
EMAIL = "athlete@example.com"
TOKEN_BYTES = b'{"di_token": "fake-token", "di_refresh_token": "fake-refresh"}'
INSTALL_COMMAND = "uv sync --all-packages --all-groups --extra garmin"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _token_file() -> Path:
    return garmin.token_dir() / sync.TOKEN_FILE_NAME


class FakeAdapter:
    """Stands in for ``runcoach_cli.garmin_connect``: ``login`` asks for the MFA code and writes a token."""

    def __init__(self, refuse: garmin.GarminError | None = None) -> None:
        self.refuse = refuse
        self.calls: list[tuple[str, str, str]] = []

    def login(self, email: str, password: str, prompt_mfa: Callable[[], str]) -> None:
        code = prompt_mfa()
        self.calls.append((email, password, code))
        if self.refuse is not None:
            raise self.refuse
        token_dir = garmin.token_dir()
        token_dir.mkdir(parents=True, exist_ok=True)
        (token_dir / sync.TOKEN_FILE_NAME).write_bytes(TOKEN_BYTES)


@pytest.fixture
def prompts(monkeypatch) -> list[str]:
    asked: list[str] = []

    def ask_text(prompt: str) -> str:
        asked.append(prompt)
        return "123456" if "MFA" in prompt else EMAIL

    def ask_secret(prompt: str) -> str:
        asked.append(prompt)
        return SENTINEL_PASSWORD

    monkeypatch.setattr(sync, "ask_text", ask_text)
    monkeypatch.setattr(sync, "ask_secret", ask_secret)
    return asked


def _install(monkeypatch, adapter: FakeAdapter) -> FakeAdapter:
    monkeypatch.setattr(sync, "_adapter", lambda: adapter)
    return adapter


def test_login_saves_token_and_no_file_holds_the_sentinel_password(monkeypatch, tmp_path, prompts) -> None:
    adapter = _install(monkeypatch, FakeAdapter())

    result = runner.invoke(app, ["sync", "login"])

    assert result.exit_code == 0, result.output
    assert adapter.calls == [(EMAIL, SENTINEL_PASSWORD, "123456")]
    assert len(prompts) == 3
    assert str(garmin.token_dir()) in _flat(result.output)
    assert SENTINEL_PASSWORD not in result.output

    files = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert files == [_token_file()]
    assert _token_file().read_bytes() == TOKEN_BYTES
    for path in files:
        assert SENTINEL_PASSWORD.encode("utf-8") not in path.read_bytes()


def test_login_password_prompt_hides_input(monkeypatch) -> None:
    seen: list[dict] = []

    def fake_prompt(text, **kwargs):
        seen.append({"text": text, **kwargs})
        return "x"

    monkeypatch.setattr(sync.typer, "prompt", fake_prompt)
    sync.ask_secret("Garmin password")
    sync.ask_text("Garmin email")
    assert seen[0]["hide_input"] is True
    assert not seen[1].get("hide_input", False)


def test_a_refused_login_keeps_the_old_token_and_exits_1(monkeypatch, prompts) -> None:
    old = b'{"di_token": "old"}'
    _token_file().parent.mkdir(parents=True)
    _token_file().write_bytes(old)
    _install(monkeypatch, FakeAdapter(refuse=garmin.LoginNeeded("Invalid username or password")))

    result = runner.invoke(app, ["sync", "login"])

    assert result.exit_code == 1
    assert "Invalid username or password" in result.output
    assert _token_file().read_bytes() == old
    assert [p.name for p in _token_file().parent.iterdir()] == [sync.TOKEN_FILE_NAME]


def test_logout_deletes_the_token_and_says_garmin_keeps_it(monkeypatch) -> None:
    _token_file().parent.mkdir(parents=True)
    _token_file().write_bytes(TOKEN_BYTES)
    # Logout must not need the library at all.
    monkeypatch.setitem(sys.modules, "garminconnect", None)

    result = runner.invoke(app, ["sync", "logout"])

    assert result.exit_code == 0, result.output
    assert not _token_file().exists()
    flat = _flat(result.output)
    assert "does not revoke" in flat
    assert "Garmin" in flat


def test_logout_with_no_token_exits_0(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "garminconnect", None)

    result = runner.invoke(app, ["sync", "logout"])

    assert result.exit_code == 0, result.output
    assert "No Garmin token" in _flat(result.output)
    assert not garmin.token_dir().exists()


def test_sync_without_the_extra_prints_the_install_command(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "garminconnect", None)

    def no_prompt(prompt: str) -> str:
        raise AssertionError(f"prompted: {prompt}")

    monkeypatch.setattr(sync, "ask_text", no_prompt)
    monkeypatch.setattr(sync, "ask_secret", no_prompt)
    monkeypatch.setattr(sync, "_adapter", lambda: pytest.fail("adapter loaded"))

    for args in (["sync"], ["sync", "login"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1, (args, result.output)
        flat = _flat(result.output)
        assert INSTALL_COMMAND in flat, (args, flat)
        assert "3.12" not in flat or sys.version_info < (3, 12)

    monkeypatch.setattr(sync, "_python_version", lambda: (3, 11))
    for args in (["sync"], ["sync", "login"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1
        flat = _flat(result.output)
        assert INSTALL_COMMAND in flat
        assert "Python 3.12" in flat

    monkeypatch.setattr(sync, "_python_version", lambda: (3, 12))
    result = runner.invoke(app, ["sync", "login"])
    assert "Python 3.12" not in _flat(result.output)
    assert not garmin.token_dir().exists()


def test_sync_help_discloses_the_unofficial_route() -> None:
    result = runner.invoke(app, ["sync", "--help"])

    assert result.exit_code == 0
    flat = _flat(result.output).lower()
    assert "unofficial" in flat
    assert "may break when garmin changes its login" in flat
    assert "token on disk" in flat
    assert "sync logout" in flat
    assert "windows" in flat


_BLOCKER = """
import sys
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name == "garminconnect" or name.startswith("garminconnect."):
            sys.stderr.write("BLOCKED-IMPORT " + name + "\\n")
            raise ImportError("garminconnect is blocked")
        return None
sys.meta_path.insert(0, _Block())
sys.argv = ["runcoach"] + sys.argv[1:]
from runcoach_cli.main import app
app()
"""


@pytest.mark.parametrize("args", [["--help"], ["sessions", "--help"]])
def test_every_other_command_runs_without_the_extra(args: list[str]) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _BLOCKER, *args],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BLOCKED-IMPORT" not in proc.stderr
    assert "garminconnect" not in proc.stderr
    assert "Usage" in proc.stdout
