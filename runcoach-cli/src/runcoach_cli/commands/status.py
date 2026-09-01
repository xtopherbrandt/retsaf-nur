"""``runcoach status`` -- render the API's health status and version.

Loads config (the guard, T008), calls the API client (T009), and prints the
health data as human-readable text. Request-failure handling (T016, this
task) guards the network-layer call and the HTTP status code; malformed-body
handling (T011) is a separate task that extends this same function further.
"""

import typer

from runcoach_cli import config
from runcoach_cli.api_client import ApiUnreachableError, get_health


def status() -> None:
    """Call GET /health on the configured API and print its status/version."""
    cfg = config.load_config()  # T008's guard -- exits before this point if config is missing

    try:
        resp = get_health(cfg.api_url)
    except ApiUnreachableError:
        typer.echo(
            f"Error: could not reach API at {cfg.api_url}. "
            f"Is it running? Start it with `runcoach-api`.",
            err=True,
        )
        raise typer.Exit(code=1) from None

    if resp.status_code != 200:
        typer.echo(f"Error: API returned {resp.status_code}: {resp.text}", err=True)
        raise typer.Exit(code=1)

    body = resp.json()  # unguarded here -- T011 adds defensive parsing on top
    typer.echo(f"API status: {body['status']} (version {body['version']})")
