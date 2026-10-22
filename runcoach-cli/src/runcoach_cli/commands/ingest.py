"""``runcoach ingest`` -- upload a FIT file to the API and render the result.

Loads config (T008's guard), calls the API client's upload_fit (T018), and
prints the returned session_id / quality_flags as human-readable text (with the
replaced session's id when the API names one), or ``Skipped: <reason>`` when the
API's 200 says the file stored nothing. This
command does no FIT-parsing or format validation itself -- that boundary
belongs to the API (POST /sessions).
"""

from pathlib import Path

import typer

from runcoach_cli import config
from runcoach_cli.api_client import ApiUnreachableError, upload_fit
from runcoach_cli.commands.sessions import _printable


def ingest(
    path: Path = typer.Argument(..., exists=True),
    resting_capture: bool = typer.Option(
        False,
        "--resting-capture",
        help=(
            "Declare this file a resting-HRV capture, overriding the "
            "resting_hrv_profile_names config for this upload only. For a "
            "capture recorded on an activity profile that is not configured "
            "-- a one-off, or a historical file."
        ),
    ),
) -> None:
    """Upload the FIT file at PATH to the configured API's /sessions endpoint."""
    cfg = config.load_config()  # T008's guard -- exits before this point if config is missing

    try:
        resp = upload_fit(cfg.api_url, path, resting_capture=resting_capture)
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
    if body.get("skipped"):
        # A file that stored nothing. Every reason prints the same way, so a reason the
        # CLI does not know by name still prints, with control characters as ?.
        typer.echo(f"Skipped: {_printable(str(body.get('reason', 'unknown')))}")
        return

    flags = ", ".join(body.get("quality_flags", [])) or "none"
    line = f"Ingested as session {body['session_id']} (quality flags: {flags})"
    replaced = body.get("replaced_session_id")
    if replaced:
        line += f"; replaces {_printable(str(replaced))}"
    typer.echo(line)
