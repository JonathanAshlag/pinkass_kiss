from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RequestType(str, Enum):
    create = "create"
    edit = "edit"
    delete = "delete"
    review = "review"


class RequestStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class RequestHistoryEntry(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str
    decision: str
    comment: Optional[str] = None


class Decision(BaseModel):
    decision: str  # "approve" or "reject"
    comment: Optional[str] = None


class ApprovalRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: RequestType
    page_id: str
    requested_by: str
    proposed_content: Optional[dict] = None
    workflow_id: str
    current_step: int = 0
    status: RequestStatus = RequestStatus.pending
    history: list[RequestHistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RequestCreate(BaseModel):
    type: RequestType
    page_id: str
    proposed_content: Optional[dict] = None
