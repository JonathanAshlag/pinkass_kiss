from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Optional, Union

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


class CreatePayload(BaseModel):
    type: Literal["create"] = "create"
    title: str
    description: str = ""
    content: str = ""
    classification: Optional[list] = None
    aliases: Optional[list] = None
    tags: Optional[list] = None


class EditPayload(BaseModel):
    type: Literal["edit"] = "edit"
    title: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    parent_id: Optional[str] = None
    references: Optional[list] = None
    aliases: Optional[list] = None
    tags: Optional[list] = None
    classification: Optional[list] = None
    next_approval_date: Optional[str] = None


class DeletePayload(BaseModel):
    type: Literal["delete"] = "delete"


class ReviewPayload(BaseModel):
    type: Literal["review"] = "review"
    title: Optional[str] = None
    content: Optional[str] = None
    trust_tier: Optional[str] = None


PageMutationPayload = Annotated[
    Union[CreatePayload, EditPayload, DeletePayload, ReviewPayload],
    Field(discriminator="type"),
]


class ApprovalRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: RequestType
    page_id: str
    requested_by: str
    proposed_content: Optional[PageMutationPayload] = None
    workflow_id: str
    current_step: int = 0
    status: RequestStatus = RequestStatus.pending
    history: list[RequestHistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RequestCreate(BaseModel):
    type: RequestType
    page_id: str
    proposed_content: Optional[dict] = None
