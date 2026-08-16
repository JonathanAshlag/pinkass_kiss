from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ConsumptionMode(str, Enum):
    search = "search"
    fetch = "fetch"
    scan = "scan"
    bundle = "bundle"


class MissCandidate(BaseModel):
    page_id: str
    title: str
    score: float


class UnavailablePageLog(BaseModel):
    page_id: str
    reason: str


class RetrievalLogEntry(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: Optional[str] = None
    agent_id: str
    agent_name: str
    session_id: str
    mode: ConsumptionMode
    query: Optional[str] = None
    matched_spans: list[str] = Field(default_factory=list)
    page_ids: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    candidates_not_returned: list[MissCandidate] = Field(default_factory=list)
    unavailable: list[UnavailablePageLog] = Field(default_factory=list)
    latency_ms: Optional[float] = None
    bundle_name: Optional[str] = None
    miss: Optional[bool] = None
    error: Optional[str] = None
