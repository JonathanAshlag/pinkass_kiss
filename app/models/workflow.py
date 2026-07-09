from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field

from app.models.page import HistoryEntry


class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    steps: list[str] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)


class WorkflowCreate(BaseModel):
    name: str
    description: str = ""
    steps: list[str] = Field(default_factory=list)


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[list[str]] = None
