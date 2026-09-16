"""Full-content, immutable snapshots of a page at the moment its content went live.

Separate from `HistoryEntry` (app/models/page.py), which stays a human-readable
audit trail (who/when/action/comment). A `PageVersion` captures everything needed
to render or bundle-pin an exact past state without touching the live page —
see app/storage/base.py's `PageRepository.get_versions`/`get_version`/`archive`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from app.models.page import ClassificationTriangle, PageStatus, Reference, TrustTier


class PageVersion(BaseModel):
    version_id: str  # "{page_id}-v{version_number}"
    page_id: str
    version_number: int
    action: str  # "create" | "edit"
    user_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    comment: Optional[str] = None

    title: str
    description: str
    content: str
    parent_id: Optional[str] = None
    references: list[Reference] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    classification: list[ClassificationTriangle] = Field(default_factory=list)
    status: PageStatus
    trust_tier: TrustTier
