from fastapi import FastAPI, File, HTTPException, UploadFile

from runcoach_api import __version__, db
from runcoach_api.ingestion.exceptions import (
    DuplicateSessionError,
    FitParseFailure,
    NotAFitFileError,
    OversizedUploadError,
)
from runcoach_api.ingestion.pipeline import ingest_fit_bytes
from runcoach_api.schemas import HealthResponse, IngestResponse

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(title="Run Coaching API")


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
    except DuplicateSessionError as exc:
        raise HTTPException(
            409, f"already ingested as session {exc.existing_session_id}"
        ) from exc
    except OversizedUploadError as exc:
        raise HTTPException(413, str(exc)) from exc
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
