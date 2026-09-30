"""
Postgres DAL primitives for the knowledge base's virtual filesystem.

This module is deliberately unaware of OKF/agent semantics: it treats
frontmatter-ish column *values* (tags, sources, verified, status,
stale_after, ...) as opaque data it stores and filters on, never as
something it validates or interprets.

All functions take an explicit SQLAlchemy `Session` -- no module-level
session/engine usage, so this stays easy to test and to embed in whatever
service layer calls it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from kb.models import File, Manifest, ManifestMember


class ManifestCycleError(ValueError):
    """Raised when adding a nested manifest member would create a cycle."""


class TreeCycleError(ValueError):
    """Raised when moving a node would place it under its own subtree."""


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


def get_node(session: Session, node_id: uuid.UUID, *, include_deleted: bool = False) -> File | None:
    node = session.get(File, node_id)
    if node is None:
        return None
    if not include_deleted and node.deleted_at is not None:
        return None
    return node


def list_children(
    session: Session,
    parent_id: uuid.UUID | None,
    *,
    include_deleted: bool = False,
) -> list[File]:
    stmt = select(File).where(File.parent_id == parent_id)
    if not include_deleted:
        stmt = stmt.where(File.deleted_at.is_(None))
    return list(session.scalars(stmt))


def list_descendants(
    session: Session, node_id: uuid.UUID, *, include_deleted: bool = False
) -> list[File]:
    """All nodes transitively under `node_id` (not including `node_id` itself)."""
    base = select(File).where(File.parent_id == node_id).cte("descendants", recursive=True)
    child = aliased(File)
    base = base.union_all(select(child).where(child.parent_id == base.c.id))

    stmt = select(File).join(base, File.id == base.c.id)
    if not include_deleted:
        stmt = stmt.where(File.deleted_at.is_(None))
    return list(session.scalars(stmt))


def create_file(
    session: Session,
    *,
    parent_id: uuid.UUID | None,
    kind: str,
    title: str,
    content: str | None = None,
    **other_columns,
) -> File:
    node = File(parent_id=parent_id, kind=kind, title=title, content=content, **other_columns)
    session.add(node)
    session.flush()
    return node


def update_node(session: Session, node_id: uuid.UUID, **fields) -> File:
    node = get_node(session, node_id, include_deleted=True)
    if node is None:
        raise ValueError(f"no such node: {node_id}")
    for key, value in fields.items():
        setattr(node, key, value)
    session.flush()
    return node


def move_node(session: Session, node_id: uuid.UUID, new_parent_id: uuid.UUID | None) -> File:
    if new_parent_id == node_id:
        raise TreeCycleError("a node cannot be its own parent")
    if new_parent_id is not None:
        descendant_ids = {d.id for d in list_descendants(session, node_id, include_deleted=True)}
        if new_parent_id in descendant_ids:
            raise TreeCycleError("cannot move a node under its own descendant")

    return update_node(session, node_id, parent_id=new_parent_id)


def delete_node(session: Session, node_id: uuid.UUID, *, cascade: bool = True) -> None:
    node = get_node(session, node_id, include_deleted=True)
    if node is None:
        raise ValueError(f"no such node: {node_id}")

    now = datetime.now(timezone.utc)
    node.deleted_at = now

    if cascade:
        for descendant in list_descendants(session, node_id, include_deleted=False):
            descendant.deleted_at = now

    session.flush()


def restore_node(session: Session, node_id: uuid.UUID) -> File:
    """
    Restores just this node -- descendants are not auto-restored, since some
    may have been independently deleted before this node was. Restoring a
    subtree is a deliberate follow-up (call list_descendants + restore_node
    on the ones that should come back).
    """
    node = get_node(session, node_id, include_deleted=True)
    if node is None:
        raise ValueError(f"no such node: {node_id}")
    node.deleted_at = None
    session.flush()
    return node


def get_content(session: Session, node_id: uuid.UUID) -> str | None:
    """
    Returns the node's text content. A future blob-backed path would check
    `blob_key` here and fetch from object storage when `content` is None
    but a blob reference is set.
    """
    node = get_node(session, node_id)
    return node.content if node is not None else None


def query_metadata(
    session: Session,
    *,
    tags: list[str] | None = None,
    status: str | None = None,
    kind: str | None = None,
    parent_id: uuid.UUID | None = None,
    include_deleted: bool = False,
) -> list[File]:
    stmt = select(File)
    if tags:
        stmt = stmt.where(File.tags.contains(tags))
    if status is not None:
        stmt = stmt.where(File.status == status)
    if kind is not None:
        stmt = stmt.where(File.kind == kind)
    if parent_id is not None:
        stmt = stmt.where(File.parent_id == parent_id)
    if not include_deleted:
        stmt = stmt.where(File.deleted_at.is_(None))
    return list(session.scalars(stmt))


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------


def create_manifest(session: Session, name: str, description: str | None = None) -> Manifest:
    manifest = Manifest(name=name, description=description)
    session.add(manifest)
    session.flush()
    return manifest


def get_manifest(
    session: Session, manifest_id: uuid.UUID, *, include_deleted: bool = False
) -> Manifest | None:
    manifest = session.get(Manifest, manifest_id)
    if manifest is None:
        return None
    if not include_deleted and manifest.deleted_at is not None:
        return None
    return manifest


def list_manifests(session: Session, *, include_deleted: bool = False) -> list[Manifest]:
    stmt = select(Manifest)
    if not include_deleted:
        stmt = stmt.where(Manifest.deleted_at.is_(None))
    return list(session.scalars(stmt))


def _manifest_transitively_contains(
    session: Session, manifest_id: uuid.UUID, target_id: uuid.UUID
) -> bool:
    """Is `target_id` reachable by walking nested-manifest members starting at `manifest_id`?"""
    stack = [manifest_id]
    seen: set[uuid.UUID] = set()
    while stack:
        current = stack.pop()
        if current == target_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        nested_ids = session.scalars(
            select(ManifestMember.child_manifest_id).where(
                ManifestMember.manifest_id == current,
                ManifestMember.child_manifest_id.is_not(None),
            )
        )
        stack.extend(nested_ids)
    return False


def add_manifest_member(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    file_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
) -> ManifestMember:
    if (file_id is None) == (child_manifest_id is None):
        raise ValueError("exactly one of file_id or child_manifest_id must be set")

    if child_manifest_id is not None:
        # Adding child_manifest_id as a member of manifest_id would create a
        # cycle if manifest_id is already reachable from child_manifest_id.
        if _manifest_transitively_contains(session, child_manifest_id, manifest_id):
            raise ManifestCycleError(
                f"adding manifest {child_manifest_id} to {manifest_id} would create a cycle"
            )

    member = ManifestMember(
        manifest_id=manifest_id, file_id=file_id, child_manifest_id=child_manifest_id
    )
    session.add(member)
    session.flush()
    return member


def remove_manifest_member(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    file_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
) -> None:
    stmt = select(ManifestMember).where(ManifestMember.manifest_id == manifest_id)
    if file_id is not None:
        stmt = stmt.where(ManifestMember.file_id == file_id)
    if child_manifest_id is not None:
        stmt = stmt.where(ManifestMember.child_manifest_id == child_manifest_id)
    for member in session.scalars(stmt):
        session.delete(member)
    session.flush()


def resolve_manifest(
    session: Session, manifest_id: uuid.UUID, _visited: set[uuid.UUID] | None = None
) -> set[File]:
    """
    Dynamically resolves a manifest to the concrete set of file/folder rows
    it currently expands to: explicit file members, every descendant of any
    directory member (so newly added children are picked up automatically),
    and everything nested manifests resolve to.
    """
    visited = _visited if _visited is not None else set()
    if manifest_id in visited:
        # Defensive guard -- add_manifest_member already prevents write-time
        # cycles, this just avoids infinite recursion if that's ever bypassed.
        return set()
    visited.add(manifest_id)

    result: set[File] = set()
    members = session.scalars(
        select(ManifestMember).where(ManifestMember.manifest_id == manifest_id)
    )
    for member in members:
        if member.file_id is not None:
            node = get_node(session, member.file_id)
            if node is None:
                continue
            result.add(node)
            if node.kind == "folder":
                result.update(list_descendants(session, node.id))
        else:
            result.update(resolve_manifest(session, member.child_manifest_id, visited))

    return result
