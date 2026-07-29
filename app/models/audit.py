from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    user_id: str
    client_application_id: Optional[str] = None
    client_session_id: Optional[str] = None


class AuditAction(str, Enum):
    ask = "ask"
    create_page = "create_page"
    edit_page = "edit_page"
    delete_page = "delete_page"


class AuditOutcome(str, Enum):
    success = "success"
    pending_approval = "pending_approval"
    denied = "denied"
    not_found = "not_found"
    error = "error"


class AuditLogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: AuditAction
    user_context: UserContext
    resource_id: Optional[str] = None
    outcome: AuditOutcome
    result: Optional[dict] = None
    latency_ms: Optional[float] = None
    request_path: str = ""
