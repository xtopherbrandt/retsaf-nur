from fastapi import FastAPI

from runcoach_api import __version__
from runcoach_api.schemas import HealthResponse

app = FastAPI(title="Run Coaching API")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
