"""``runcoach ingest`` -- upload a FIT file to the API and render the result.

Loads config (T008's guard), calls the API client's upload_fit (T018), and
prints the returned session_id / quality_flags as human-readable text. This
command does no FIT-parsing or format validation itself -- that boundary
belongs to the API (POST /sessions).
"""

from pathlib import Path

import typer

from runcoach_cli import config
from runcoach_cli.api_client import ApiUnreachableError, upload_fit


def ingest(path: Path = typer.Argument(..., exists=True)) -> None:
    """Upload the FIT file at PATH to the configured API's /sessions endpoint."""
    cfg = config.load_config()  # T008's guard -- exits before this point if config is missing

    try:
        resp = upload_fit(cfg.api_url, path)
    except ApiUnreachableError:
        typer.echo(
            f"Error: could not reach API at {cfg.api_url}. "
            f"Is it running? Start it with `runcoach-api`.",
            err=True,
        )
        raise typer.Exit(code=1) from None

    if resp.status_code not in (200, 201):
        typer.echo(f"Error: API returned {resp.status_code}: {resp.text}", err=True)
        raise typer.Exit(code=1)

    body = resp.json()
    flags = ", ".join(body.get("quality_flags", [])) or "none"
    typer.echo(f"Ingested as session {body['session_id']} (quality flags: {flags})")
