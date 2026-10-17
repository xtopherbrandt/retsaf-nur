"""Thin httpx wrapper around the backend API's endpoints.

This is the CLI's only call site to the runcoach API (Garmin Connect is reached through
``garmin_connect``). It returns the raw
`httpx.Response` on success and never parses or validates the response
body - that boundary belongs to the caller (see status command / T010-T011).
"""

import uuid
from pathlib import Path
from urllib.parse import quote

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


# A page of session summaries is computed from each session's records on read; before the
# records index a 200-row page of long runs took about 15 s, so the list call gets its own
# timeout rather than the shared client's 5 s (as the upload call does).
LIST_TIMEOUT = 30.0


def list_sessions(base_url: str, since: str | None, limit: int) -> httpx.Response:
    """Call GET {base_url}/sessions?limit=...&since=... and return the raw response.

    ``since`` is the previous page's ``next_cursor``, passed through untouched; ``None`` omits
    the parameter. Only network-layer failures are translated into ApiUnreachableError; status
    and body are the caller's job.
    """
    params: dict[str, str | int] = {"limit": limit}
    if since is not None:
        params["since"] = since
    try:
        return client.get(f"{base_url}/sessions", params=params, timeout=LIST_TIMEOUT)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


# An archive upload may be up to 1 GiB (the API's import cap), so it gets far more than the 60 s a
# 50 MB FIT upload gets.
IMPORT_UPLOAD_TIMEOUT = 600.0


def start_import(base_url: str, path: Path, upload_name: str | None = None) -> httpx.Response:
    """Upload the zip at ``path`` as the multipart part ``file`` of POST {base_url}/imports.

    ``upload_name`` is the file name sent with the part (the API stores it as ``archive_name``);
    it defaults to ``path``'s own name. Only network-layer failures are translated into
    ApiUnreachableError; status and body are the caller's job.
    """
    try:
        with open(path, "rb") as f:
            return client.post(
                f"{base_url}/imports",
                files={"file": (upload_name or path.name, f, "application/zip")},
                timeout=IMPORT_UPLOAD_TIMEOUT,
            )
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def get_import(base_url: str, import_id: str) -> httpx.Response:
    """Call GET {base_url}/imports/{import_id} and return the raw response.

    The body carries the job's per-file rows, which grow with the archive, so the call uses
    ``LIST_TIMEOUT``. Only network-layer failures are translated into ApiUnreachableError.
    """
    try:
        return client.get(f"{base_url}/imports/{import_id}", timeout=LIST_TIMEOUT)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def list_imports(base_url: str) -> httpx.Response:
    """Call GET {base_url}/imports (newest first, without per-file rows) and return the raw response.

    Only network-layer failures are translated into ApiUnreachableError.
    """
    try:
        return client.get(f"{base_url}/imports", timeout=LIST_TIMEOUT)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def add_hr_history_entry(base_url: str, field: str, value: int, effective_from: str) -> httpx.Response:
    """POST {base_url}/me/hr-history with ``{field, value, effective_from}`` and return the raw response.

    The body is sent as given; the API validates it (201 new, 200 replaced, 422 refused). Only
    network-layer failures are translated into ApiUnreachableError.
    """
    body = {"field": field, "value": value, "effective_from": effective_from}
    try:
        return client.post(f"{base_url}/me/hr-history", json=body)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def list_hr_history(base_url: str) -> httpx.Response:
    """Call GET {base_url}/me/hr-history (every dated HR entry) and return the raw response.

    Only network-layer failures are translated into ApiUnreachableError.
    """
    try:
        return client.get(f"{base_url}/me/hr-history")
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def delete_hr_history_entry(base_url: str, entry_id: int) -> httpx.Response:
    """Call DELETE {base_url}/me/hr-history/{entry_id} and return the raw response (204, or 404).

    Only network-layer failures are translated into ApiUnreachableError.
    """
    try:
        return client.delete(f"{base_url}/me/hr-history/{entry_id}")
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


# A recompute reads the session's records and every stored file's HR values, then writes the load in
# one transaction, so it gets more than the shared client's 5 s (as the list call does).
RECOMPUTE_TIMEOUT = 30.0


def recompute_session_load(base_url: str, session_id: str) -> httpx.Response:
    """Call POST {base_url}/sessions/{session_id}/load/recompute and return the raw response.

    The body is the session's ``SessionLoad`` plus ``changed``. Only network-layer failures
    (timeouts included) are translated into ApiUnreachableError; status and body are the caller's job.
    """
    try:
        return client.post(f"{base_url}/sessions/{session_id}/load/recompute", timeout=RECOMPUTE_TIMEOUT)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def upload_fit_bytes(base_url: str, data: bytes, *, runs_and_captures_only: bool = True) -> httpx.Response:
    """Upload ``data`` as the multipart part ``file`` of POST {base_url}/sessions.

    For bytes read from a downloaded zip entry. The part's file name is generated per call
    (``garmin-<32 hex>.fit``), never taken from the entry: the API screens a file by its content,
    and an entry name is input the CLI does not pass on. ``runs_and_captures_only`` is sent as a
    form field spelled "true"/"false" (as ``upload_fit`` spells ``resting_capture``, which is not
    sent here). Only network-layer failures are translated into ApiUnreachableError; status and
    body are the caller's job.
    """
    name = f"garmin-{uuid.uuid4().hex}.fit"
    try:
        return client.post(
            f"{base_url}/sessions",
            files={"file": (name, data, "application/octet-stream")},
            data={"runs_and_captures_only": "true" if runs_and_captures_only else "false"},
            timeout=UPLOAD_TIMEOUT,
        )
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def get_openapi(base_url: str) -> httpx.Response:
    """Call GET {base_url}/openapi.json and return the raw response.

    The served schema can be large, so the call gets the list call's timeout. Only network-layer
    failures are translated into ApiUnreachableError; status and body are the caller's job.
    """
    try:
        return client.get(f"{base_url}/openapi.json", timeout=LIST_TIMEOUT)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def put_race_tags(base_url: str, items: list[dict]) -> httpx.Response:
    """Send ``items`` as ``{"items": items}``, the JSON body of PUT {base_url}/race-tags, and return
    the raw response.

    The API matches up to 500 items against the stored sessions in one call, so the call gets the
    list call's timeout. The items are sent as given; the API validates them. Only network-layer
    failures (timeouts included) are translated into ApiUnreachableError.
    """
    try:
        return client.put(f"{base_url}/race-tags", json={"items": items}, timeout=LIST_TIMEOUT)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def get_state(base_url: str, as_of: str | None) -> httpx.Response:
    """Call GET {base_url}/state (threshold pace from the tagged races and threshold workouts) and
    return the raw response.

    ``as_of`` is sent as given; ``None`` omits it (the API's default is now). The API replays every
    tagged race on each read, so the call gets the list call's timeout. Only network-layer failures
    are translated into ApiUnreachableError; status and body are the caller's job.
    """
    params = {} if as_of is None else {"as_of": as_of}
    try:
        return client.get(f"{base_url}/state", params=params, timeout=LIST_TIMEOUT)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def put_session_laps(base_url: str, session_id: str, data: bytes) -> httpx.Response:
    """Send ``data`` (one FIT file) as the multipart part ``file`` of PUT
    {base_url}/sessions/{session_id}/laps and return the raw response.

    ``session_id`` is escaped into one path segment. The part's file name is generated per call, as
    ``upload_fit_bytes`` names it. The API decodes the whole file, so the call gets the upload
    timeout. Only network-layer failures (timeouts included) are translated into
    ApiUnreachableError; status and body are the caller's job.
    """
    name = f"garmin-{uuid.uuid4().hex}.fit"
    try:
        return client.put(
            f"{base_url}/sessions/{quote(session_id, safe='')}/laps",
            files={"file": (name, data, "application/octet-stream")},
            timeout=UPLOAD_TIMEOUT,
        )
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc


def post_laps_candidates(base_url: str, items: list[dict]) -> httpx.Response:
    """Send ``items`` as ``{"items": items}``, the JSON body of POST {base_url}/laps/candidates, and
    return the raw response.

    The API matches up to 500 items against the stored sessions in one read-only call, so the call
    gets the list call's timeout. The items are sent as given; the API validates them. Only
    network-layer failures (timeouts included) are translated into ApiUnreachableError.
    """
    try:
        return client.post(f"{base_url}/laps/candidates", json={"items": items}, timeout=LIST_TIMEOUT)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(base_url) from exc
