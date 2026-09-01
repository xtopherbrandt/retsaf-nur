"""``runcoach status`` -- render the API's health status and version.

Loads config (the guard, T008), calls the API client (T009), and prints the
health data as human-readable text. Request-failure handling (T016) guards
the network-layer call and the HTTP status code; malformed-body handling
(T011, this task) defensively parses the response body on top of that.
"""

import json

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

    try:
        body = resp.json()
        status_val = body["status"]
        version_val = body["version"]
        if not isinstance(status_val, str) or not isinstance(version_val, str):
            raise ValueError("unexpected field types in health response")
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        typer.echo(f"Error: the API's response could not be understood: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"API status: {status_val} (version {version_val})")
