"""
KB service layer: the single chokepoint every consumer (REST API, agents, ingestion
jobs, future services) goes through instead of calling kb.dal directly.

Right now this is mostly a thin 1:1 passthrough to kb.dal -- authz, concurrency
checks, and real validation hooks are explicitly deferred (see CLAUDE.md). The point
of having this layer already is so those hooks have one place to land later without
having to touch every caller. get_warnings is the one piece of real logic here: it
composes kb.okf's advisory checks into a single report for a node.

All functions take an explicit SQLAlchemy `Session`, same convention as kb.dal --
session lifecycle (opening, committing, closing) is the caller's job, not this
module's.
"""

import uuid

from sqlalchemy.orm import Session

from kb import dal, dci, okf
from kb.models import File, Manifest, ManifestMember

# Re-exported as-is: dal.py's/dci.py's own exceptions are this layer's exceptions too.
ManifestCycleError = dal.ManifestCycleError
TreeCycleError = dal.TreeCycleError
PatternError = dci.PatternError


def get_warnings(session: Session, node: File) -> list[str]:
    """
    Composes kb.okf's advisory checks into one report for `node`. Never raises --
    every check it calls is itself advisory-only.
    """
    warnings: list[str] = []
    warnings.extend(
        f"broken link: {link}" for link in okf.find_broken_links(session, node.id)
    )
    warnings.extend(okf.validate_frontmatter(node))
    warnings.extend(
        f"unresolved footnote: [^{label}]"
        for label in okf.find_unresolved_footnotes(node)
    )
    if okf.is_stale(node):
        warnings.append(f"stale: stale_after ({node.stale_after.isoformat()}) has passed")
    return warnings


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


def get_node(session: Session, node_id: uuid.UUID, *, include_deleted: bool = False) -> File | None:
    return dal.get_node(session, node_id, include_deleted=include_deleted)


def list_children(
    session: Session, parent_id: uuid.UUID | None, *, include_deleted: bool = False
) -> list[File]:
    return dal.list_children(session, parent_id, include_deleted=include_deleted)


def list_descendants(
    session: Session, node_id: uuid.UUID, *, include_deleted: bool = False
) -> list[File]:
    return dal.list_descendants(session, node_id, include_deleted=include_deleted)


def create_file(
    session: Session,
    *,
    parent_id: uuid.UUID | None,
    kind: str,
    title: str,
    content: str | None = None,
    **other_columns,
) -> File:
    return dal.create_file(
        session, parent_id=parent_id, kind=kind, title=title, content=content, **other_columns
    )


def update_node(session: Session, node_id: uuid.UUID, **fields) -> File:
    return dal.update_node(session, node_id, **fields)


def move_node(session: Session, node_id: uuid.UUID, new_parent_id: uuid.UUID | None) -> File:
    return dal.move_node(session, node_id, new_parent_id)


def delete_node(session: Session, node_id: uuid.UUID, *, cascade: bool = True) -> None:
    dal.delete_node(session, node_id, cascade=cascade)


def restore_node(session: Session, node_id: uuid.UUID) -> File:
    return dal.restore_node(session, node_id)


def query_metadata(
    session: Session,
    *,
    tags: list[str] | None = None,
    status: str | None = None,
    kind: str | None = None,
    parent_id: uuid.UUID | None = None,
    include_deleted: bool = False,
) -> list[File]:
    return dal.query_metadata(
        session,
        tags=tags,
        status=status,
        kind=kind,
        parent_id=parent_id,
        include_deleted=include_deleted,
    )


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------


def create_manifest(session: Session, name: str, description: str | None = None) -> Manifest:
    return dal.create_manifest(session, name, description)


def get_manifest(
    session: Session, manifest_id: uuid.UUID, *, include_deleted: bool = False
) -> Manifest | None:
    return dal.get_manifest(session, manifest_id, include_deleted=include_deleted)


def list_manifests(session: Session, *, include_deleted: bool = False) -> list[Manifest]:
    return dal.list_manifests(session, include_deleted=include_deleted)


def add_manifest_member(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    file_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
) -> ManifestMember:
    return dal.add_manifest_member(
        session, manifest_id, file_id=file_id, child_manifest_id=child_manifest_id
    )


def remove_manifest_member(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    file_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
) -> None:
    dal.remove_manifest_member(
        session, manifest_id, file_id=file_id, child_manifest_id=child_manifest_id
    )


def list_manifest_members(session: Session, manifest_id: uuid.UUID) -> list[ManifestMember]:
    return dal.list_manifest_members(session, manifest_id)


def resolve_manifest(session: Session, manifest_id: uuid.UUID) -> set[File]:
    return dal.resolve_manifest(session, manifest_id)


# --------------------------------------------------------------------------
# Direct corpus interaction (agent-facing ls/grep/read, see kb.dci)
# --------------------------------------------------------------------------


def list_paths(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    under: str | None = None,
    recursive: bool = False,
    max_chars: int = dci.DEFAULT_MAX_CHARS,
) -> dci.ToolOutput:
    return dci.list_paths(
        session, manifest_id, under=under, recursive=recursive, max_chars=max_chars
    )


def search_lines(
    session: Session,
    manifest_id: uuid.UUID,
    patterns: str | list[str],
    *,
    paths: list[str] | None = None,
    ignore_case: bool = False,
    context: int = 0,
    files_only: bool = False,
    max_chars: int = dci.DEFAULT_MAX_CHARS,
) -> dci.ToolOutput:
    return dci.search_lines(
        session,
        manifest_id,
        patterns,
        paths=paths,
        ignore_case=ignore_case,
        context=context,
        files_only=files_only,
        max_chars=max_chars,
    )


def read_lines(
    session: Session,
    manifest_id: uuid.UUID,
    path: str,
    *,
    offset: int = 1,
    limit: int = dci.DEFAULT_READ_LIMIT,
    max_chars: int = dci.DEFAULT_MAX_CHARS,
) -> dci.ToolOutput:
    return dci.read_lines(
        session, manifest_id, path, offset=offset, limit=limit, max_chars=max_chars
    )


# --------------------------------------------------------------------------
# Raw originals (kb.blobs): the source bytes of converted uploads (PDF, ...)
# --------------------------------------------------------------------------


def get_original(session: Session, node_id: uuid.UUID) -> tuple[bytes, str, str] | None:
    """(data, mime, filename) of a node's retained original, or None if it has none
    (markdown nodes, or no blob store configured). Raises ValueError for no such node."""
    from kb import blobs

    node = dal.get_node(session, node_id)
    if node is None:
        raise ValueError(f"no such node: {node_id}")
    store = blobs.get_blob_store()
    if not node.blob_key or store is None:
        return None
    data = blobs.get_original(store, node.blob_key)
    if data is None:
        return None
    resource = next((s.get("resource") for s in node.sources if isinstance(s, dict)), None)
    filename = resource.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if resource else node.title
    return data, node.blob_mime_type or "application/octet-stream", filename


# --------------------------------------------------------------------------
# Semantic index (derived kb_chunks, see kb.index). Imported lazily so that importing
# kb.service never requires the LangChain/pgvector stack.
# --------------------------------------------------------------------------


def semantic_search(
    session: Session,
    manifest_id: uuid.UUID,
    query: str,
    *,
    k: int = 8,
    tags: list[str] | None = None,
    status: str | None = None,
    store=None,
):
    """-> list[kb.index.search.SearchHit], best first."""
    from kb.index import search

    return search.semantic_search(
        session, manifest_id, query, k=k, tags=tags, status=status, store=store
    )


# Unlike the functions above, the indexing functions take no Session: they open their
# own (after the caller's writes are committed), since the index is derived state.


def index_files(file_ids, *, store=None, session_factory=None):
    """(Re)index the given node ids; deleted/missing/content-less ones get unindexed.
    -> kb.index.sync.IndexResult. Never raises per file."""
    from kb.index import sync

    return sync.index_files(file_ids, store=store, session_factory=session_factory)


def unindex_files(file_ids, *, store=None) -> int:
    from kb.index import sync

    return sync.unindex_files(file_ids, store=store)


def reindex_all(*, store=None, session_factory=None):
    from kb.index import sync

    return sync.reindex_all(store=store, session_factory=session_factory)
