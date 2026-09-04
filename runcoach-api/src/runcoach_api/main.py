from fastapi import FastAPI, File, HTTPException, UploadFile
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from runcoach_api import __version__, db
from runcoach_api.ingestion.exceptions import (
    DuplicateSessionError,
    FitParseFailure,
    MissingCanonicalFieldError,
    NotAFitFileError,
)
from runcoach_api.ingestion.pipeline import ingest_fit_bytes
from runcoach_api.schemas import HealthResponse, IngestResponse

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# The precheck compares the request's declared Content-Length -- which
# includes multipart boundary markers and part headers on top of the
# file's own bytes -- against MAX_UPLOAD_BYTES, which caps the *file's*
# byte count exactly (as create_session's own read(MAX_UPLOAD_BYTES + 1)
# check enforces). A single-field, single-file /sessions upload needs at
# most a few hundred bytes of multipart framing, so this allowance is
# generous headroom for that framing, not a second size cap: a file
# right at MAX_UPLOAD_BYTES must still pass the precheck and reach
# create_session's exact byte-for-byte check, rather than being
# falsely rejected a few bytes early by request-level framing overhead.
_MULTIPART_FRAMING_ALLOWANCE_BYTES = 64 * 1024


class ContentLengthLimitMiddleware:
    """Rejects an oversized upload by its declared ``Content-Length``
    header before Starlette's multipart parser ever runs.

    M1 (sprint-002 review): ``create_session``'s own
    ``file.file.read(MAX_UPLOAD_BYTES + 1)`` guard only stops FIT
    parsing/DB writes on an oversized file -- it runs too late to stop
    the buffering itself. By the time that line executes, FastAPI has
    already resolved the ``UploadFile = File(...)`` dependency, which
    means Starlette's ``MultiPartParser`` has already consumed the
    entire request body and spooled it to a ``SpooledTemporaryFile``
    (spilling to disk past its 1MB in-memory threshold -- confirmed by
    reading the vendored source, ``.venv/.../starlette/formparsers.py``:
    ``MultiPartParser.max_part_size`` caps the size of an individual
    multipart *part*, never the total size of a file part, so nothing
    in Starlette itself stops an arbitrarily large file body from being
    received and buffered before a route ever runs).

    This middleware runs ahead of routing/parsing and rejects a request
    whose ``Content-Length`` already exceeds the cap (plus a small
    multipart-framing allowance -- see
    ``_MULTIPART_FRAMING_ALLOWANCE_BYTES``) -- for that request,
    ``receive()`` (and therefore multipart buffering) never happens. It
    does not close the full gap: a client using chunked
    transfer-encoding sends no ``Content-Length`` header at all and is
    not caught here. That residual gap is accepted rather than closed
    with a full ASGI-level streaming body-size limit -- see F003's
    Decision Log, 2026-09-03 "M1 upload buffering" entry for why, given
    this feature's local-first single-athlete deployment model.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.precheck_ceiling = max_bytes + _MULTIPART_FRAMING_ALLOWANCE_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                declared_bytes = None
            if declared_bytes is not None and declared_bytes > self.precheck_ceiling:
                response = PlainTextResponse(
                    f"upload exceeds {self.max_bytes} byte limit", status_code=413
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


app = FastAPI(title="Run Coaching API")
app.add_middleware(ContentLengthLimitMiddleware, max_bytes=MAX_UPLOAD_BYTES)


@app.on_event("startup")
def on_startup() -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
    finally:
        conn.close()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@app.post("/sessions", response_model=IngestResponse, status_code=201)
def create_session(file: UploadFile = File(...)) -> IngestResponse:
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_BYTES} byte limit")
    try:
        result = ingest_fit_bytes(raw)
    except NotAFitFileError as exc:
        raise HTTPException(400, f"not a valid FIT file: {exc}") from exc
    except FitParseFailure as exc:
        raise HTTPException(400, f"file could not be parsed: {exc}") from exc
    except MissingCanonicalFieldError as exc:
        raise HTTPException(400, f"{exc.field} could not be determined: {exc}") from exc
    except DuplicateSessionError as exc:
        raise HTTPException(
            409, f"already ingested as session {exc.existing_session_id}"
        ) from exc
    return IngestResponse(session_id=result.session_id, quality_flags=result.quality_flags)


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    conn = db.get_connection()
    try:
        detail = db.get_session_detail(conn, session_id)
    finally:
        conn.close()

    if detail is None:
        raise HTTPException(404, f"session {session_id} not found")

    return detail
