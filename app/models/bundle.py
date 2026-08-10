from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ContentForm(str, Enum):
    description = "description"
    full_info = "full_info"


class BundleEntry(BaseModel):
    page_id: str
    content_form: ContentForm


class Bundle(BaseModel):
    name: str
    entries: list[BundleEntry] = Field(default_factory=list)
    created_by: str
    created_at: datetime
    updated_at: datetime


class BundleEntriesUpdate(BaseModel):
    entries: list[BundleEntry]


class BundleFetchResponse(BaseModel):
    bundle_name: str
    rendered_text: str
    entries: list[BundleEntry]
