"""``runcoach status`` -- render the API's health status and version.

Happy path only: loads config (the guard, T008), calls the API client
(T009), and prints the health data as human-readable text. Request-failure
handling (unreachable/non-2xx) and malformed-body handling are separate
tasks (T016, T011) that extend this same function afterward.
"""

import typer

from runcoach_cli import config
from runcoach_cli.api_client import get_health


def status() -> None:
    """Call GET /health on the configured API and print its status/version."""
    cfg = config.load_config()  # T008's guard -- exits before this point if config is missing
    resp = get_health(cfg.api_url)  # unguarded here -- T016 adds the try/except in the next wave
    body = resp.json()  # unguarded here -- T011 adds defensive parsing after T016
    typer.echo(f"API status: {body['status']} (version {body['version']})")
