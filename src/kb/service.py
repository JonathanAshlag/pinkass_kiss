"""
KB service layer: the single chokepoint every consumer (REST API, agents, ingestion
jobs, future services) goes through instead of calling kb.storage.dal directly.

Mostly a thin passthrough to kb.storage.dal, plus three pieces of real logic:
`get_warnings` composes kb.okf's advisory checks into a single report for a node;
every mutation runs the kb.policy permission check (folder/file kinds, agent lock) for
its `actor` (default "human"; there is no authn yet) before delegating; and every
mutation that changes indexed content records the node ids it touched on the session,
so that `commit` can bring the semantic index in step afterwards. Concurrency checks
and real validation hooks are still deferred (see CLAUDE.md).

All functions take an explicit SQLAlchemy `Session`, same convention as kb.storage.dal --
session lifecycle (opening, closing) is the caller's job, not this module's. Commit
through `commit(session)` rather than `session.commit()` when the writes should reach
the semantic index: a plain commit (or any rollback) just drops the recorded ids.
"""

import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from kb import okf
from kb.policy import Action, Actor, PermissionDenied, check

from kb.retrieval import dci

from kb.storage import dal
from kb.storage.models import File, Folder, Manifest, ManifestMember, Node

# Re-exported as-is: dal.py's/dci.py's/policy.py's own exceptions are this layer's too.
ManifestCycleError = dal.ManifestCycleError
TreeCycleError = dal.TreeCycleError
FieldError = dal.FieldError
PatternError = dci.PatternError
PermissionDenied = PermissionDenied

log = logging.getLogger(__name__)


def get_warnings(session: Session, node: Node) -> list[str]:
    """
    Composes kb.okf's advisory checks into one report for `node` (always [] for a
    folder -- they're all about file content/frontmatter). Never raises -- every
    check it calls is itself advisory-only.
    """
    if not isinstance(node, File):
        return []
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
# Commit + index sync: the write seam
# --------------------------------------------------------------------------
#
# Mutations below record the ids they touched in `session.info[_TOUCHED]`; `commit`
# commits, then hands exactly those ids to the indexer. The set lives as long as the
# session's outermost transaction: a commit or rollback of it clears the set (event
# below), so a rolled-back write is never indexed. A rolled-back SAVEPOINT keeps its
# ids -- over-indexing is harmless (index_files unindexes deleted/missing ids).

_TOUCHED = "kb.touched_node_ids"


def _touch(session: Session, *node_ids: uuid.UUID) -> None:
    session.info.setdefault(_TOUCHED, {}).update(dict.fromkeys(node_ids))  # ordered set


@event.listens_for(Session, "after_transaction_end")
def _clear_touched(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(_TOUCHED, None)


def touched_ids(session: Session) -> list[uuid.UUID]:
    """Node ids the session's uncommitted writes touched, in first-touch order."""
    return list(session.info.get(_TOUCHED, ()))


def auto_index_enabled() -> bool:
    """KB_AUTO_INDEX (default on): set to 0/false/off to skip indexing after writes,
    e.g. in dev without an embeddings server. Read on every commit so tests can flip it."""
    return os.environ.get("KB_AUTO_INDEX", "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass
class CommitResult:
    touched: list[uuid.UUID] = field(default_factory=list)
    # The IndexResult when indexing ran synchronously and didn't blow up as a whole;
    # None when it was skipped, scheduled, or failed (failures are logged).
    indexed: object | None = None


def commit(
    session: Session,
    *,
    index: bool | None = None,
    schedule: Callable[..., None] | None = None,
) -> CommitResult:
    """Commit the session, then bring the semantic index in step with what it wrote.

    `index`: None follows KB_AUTO_INDEX, True/False force it. Indexing runs strictly
    after the commit (the indexer opens its own session and must see the new state):
    synchronously, or as `schedule(fn, ids)` when given (the API passes
    `BackgroundTasks.add_task`, keeping it out of the request and the event loop). The
    index is derived state, so indexing failures are logged, never raised -- the writes
    already succeeded, and `reindex_all` / `POST /index/reindex` repair it. If the
    commit itself raises, nothing is indexed (the rollback clears the recorded ids).
    """
    ids = touched_ids(session)
    session.commit()
    result = CommitResult(touched=ids)
    if not ids or not (auto_index_enabled() if index is None else index):
        return result
    if schedule is not None:
        schedule(_index_logged, ids)
    else:
        result.indexed = _index_logged(ids)
    return result


def _index_logged(node_ids: list[uuid.UUID]):
    """index_files, with every failure logged instead of raised."""
    try:
        result = index_files(node_ids)
    except Exception:
        log.exception("indexing failed for %d node(s)", len(node_ids))
        return None
    for file_id, error in getattr(result, "failed", None) or []:
        log.warning("indexing %s failed: %s", file_id, error)
    return result


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


def get_node(session: Session, node_id: uuid.UUID, *, include_deleted: bool = False) -> Node | None:
    return dal.get_node(session, node_id, include_deleted=include_deleted)


def get_file(session: Session, file_id: uuid.UUID, *, include_deleted: bool = False) -> File | None:
    return dal.get_file(session, file_id, include_deleted=include_deleted)


def get_folder(
    session: Session, folder_id: uuid.UUID, *, include_deleted: bool = False
) -> Folder | None:
    return dal.get_folder(session, folder_id, include_deleted=include_deleted)


def list_children(
    session: Session, parent_id: uuid.UUID | None, *, include_deleted: bool = False
) -> list[Node]:
    return dal.list_children(session, parent_id, include_deleted=include_deleted)


def list_descendants(
    session: Session, node_id: uuid.UUID, *, include_deleted: bool = False
) -> list[Node]:
    return dal.list_descendants(session, node_id, include_deleted=include_deleted)


# --- permission checks (kb.policy) -------------------------------------------


def _check(session: Session, actor: Actor, action: Action, node: Node) -> None:
    check(actor, action, node, dal.list_ancestors(session, node))


def _existing(session: Session, node_id: uuid.UUID) -> Node:
    node = dal.get_node(session, node_id, include_deleted=True)
    if node is None:
        raise ValueError(f"no such node: {node_id}")
    return node


def _reject_auto_updated(kind: str | None) -> None:
    if kind == "auto_updated":
        raise PermissionDenied("auto_updated is reserved for auto-update jobs (not in this version)")


def _check_create(
    session: Session, actor: Actor, parent_id: uuid.UUID | None, columns: dict
) -> None:
    """Creating a node needs CREATE_INSIDE on its parent folder; choosing a non-default
    kind or agent lock up front counts as changing them."""
    _reject_auto_updated(columns.get("kind"))
    parent = dal.get_folder(session, parent_id) if parent_id is not None else None
    if parent is None:
        return  # a root folder, or a bad parent_id that dal reports
    _check(session, actor, Action.CREATE_INSIDE, parent)
    if columns.get("kind", "manual") != "manual":
        _check(session, actor, Action.SET_KIND, parent)
    if columns.get("agent_locked"):
        _check(session, actor, Action.SET_AGENT_LOCK, parent)


def create_file(
    session: Session,
    *,
    parent_id: uuid.UUID,
    title: str,
    content: str,
    actor: Actor = "human",
    **other_columns,
) -> File:
    _check_create(session, actor, parent_id, other_columns)
    node = dal.create_file(
        session, parent_id=parent_id, title=title, content=content, **other_columns
    )
    _touch(session, node.id)
    return node


def create_folder(
    session: Session,
    *,
    parent_id: uuid.UUID | None,
    title: str,
    actor: Actor = "human",
    **other_columns,
) -> Folder:
    _check_create(session, actor, parent_id, other_columns)
    node = dal.create_folder(session, parent_id=parent_id, title=title, **other_columns)
    _touch(session, node.id)  # folders are never indexed; recorded so callers needn't care
    return node


_FIELD_ACTIONS = {"title": Action.RENAME, "kind": Action.SET_KIND, "agent_locked": Action.SET_AGENT_LOCK}


def update_node(session: Session, node_id: uuid.UUID, *, actor: Actor = "human", **fields) -> Node:
    """Each field that actually changes needs its action: title -> RENAME, kind ->
    SET_KIND, agent_locked -> SET_AGENT_LOCK, anything else -> EDIT. Checked against the
    node's current state, so unlocking a skeleton and renaming it are two updates."""
    node = _existing(session, node_id)
    _reject_auto_updated(fields.get("kind"))
    changed = [key for key, value in fields.items() if getattr(node, key, None) != value]
    for action in dict.fromkeys(_FIELD_ACTIONS.get(key, Action.EDIT) for key in changed):
        _check(session, actor, action, node)
    node = dal.update_node(session, node_id, **fields)
    _touch(session, node_id)
    return node


def move_node(
    session: Session,
    node_id: uuid.UUID,
    new_parent_id: uuid.UUID | None,
    *,
    actor: Actor = "human",
) -> Node:
    """Needs MOVE on the node and CREATE_INSIDE on the destination folder.

    Records nothing for the index: chunks don't store paths (dci renders them at read
    time), so a move leaves every chunk valid. Revisit if chunks ever embed the path."""
    node = _existing(session, node_id)
    if node.parent_id == new_parent_id:
        return node
    _check(session, actor, Action.MOVE, node)
    destination = dal.get_folder(session, new_parent_id) if new_parent_id is not None else None
    if destination is not None:
        _check(session, actor, Action.CREATE_INSIDE, destination)
    return dal.move_node(session, node_id, new_parent_id)


def delete_node(
    session: Session, node_id: uuid.UUID, *, cascade: bool = True, actor: Actor = "human"
) -> None:
    """Needs DELETE on the node itself only (a skeleton's lock doesn't reach up: deleting
    a manual folder cascades through any skeletons inside it).

    Records the node plus, with `cascade`, exactly the descendants the cascade
    soft-deletes (dal.delete_node only touches still-active ones), so their chunks go."""
    _check(session, actor, Action.DELETE, _existing(session, node_id))
    touched = [node_id]
    if cascade:
        touched += [d.id for d in dal.list_descendants(session, node_id)]
    dal.delete_node(session, node_id, cascade=cascade)
    _touch(session, *touched)


def restore_node(session: Session, node_id: uuid.UUID, *, actor: Actor = "human") -> Node:
    """Needs CREATE_INSIDE on the parent folder (restoring is putting the node back).
    The parent must be active: restoring into a deleted folder would leave an active
    node nobody can reach (and used to skip the permission check), so restore the
    parent first -- FieldError otherwise."""
    node = _existing(session, node_id)
    if node.parent_id is not None:
        parent = dal.get_folder(session, node.parent_id, include_deleted=True)
        if parent is not None and parent.deleted_at is not None:
            raise FieldError(f"cannot restore {node.title!r}: its folder {parent.title!r} is deleted (restore it first)")
        if parent is not None:
            _check(session, actor, Action.CREATE_INSIDE, parent)
    node = dal.restore_node(session, node_id)
    _touch(session, node_id)  # single-node restore, so only this node comes back
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
    return dal.query_metadata(
        session,
        tags=tags,
        status=status,
        node_type=node_type,
        kind=kind,
        parent_id=parent_id,
        include_deleted=include_deleted,
    )


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------


# Manifest ops record nothing for the index: chunks carry no manifest membership
# (semantic_search scopes by manifest at query time).


def create_manifest(session: Session, name: str, description: str | None = None) -> Manifest:
    return dal.create_manifest(session, name, description)


def get_manifest(
    session: Session, manifest_id: uuid.UUID, *, include_deleted: bool = False
) -> Manifest | None:
    return dal.get_manifest(session, manifest_id, include_deleted=include_deleted)


def list_manifests(session: Session, *, include_deleted: bool = False) -> list[Manifest]:
    return dal.list_manifests(session, include_deleted=include_deleted)


def _member_target(session: Session, node_id: uuid.UUID | None) -> dict:
    """A node id as the dal's file_id=/folder_id= keyword."""
    if node_id is None:
        return {}
    node = _existing(session, node_id)
    return {"folder_id" if isinstance(node, Folder) else "file_id": node_id}


def add_manifest_member(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
) -> ManifestMember:
    """`node_id` is a file (just that file) or a folder (its whole subtree)."""
    if (node_id is None) == (child_manifest_id is None):
        raise ValueError("exactly one of node_id or child_manifest_id must be set")
    return dal.add_manifest_member(
        session, manifest_id, child_manifest_id=child_manifest_id, **_member_target(session, node_id)
    )


def remove_manifest_member(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None = None,
    child_manifest_id: uuid.UUID | None = None,
) -> None:
    dal.remove_manifest_member(
        session, manifest_id, child_manifest_id=child_manifest_id, **_member_target(session, node_id)
    )


def list_manifest_members(session: Session, manifest_id: uuid.UUID) -> list[ManifestMember]:
    return dal.list_manifest_members(session, manifest_id)


def resolve_manifest(session: Session, manifest_id: uuid.UUID) -> set[Node]:
    return dal.resolve_manifest(session, manifest_id)


# --------------------------------------------------------------------------
# Direct corpus interaction (agent-facing ls/grep/read, see kb.retrieval.dci)
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
# Raw originals (kb.storage.blobs): the source bytes of converted uploads (PDF, ...)
# --------------------------------------------------------------------------


def get_original(session: Session, node_id: uuid.UUID) -> tuple[bytes, str, str] | None:
    """(data, mime, filename) of a node's retained original, or None if it has none
    (markdown nodes, or no blob store configured). Raises ValueError for no such node."""
    from kb.storage import blobs

    node = dal.get_node(session, node_id)
    if node is None:
        raise ValueError(f"no such node: {node_id}")
    if not isinstance(node, File):
        return None
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
# Semantic index (derived kb_chunks, see kb.semantic_index). Imported lazily so that importing
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
    """-> list[kb.retrieval.semantic.SearchHit], best first."""
    from kb.retrieval import semantic

    return semantic.semantic_search(
        session, manifest_id, query, k=k, tags=tags, status=status, store=store
    )


# Unlike the functions above, the indexing functions take no Session: they open their
# own (after the caller's writes are committed), since the index is derived state.


def index_files(file_ids, *, store=None, session_factory=None):
    """(Re)index the given node ids; deleted/missing ones and folders get unindexed.
    -> kb.semantic_index.indexer.IndexResult. Never raises per file."""
    from kb.semantic_index import indexer

    return indexer.index_files(file_ids, store=store, session_factory=session_factory)


def unindex_files(file_ids, *, store=None) -> int:
    from kb.semantic_index import indexer

    return indexer.unindex_files(file_ids, store=store)


def reindex_all(*, store=None, session_factory=None):
    from kb.semantic_index import indexer

    return indexer.reindex_all(store=store, session_factory=session_factory)
