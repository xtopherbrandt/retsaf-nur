"""``runcoach init`` -- write the CLI's TOML config file.

Thin Typer wrapper around :func:`runcoach_cli.config.save_config`. All the
write/guard logic lives in ``config.py``; this module only handles CLI
option parsing and user-facing messages.
"""

import typer

from runcoach_cli import config


def init(
    api_url: str = typer.Option(..., "--api-url", help="Base URL of the runcoach API"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config"),
) -> None:
    """Write the runcoach API URL to the local config file."""
    try:
        config.save_config(api_url, force=force)
    except config.ConfigAlreadyExistsError:
        typer.echo(
            f"Error: config already exists at {config.CONFIG_PATH}. Use --force to overwrite.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Wrote config to {config.CONFIG_PATH}")
