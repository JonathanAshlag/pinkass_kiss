from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Agent(BaseModel):
    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    user_id: str
    api_key_hash: str
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.utcnow())
    updated_at: datetime = Field(default_factory=lambda: datetime.utcnow())


class AgentCreate(BaseModel):
    name: str
    user_id: Optional[str] = None


class AgentCreateResponse(BaseModel):
    agent_id: str
    name: str
    user_id: str
    api_key: str


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None


class AgentRequestContext(BaseModel):
    agent: Agent
    user: object
    session_id: str
    request_id: str
