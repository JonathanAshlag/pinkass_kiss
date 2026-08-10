from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from app.models.retrieval_log import MissCandidate


class SearchRequest(BaseModel):
    query: str


class SearchHit(BaseModel):
    page_id: str
    title: str
    description: str
    trust_tier: str
    score: float


class SearchResponse(BaseModel):
    miss: bool
    results: list[SearchHit] = []
    message: Optional[str] = None
    nearest_candidates: list[MissCandidate] = []


class FetchRequest(BaseModel):
    page_ids: list[str]


class FetchHit(BaseModel):
    page_id: str
    title: str
    content: str
    trust_tier: str


class UnavailablePage(BaseModel):
    page_id: str
    reason: Literal["not_found", "forbidden"]


class FetchResponse(BaseModel):
    results: list[FetchHit]
    unavailable: list[UnavailablePage] = []


class ScanRequest(BaseModel):
    text: str


class ScanMatch(BaseModel):
    page_id: str
    title: str
    description: str
    trust_tier: str


class ScanResponse(BaseModel):
    matches: list[ScanMatch]
