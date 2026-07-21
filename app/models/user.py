from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PermissionLevel(str, Enum):
    read_only = "read_only"
    editor = "editor"
    admin = "admin"


class User(BaseModel):
    user_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    permission_level: PermissionLevel = PermissionLevel.read_only
    workflow_id: Optional[str] = None


class UserCreate(BaseModel):
    name: str
    permission_level: PermissionLevel = PermissionLevel.read_only
    workflow_id: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    permission_level: Optional[PermissionLevel] = None
    workflow_id: Optional[str] = None
