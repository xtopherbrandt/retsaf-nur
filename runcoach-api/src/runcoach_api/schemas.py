from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class IngestResponse(BaseModel):
    session_id: str
    quality_flags: list[str]
