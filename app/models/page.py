from __future__ import annotations

import uuid
from datetime import datetime, date, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PageStatus(str, Enum):
    draft = "draft"
    pending_approval = "pending_approval"
    published = "published"
    rejected = "rejected"
    deleted = "deleted"


class TrustTier(str, Enum):
    unverified = "unverified"
    source_checked = "source_checked"   # reserved for future automation
    verified = "verified"


class ReferenceType(str, Enum):
    file = "file"
    page = "page"


class Reference(BaseModel):
    type: ReferenceType
    file_id: Optional[str] = None
    page_id: Optional[str] = None


class HistoryEntry(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str
    action: str
    diff: Optional[str] = None
    snapshot: Optional[str] = None
    comment: Optional[str] = None


class Page(BaseModel):
    page_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    parent_id: Optional[str] = None
    references: list[Reference] = Field(default_factory=list)
    content: str = ""
    history: list[HistoryEntry] = Field(default_factory=list)
    next_approval_date: Optional[date] = None
    status: PageStatus = PageStatus.draft
    trust_tier: TrustTier = TrustTier.unverified
    verified_content_hash: Optional[str] = None  # sha256(content) at time of last tier promotion
    verified_at: Optional[datetime] = None
    verified_by: Optional[str] = None            # user_id or "system"
    inbound_link_count: int = 0                  # cached by scheduler; not user-editable
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PageCreate(BaseModel):
    title: str
    parent_id: Optional[str] = None
    content: str = ""
    references: list[Reference] = Field(default_factory=list)
    next_approval_date: Optional[date] = None


class PageUpdate(BaseModel):
    title: Optional[str] = None
    parent_id: Optional[str] = None
    content: Optional[str] = None
    references: Optional[list[Reference]] = None
    next_approval_date: Optional[date] = None
