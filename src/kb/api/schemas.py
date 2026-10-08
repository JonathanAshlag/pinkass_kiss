"""
Pydantic request/response models for src/kb/api/app.py.

NodeRead/ManifestRead use from_attributes=True so they can be built directly off the
SQLAlchemy File/Folder/Manifest objects kb.service returns (`NodeRead.model_validate(node)`).
"""

import uuid
from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


NodeType = Literal["file", "folder"]

# Columns only files have; folders carry just organizational fields.
FILE_ONLY_FIELDS = ("content", "aliases", "sources", "verified", "generated", "stale_after", "status")


class NodeCreate(BaseModel):
    """`type` picks the table. Files need `parent_id` (a folder) and `content`; folders
    may be roots and take none of the FILE_ONLY_FIELDS. `kind` defaults to manual."""

    type: NodeType = "file"
    parent_id: uuid.UUID | None = None
    kind: str | None = None
    agent_locked: bool | None = None
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

    @model_validator(mode="after")
    def fields_match_type(self) -> "NodeCreate":
        if self.type == "file":
            if self.parent_id is None:
                raise ValueError("a file needs a parent_id (files live inside folders)")
            if self.content is None:
                raise ValueError("a file needs content")
        else:
            extra = [f for f in FILE_ONLY_FIELDS if f in self.model_fields_set]
            if extra:
                raise ValueError(f"folders have no {', '.join(extra)}")
        return self


class NodeUpdate(BaseModel):
    """
    Partial update -- only fields the client actually sent are applied (see
    app.py's use of exclude_unset). Deliberately excludes `parent_id`: moving a
    node has to go through POST /nodes/{id}/move, which runs cycle detection;
    update_node does not. FILE_ONLY_FIELDS on a folder -> 422.
    """

    kind: str | None = None
    agent_locked: bool | None = None
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


class NodeSummary(BaseModel):
    """A file or folder; `type` says which. `status` is None for folders."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: NodeType = Field(validation_alias="node_type")
    parent_id: uuid.UUID | None
    kind: str
    agent_locked: bool
    title: str
    description: str | None
    tags: list[str]
    status: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class NodeRead(NodeSummary):
    """File-only fields are None for folders."""

    aliases: list[str] | None = None
    content: str | None = None
    sources: list | None = None
    verified: list | None = None
    generated: dict | None = None
    stale_after: datetime | None = None
    blob_key: str | None = None  # set when the original (PDF, ...) is retained: GET /nodes/{id}/raw
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
    """`node_id`: a file (just that file) or a folder (its whole subtree)."""

    node_id: uuid.UUID | None = None
    child_manifest_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> "ManifestMemberCreate":
        if (self.node_id is None) == (self.child_manifest_id is None):
            raise ValueError("exactly one of node_id or child_manifest_id must be set")
        return self


class ToolOutputRead(BaseModel):
    """A kb.retrieval.dci tool result: agent-ready text, plus whether it was cut at max_chars."""

    model_config = ConfigDict(from_attributes=True)

    text: str
    truncated: bool


class ManifestMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    node_id: uuid.UUID | None
    child_manifest_id: uuid.UUID | None


class IngestReportRead(BaseModel):
    """kb.ingest.IngestReport, with paths relative to the uploaded folder's parent. An
    ingest is all or nothing: if any file fails to convert, the response is a 422 whose
    `failed` lists every such file ({path, error}) and nothing is created."""

    root_id: uuid.UUID
    files_created: list[uuid.UUID]
    folders_created: list[uuid.UUID]
    skipped: list[str]


class IngestExtensionsRead(BaseModel):
    extensions: list[str]


class SearchHitRead(BaseModel):
    """A kb.retrieval.semantic.SearchHit: one chunk matching a semantic query. `path` and
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
