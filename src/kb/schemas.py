"""
Pydantic request/response models for src/kb/api.py.

FileRead/ManifestRead use from_attributes=True so they can be built directly off the
SQLAlchemy File/Manifest objects kb.service returns (`FileRead.model_validate(node)`).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class FileCreate(BaseModel):
    parent_id: uuid.UUID | None = None
    kind: str = "file"
    title: str
    content: str | None = None
    aliases: list[str] | None = None
    description: str | None = None
    tags: list[str] | None = None
    sources: list | None = None
    verified: list | None = None
    generated: dict | None = None
    stale_after: datetime | None = None
    status: str | None = None


class FileUpdate(BaseModel):
    """
    Partial update -- only fields the client actually sent are applied (see
    api.py's use of exclude_unset). Deliberately excludes `parent_id`: moving a
    node has to go through POST /files/{id}/move, which runs cycle detection;
    update_node does not.
    """

    kind: str | None = None
    title: str | None = None
    content: str | None = None
    aliases: list[str] | None = None
    description: str | None = None
    tags: list[str] | None = None
    sources: list | None = None
    verified: list | None = None
    generated: dict | None = None
    stale_after: datetime | None = None
    status: str | None = None


class MoveRequest(BaseModel):
    new_parent_id: uuid.UUID | None = None


class FileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    kind: str
    title: str
    description: str | None
    tags: list[str]
    status: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class FileRead(FileSummary):
    aliases: list[str]
    content: str | None
    sources: list
    verified: list
    generated: dict | None
    stale_after: datetime | None
    blob_key: str | None = None  # set when the original (PDF, ...) is retained: GET /files/{id}/raw
    blob_mime_type: str | None = None
    warnings: list[str] = []


class ManifestCreate(BaseModel):
    name: str
    description: str | None = None


class ManifestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ManifestMemberCreate(BaseModel):
    file_id: uuid.UUID | None = None
    child_manifest_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> "ManifestMemberCreate":
        if (self.file_id is None) == (self.child_manifest_id is None):
            raise ValueError("exactly one of file_id or child_manifest_id must be set")
        return self


class ToolOutputRead(BaseModel):
    """A kb.dci tool result: agent-ready text, plus whether it was cut at max_chars."""

    model_config = ConfigDict(from_attributes=True)

    text: str
    truncated: bool


class ManifestMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_id: uuid.UUID | None
    child_manifest_id: uuid.UUID | None


class IngestFailure(BaseModel):
    path: str
    error: str


class IngestReportRead(BaseModel):
    """kb.ingest.IngestReport, with paths relative to the uploaded folder's parent."""

    root_id: uuid.UUID
    files_created: list[uuid.UUID]
    folders_created: list[uuid.UUID]
    skipped: list[str]
    failed: list[IngestFailure]


class IngestExtensionsRead(BaseModel):
    extensions: list[str]


class SearchHitRead(BaseModel):
    """A kb.index.search.SearchHit: one chunk matching a semantic query. `path` and
    `start_line` can be passed straight to GET /manifests/{id}/read (path, offset)."""

    model_config = ConfigDict(from_attributes=True)

    file_id: uuid.UUID
    path: str
    title: str
    heading: str | None
    start_line: int
    end_line: int
    snippet: str
    score: float  # cosine similarity, higher = closer


class ReindexRequest(BaseModel):
    """Node ids to (re)index; omitted/null means a full reindex of the whole KB."""

    file_ids: list[uuid.UUID] | None = None


class IndexFailure(BaseModel):
    file_id: uuid.UUID
    error: str


class IndexResultRead(BaseModel):
    num_added: int
    num_updated: int
    num_skipped: int
    num_deleted: int
    failed: list[IndexFailure]
