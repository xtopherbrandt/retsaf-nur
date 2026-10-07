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

# FIT uploads can be up to 50MB (the API's own upload cap) - the 5s timeout
# above is tuned for the tiny /health payload and is nowhere near enough for
# a large multipart upload on a slow connection. Override just the upload
# call's timeout rather than raising the shared client's default, so /health
# and /status keep their fast, bounded 5s timeout.
UPLOAD_TIMEOUT = 60.0


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


def upload_fit(
    base_url: str, file_path: Path, resting_capture: bool = False
) -> httpx.Response:
    """Upload the FIT file at ``file_path`` via POST {base_url}/sessions.

    Only network-layer failures are translated into ApiUnreachableError.
    Non-2xx responses are returned unchanged - status-code and body-shape
    handling are the caller's job. No FIT-format validation happens here;
    the API owns that.

    ``resting_capture`` is F004's upload-time Tier-1 declaration, sent as a
    form field in the same multipart body as the file. It is always sent, and
    always spelled out as "true"/"false": an *empty* form value is not a bool
    the API's pydantic coercion accepts, so a blank part would be a 422 rather
    than a "no". One wire shape for both answers.
    """
    try:
        with open(file_path, "rb") as f:
            return client.post(
                f"{base_url}/sessions",
                files={"file": (file_path.name, f)},
                data={"resting_capture": "true" if resting_capture else "false"},
                timeout=UPLOAD_TIMEOUT,
            )
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def get_me(base_url: str) -> httpx.Response:
    """Call GET {base_url}/me (the athlete profile and HR anchors) and return the raw response.

    Only network-layer failures are translated into ApiUnreachableError; status and body are the
    caller's job.
    """
    try:
        return client.get(f"{base_url}/me")
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def patch_me(base_url: str, changes: dict) -> httpx.Response:
    """Send ``changes`` as the JSON body of PATCH {base_url}/me and return the raw response.

    ``changes`` maps profile field names to their new values, ``None`` clearing a field. It is sent
    as given; the API validates it. Only network-layer failures are translated into
    ApiUnreachableError.
    """
    try:
        return client.patch(f"{base_url}/me", json=changes)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc
