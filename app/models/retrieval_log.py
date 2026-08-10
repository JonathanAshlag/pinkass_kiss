from __future__ import annotations

import uuid
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


class RetrievalLogEntry(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str
    session_id: str
    mode: ConsumptionMode
    query: Optional[str] = None
    query_hash: Optional[str] = None
    raw_text: Optional[str] = None
    raw_text_logged: bool = False
    matched_spans: list[str] = Field(default_factory=list)
    page_ids: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    candidates_not_returned: list[MissCandidate] = Field(default_factory=list)
    returned_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    bundle_name: Optional[str] = None
    miss: bool = False
    cache_hit: bool = False
