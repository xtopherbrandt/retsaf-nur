"""Thin httpx wrapper around the backend API's GET /health endpoint.

This is the only network call site in the CLI. It returns the raw
`httpx.Response` on success and never parses or validates the response
body - that boundary belongs to the caller (see status command / T010-T011).
"""

from pathlib import Path

import httpx

# A single bare-float timeout applies uniformly to connect/read/write/pool,
# bounding the whole call near 5s total - not a per-phase httpx.Timeout(...)
# object, which could total up to ~20s worst case.
client = httpx.Client(timeout=5.0)


class ApiUnreachableError(Exception):
    """Raised when the API cannot be reached (connection failure or timeout)."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        super().__init__(f"could not reach API at {base_url}")


def get_health(base_url: str) -> httpx.Response:
    """Call GET {base_url}/health and return the raw response.

    Only network-layer failures (connection refused, bounded timeout, and
    other transport-level errors such as a mid-response connection drop)
    are translated into ApiUnreachableError. Non-2xx responses are returned
    unchanged - status-code and body-shape handling are the caller's job.
    """
    try:
        return client.get(f"{base_url}/health")
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def upload_fit(base_url: str, file_path: Path) -> httpx.Response:
    """Upload the FIT file at ``file_path`` via POST {base_url}/sessions.

    Only network-layer failures are translated into ApiUnreachableError.
    Non-2xx responses are returned unchanged - status-code and body-shape
    handling are the caller's job. No FIT-format validation happens here;
    the API owns that.
    """
    try:
        with open(file_path, "rb") as f:
            return client.post(f"{base_url}/sessions", files={"file": (file_path.name, f)})
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc
