"""CLI configuration read/write for the runcoach CLI.

Writes the athlete-supplied API URL to a local TOML config file
(``~/.runcoach/cli.toml`` by default), and reads it back via
``load_config()`` -- the guard every non-``init`` command calls first.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w
import typer

CONFIG_PATH = Path.home() / ".runcoach" / "cli.toml"


class ConfigAlreadyExistsError(Exception):
    """Raised when a config file already exists and ``force`` was not set."""


class ConfigCorruptError(Exception):
    """Raised when the config file exists but is not valid TOML.

    Distinct from a well-formed file missing a required field.
    """


def save_config(api_url: str, force: bool = False) -> None:
    """Write ``api_url`` to ``CONFIG_PATH`` as TOML.

    Args:
        api_url: The base URL of the runcoach API to persist.
        force: When ``False`` (default), refuses to overwrite an existing
            config file. When ``True``, overwrites it.

    Raises:
        ConfigAlreadyExistsError: ``CONFIG_PATH`` already exists and
            ``force`` is ``False``. Checked before any write -- no
            partial or silent overwrite ever happens.
    """
    if CONFIG_PATH.exists() and not force:
        raise ConfigAlreadyExistsError(CONFIG_PATH)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(tomli_w.dumps({"api_url": api_url}), encoding="utf-8")


@dataclass
class Config:
    """In-memory representation of the CLI's config file."""

    api_url: str


def load_config() -> Config:
    """Read ``CONFIG_PATH`` and return a populated :class:`Config`.

    This is the guard every non-``init`` command calls first. If no config
    file exists, it prints guidance and exits non-zero -- before any HTTP
    client is constructed (that coupling belongs to the calling command,
    not this guard). It never creates ``~/.runcoach/`` itself; only
    ``save_config`` does that.

    Raises:
        typer.Exit: ``CONFIG_PATH`` does not exist, is not valid TOML, or
            is missing the required ``api_url`` field (exit code 1 in
            every case).
    """
    if not CONFIG_PATH.exists():
        typer.echo(
            f"Error: no config found at {CONFIG_PATH}. Run `runcoach init` first.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        with CONFIG_PATH.open("rb") as f:
            try:
                data = tomllib.load(f)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigCorruptError(
                    f"Config file at {CONFIG_PATH} could not be parsed as TOML: {exc}"
                ) from exc
    except ConfigCorruptError as exc:
        typer.echo(
            f"Error: config file at {CONFIG_PATH} could not be parsed: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    try:
        return Config(api_url=data["api_url"])
    except KeyError:
        typer.echo(
            f"Error: config file at {CONFIG_PATH} is missing required field 'api_url'.",
            err=True,
        )
        raise typer.Exit(code=1)
