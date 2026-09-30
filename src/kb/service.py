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

from kb import dal, okf
from kb.models import File, Manifest, ManifestMember

# Re-exported as-is: dal.py's own exceptions are this layer's exceptions too.
ManifestCycleError = dal.ManifestCycleError
TreeCycleError = dal.TreeCycleError


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


def resolve_manifest(session: Session, manifest_id: uuid.UUID) -> set[File]:
    return dal.resolve_manifest(session, manifest_id)
