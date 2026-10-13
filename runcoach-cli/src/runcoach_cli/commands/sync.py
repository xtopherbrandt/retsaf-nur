"""``runcoach sync``: fetch new runs and snapshots from Garmin Connect (F022).

This module holds the ``sync`` sub-app and its ``login`` and ``logout`` commands.

- ``sync login`` asks for the email, the password (not echoed) and, only when Garmin asks for one,
  the MFA code, through the module-level prompt seams ``ask_text`` and ``ask_secret`` (tests replace
  them). It calls the adapter's ``login`` (``runcoach_cli.garmin_connect.login``), which saves the
  token only after Garmin accepts the login, so a refused login keeps any old token. The email and
  password are never written anywhere.
- ``sync logout`` deletes the token file under ``garmin.token_dir()``. It never imports the Garmin
  library, so it works without the ``garmin`` extra. Deleting the file does not revoke the token
  at Garmin.
- ``sync`` and ``sync login`` need the unofficial ``garminconnect`` library, an optional extra
  (Python 3.12+). Without it they print the install command and exit 1. No other command
  imports the library, so every other command works without the extra.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from types import ModuleType

import typer

from runcoach_cli import garmin
from runcoach_cli.commands.profile import _fail
from runcoach_cli.commands.sessions import _write_utf8

# The file the library writes into the token directory (``Client.dump``).
TOKEN_FILE_NAME = "garmin_tokens.json"

INSTALL_COMMAND = "uv sync --all-packages --all-groups --extra garmin"
_LIBRARY = "garminconnect"
_MIN_PYTHON = (3, 12)

HELP = (
    "Fetch new runs and Health Snapshots from Garmin Connect and upload them to the API.\n\n"
    "This uses the unofficial garminconnect library, not an API Garmin offers: it may break when "
    "Garmin changes its login, and its use is at the account holder's own risk under Garmin's "
    "terms.\n\n"
    "`runcoach sync login` keeps a token on disk under the runcoach config folder (garmin/). On "
    "Windows that file is not protected by file permissions, only by your user profile; anyone who "
    "can read it can use the Garmin account until the token expires. `runcoach sync logout` "
    "deletes it (it does not revoke it at Garmin)."
)

sync_app = typer.Typer(help=HELP, invoke_without_command=True)


def ask_text(prompt: str) -> str:
    """Prompt for a visible line of text (the email, the MFA code)."""
    return typer.prompt(prompt)


def ask_secret(prompt: str) -> str:
    """Prompt for a secret without echoing it (the password)."""
    return typer.prompt(prompt, hide_input=True)


def _python_version() -> tuple[int, int]:
    return sys.version_info[:2]


def _library_missing() -> bool:
    """True when ``garminconnect`` cannot be imported. Looks it up without importing it."""
    try:
        return importlib.util.find_spec(_LIBRARY) is None
    except (ImportError, ValueError):
        return True


def _require_library() -> None:
    """Exit 1 with the install command when the ``garmin`` extra is not installed."""
    if not _library_missing():
        return
    message = f"the Garmin library is not installed. Install it with `{INSTALL_COMMAND}`."
    if _python_version() < _MIN_PYTHON:
        major, minor = _MIN_PYTHON
        message += (
            f" It needs Python {major}.{minor} or later; this is Python "
            f"{'.'.join(str(part) for part in _python_version())}."
        )
    raise _fail(message)


def _adapter() -> ModuleType:
    """The real Garmin adapter, imported only when a command needs Garmin."""
    from runcoach_cli import garmin_connect

    return garmin_connect


@sync_app.callback()
def sync(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _require_library()
    _write_utf8(ctx.get_help())


@sync_app.command(name="login")
def login() -> None:
    """Log in to Garmin Connect and save the token (replacing any saved one)."""
    _require_library()
    email = ask_text("Garmin email")
    password = ask_secret("Garmin password")
    prompt_mfa: Callable[[], str] = lambda: ask_text("Garmin MFA code")
    try:
        _adapter().login(email, password, prompt_mfa)
    except garmin.GarminError as exc:
        raise _fail(f"Garmin refused the login: {exc}. Any saved token is unchanged.") from None
    _write_utf8(f"Logged in. Token saved in {garmin.token_dir()}")


@sync_app.command(name="logout")
def logout() -> None:
    """Delete the saved Garmin token. This does not revoke it at Garmin."""
    token_file = garmin.token_dir() / TOKEN_FILE_NAME
    if not token_file.exists():
        _write_utf8(f"No Garmin token at {token_file}: nothing to delete.")
        return
    try:
        token_file.unlink()
    except OSError as exc:
        raise _fail(f"could not delete the Garmin token at {token_file}: {exc}") from None
    _write_utf8(f"Deleted the Garmin token at {token_file}. This does not revoke it at Garmin.")
