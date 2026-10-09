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

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session, aliased

from kb.storage.models import File, FileKind, Folder, FolderKind, Manifest, ManifestMember, Node


class ManifestCycleError(ValueError):
    """Raised when adding a nested manifest member would create a cycle."""


class TreeCycleError(ValueError):
    """Raised when moving a folder would place it under its own subtree."""


# Serializes folder moves: two concurrent moves (A under B, B under A) would each pass the
# cycle check. Held until commit, so never move a folder inside a long transaction.
_FOLDER_MOVE_LOCK = 4242


class FieldError(ValueError):
    """Raised when an update names a field the node's type doesn't have, or a file is
    placed outside a folder."""


# --------------------------------------------------------------------------
# Nodes: folders (tree containers) and files (always inside a folder). Ids are
# unique across both tables, so the generic functions take a bare node id.
# --------------------------------------------------------------------------


def _active(node, include_deleted: bool):
    if node is None or (not include_deleted and node.deleted_at is not None):
        return None
    return node


def get_node(session: Session, node_id: uuid.UUID, *, include_deleted: bool = False) -> Node | None:
    node = session.get(File, node_id) or session.get(Folder, node_id)
    return _active(node, include_deleted)


def get_file(session: Session, file_id: uuid.UUID, *, include_deleted: bool = False) -> File | None:
    return _active(session.get(File, file_id), include_deleted)


def get_folder(
    session: Session, folder_id: uuid.UUID, *, include_deleted: bool = False
) -> Folder | None:
    return _active(session.get(Folder, folder_id), include_deleted)


def list_ancestors(session: Session, node: Node) -> list[Folder]:
    """The folders above `node`, nearest first (deleted ones included)."""
    ancestors: list[Folder] = []
    parent_id = node.parent_id
    while parent_id is not None:
        parent = session.get(Folder, parent_id)
        if parent is None:
            break
        ancestors.append(parent)
        parent_id = parent.parent_id
    return ancestors


def list_children(
    session: Session,
    parent_id: uuid.UUID | None,
    *,
    include_deleted: bool = False,
) -> list[Node]:
    """Sub-folders, then files. `parent_id=None` lists the root folders (files are
    never at the root)."""
    folders = select(Folder).where(Folder.parent_id == parent_id)
    if not include_deleted:
        folders = folders.where(Folder.deleted_at.is_(None))
    children: list[Node] = list(session.scalars(folders))
    if parent_id is not None:
        files = select(File).where(File.parent_id == parent_id)
        if not include_deleted:
            files = files.where(File.deleted_at.is_(None))
        children.extend(session.scalars(files))
    return children


def _descendant_folder_ids(node_id: uuid.UUID):
    """Recursive CTE of the ids of every folder transitively under `node_id`."""
    base = select(Folder.id).where(Folder.parent_id == node_id).cte("descendants", recursive=True)
    child = aliased(Folder)
    return base.union_all(select(child.id).where(child.parent_id == base.c.id))


def list_descendants(
    session: Session, node_id: uuid.UUID, *, include_deleted: bool = False
) -> list[Node]:
    """All nodes transitively under `node_id` (not including `node_id` itself):
    folders, then files. A file has none."""
    if session.get(Folder, node_id) is None:
        return []
    folder_ids = select(_descendant_folder_ids(node_id).c.id)
    folders = select(Folder).where(Folder.id.in_(folder_ids))
    files = select(File).where(
        or_(File.parent_id == node_id, File.parent_id.in_(select(_descendant_folder_ids(node_id).c.id)))
    )
    if not include_deleted:
        folders = folders.where(Folder.deleted_at.is_(None))
        files = files.where(File.deleted_at.is_(None))
    return [*session.scalars(folders), *session.scalars(files)]


def _require_folder(session: Session, folder_id: uuid.UUID) -> Folder:
    folder = session.get(Folder, folder_id)
    if folder is None:
        if session.get(File, folder_id) is not None:
            raise FieldError(f"not a folder: {folder_id}")
        raise ValueError(f"no such folder: {folder_id}")
    if folder.deleted_at is not None:
        # an active node under a deleted folder is unreachable (list_children, manifests, search)
        raise FieldError(f"folder {folder.title!r} is deleted (restore it first)")
    return folder


def create_file(
    session: Session,
    *,
    parent_id: uuid.UUID,
    title: str,
    content: str,
    **other_columns,
) -> File:
    if parent_id is None:
        raise FieldError("a file must be inside a folder")
    _require_folder(session, parent_id)
    node = File(parent_id=parent_id, title=title, content=content, **other_columns)
    session.add(node)
    session.flush()
    return node


def create_folder(
    session: Session,
    *,
    parent_id: uuid.UUID | None,
    title: str,
    **other_columns,
) -> Folder:
    if parent_id is not None:
        _require_folder(session, parent_id)
    node = Folder(parent_id=parent_id, title=title, **other_columns)
    session.add(node)
    session.flush()
    return node


def update_node(session: Session, node_id: uuid.UUID, **fields) -> Node:
    node = get_node(session, node_id, include_deleted=True)
    if node is None:
        raise ValueError(f"no such node: {node_id}")
    columns = type(node).__table__.columns.keys()
    for key in fields:
        if key not in columns:
            raise FieldError(f"a {type(node).__name__.lower()} has no field {key!r}")
    for key, value in fields.items():
        setattr(node, key, value)
    session.flush()
    return node


def move_node(session: Session, node_id: uuid.UUID, new_parent_id: uuid.UUID | None) -> Node:
    node = get_node(session, node_id, include_deleted=True)
    if node is None:
        raise ValueError(f"no such node: {node_id}")
    if new_parent_id == node_id:
        raise TreeCycleError("a node cannot be its own parent")
    if new_parent_id is None:
        if isinstance(node, File):
            raise FieldError("a file must be inside a folder")
    else:
        _require_folder(session, new_parent_id)
        if isinstance(node, Folder):
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _FOLDER_MOVE_LOCK})
            descendant_ids = {d.id for d in list_descendants(session, node_id, include_deleted=True)}
            if new_parent_id in descendant_ids:
                raise TreeCycleError("cannot move a folder under its own descendant")

    node.parent_id = new_parent_id
    session.flush()
    return node


def delete_node(session: Session, node_id: uuid.UUID) -> None:
    node = get_node(session, node_id, include_deleted=True)
    if node is None:
        raise ValueError(f"no such node: {node_id}")

    now = datetime.now(timezone.utc)
    node.deleted_at = now

    for descendant in list_descendants(session, node_id, include_deleted=False):
        descendant.deleted_at = now

    session.flush()


def restore_node(session: Session, node_id: uuid.UUID) -> Node:
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


def query_metadata(
    session: Session,
    *,
    tags: list[str] | None = None,
    status: str | None = None,
    node_type: str | None = None,
    kind: str | None = None,
    parent_id: uuid.UUID | None = None,
    include_deleted: bool = False,
) -> list[Node]:
    """Folders, then files, matching every given filter. `node_type` is "file" or
    "folder"; `kind` is the file/folder kind. Folders have no status, so a `status`
    filter (or a `kind` only files have) yields files only."""
    results: list[Node] = []
    for model, kinds, type_name in ((Folder, FolderKind.enums, "folder"), (File, FileKind.enums, "file")):
        if node_type is not None and node_type != type_name:
            continue
        if status is not None and model is Folder:
            continue
        if kind is not None and kind not in kinds:
            continue
        stmt = select(model)
        if tags:
            stmt = stmt.where(model.tags.contains(tags))
        if status is not None:
            stmt = stmt.where(File.status == status)
        if kind is not None:
            stmt = stmt.where(model.kind == kind)
        if parent_id is not None:
            stmt = stmt.where(model.parent_id == parent_id)
        if not include_deleted:
            stmt = stmt.where(model.deleted_at.is_(None))
        results.extend(session.scalars(stmt))
    return results


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
    folder_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
) -> ManifestMember:
    if sum(x is not None for x in (file_id, folder_id, child_manifest_id)) != 1:
        raise ValueError("exactly one of file_id, folder_id or child_manifest_id must be set")

    if child_manifest_id is not None:
        # Adding child_manifest_id as a member of manifest_id would create a
        # cycle if manifest_id is already reachable from child_manifest_id.
        if _manifest_transitively_contains(session, child_manifest_id, manifest_id):
            raise ManifestCycleError(
                f"adding manifest {child_manifest_id} to {manifest_id} would create a cycle"
            )

    member = ManifestMember(
        manifest_id=manifest_id,
        file_id=file_id,
        folder_id=folder_id,
        child_manifest_id=child_manifest_id,
    )
    session.add(member)
    session.flush()
    return member


def remove_manifest_member(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    file_id: uuid.UUID | None = None,
    folder_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
) -> None:
    stmt = select(ManifestMember).where(ManifestMember.manifest_id == manifest_id)
    if file_id is not None:
        stmt = stmt.where(ManifestMember.file_id == file_id)
    if folder_id is not None:
        stmt = stmt.where(ManifestMember.folder_id == folder_id)
    if child_manifest_id is not None:
        stmt = stmt.where(ManifestMember.child_manifest_id == child_manifest_id)
    for member in session.scalars(stmt):
        session.delete(member)
    session.flush()


def list_manifest_members(session: Session, manifest_id: uuid.UUID) -> list[ManifestMember]:
    """The manifest's direct membership rows (not expanded -- see resolve_manifest)."""
    return list(
        session.scalars(
            select(ManifestMember)
            .where(ManifestMember.manifest_id == manifest_id)
            .order_by(ManifestMember.created_at)
        )
    )


def resolve_manifest(
    session: Session, manifest_id: uuid.UUID, _visited: set[uuid.UUID] | None = None
) -> set[Node]:
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

    result: set[Node] = set()
    members = list(
        session.scalars(select(ManifestMember).where(ManifestMember.manifest_id == manifest_id))
    )
    file_ids = [m.file_id for m in members if m.file_id is not None]
    if file_ids:
        result.update(
            session.scalars(select(File).where(File.id.in_(file_ids), File.deleted_at.is_(None)))
        )
    for member in members:
        if member.file_id is not None:
            continue
        elif member.folder_id is not None:
            folder = get_folder(session, member.folder_id)
            if folder is not None:
                result.add(folder)
                result.update(list_descendants(session, folder.id))
        else:
            result.update(resolve_manifest(session, member.child_manifest_id, visited))

    return result
