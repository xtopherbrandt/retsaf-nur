import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from runcoach_cli.main import app

runner = CliRunner()


def test_help_lists_init_and_status():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.stdout
    assert "status" in result.stdout


# A fresh interpreter that imports only the entry point's module: no test module has imported a
# command module first, so a command that registers itself only on import is seen as a user sees it.
_ENTRY_POINT_ONLY = """
import socket
import sys

def _refuse(*args, **kwargs):
    raise OSError("network blocked in this test")

socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse

from typer.testing import CliRunner
from runcoach_cli.main import app

result = CliRunner().invoke(app, ["sync", "laps", "--help"])
sys.stdout.write(result.output)
sys.stdout.write("EXIT-CODE " + str(result.exit_code) + "\\n")
"""


def test_sync_laps_is_reachable_from_the_entry_point_alone(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    child_env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), COLUMNS="200", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", _ENTRY_POINT_ONLY],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=child_env,
        cwd=tmp_path,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EXIT-CODE 0\n" in proc.stdout, proc.stdout + proc.stderr
    assert "--since" in proc.stdout, proc.stdout
