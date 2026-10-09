"""
KB service layer: the single chokepoint every consumer (REST API, agents, ingestion
jobs, future services) goes through instead of calling kb.storage.dal directly.

Mostly a thin passthrough to kb.storage.dal, plus three pieces of real logic:
`get_warnings` composes kb.okf's advisory checks into a single report for a node;
every mutation runs the kb.policy permission check (folder/file kinds, agent lock) for
its `actor` (default "human"; there is no authn yet) before delegating; and every
mutation records the node ids it touched on the session, so that every commit is
all-or-nothing across the DB, the semantic index and S3 (see "Commit" below).
Concurrency checks and real validation hooks are still deferred (see CLAUDE.md).

All functions take an explicit SQLAlchemy `Session`, same convention as kb.storage.dal --
session lifecycle (opening, closing) is the caller's job, not this module's.
"""

import logging
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session, SessionTransaction, undefer

from kb import okf
from kb.policy import Action, Actor, PermissionDenied, check
from kb.settings import get_settings

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
# Commit: the all-or-nothing write seam (DB rows + semantic index + S3 originals)
# --------------------------------------------------------------------------
#
# A write lands in all three stores or in none. The index and S3 can't join the DB
# transaction (LangChain's PGVectorStore / SQLRecordManager commit on their own
# connections), so there is one commit point instead: the DB commit. Everything else is
# written *before* it, where nothing can reach it yet -- search scopes to committed file
# ids, and only a committed row points at an S3 object -- and is settled after it:
#
#   stage(...)        ingest, before its transaction: embed + index the new files
#                     (add-only), upload their originals (parallel)
#   before_commit     every commit: stage the files its writes touched (add-only;
#                     chunks staged earlier are skipped, never re-embedded). Raises
#                     IndexingError on failure, so the commit doesn't happen
#   COMMIT            the commit point
#   transaction end   commit   -> reconcile edited/deleted ids (drops stale chunks)
#                     rollback -> reconcile every staged id + delete uploaded originals
#                                 no committed row references (the undo)
#
# Settling is state-based (make the index / bucket match whatever the DB holds), so a
# connection lost *during* COMMIT -- when it's unknown whether it happened -- is settled
# correctly either way. Settling never raises (failures are logged); what it can't
# undo is unreachable anyway, and scripts/gc.py sweeps it after a crash.
#
# Mutations record the ids they touch in `session.info[_TOUCHED]` (and creations in
# `_CREATED`). These live as long as the session's outermost transaction. A rolled-back
# SAVEPOINT keeps its ids -- over-staging is harmless.

_TOUCHED = "kb.touched_node_ids"
_CREATED = "kb.created_node_ids"
_STAGED = "kb.staged"  # staging ran (or started): the transaction end must settle
_UPLOADED = "kb.uploaded_originals"  # {key: BlobStore} uploaded by this transaction
_COMMITTED = "kb.committed"
_LAST_STAGE = "kb.last_stage_result"  # outlives the transaction; read by commit()



class IndexingError(Exception):
    """Embedding/indexing a write's files failed, so the write was not committed. Not a
    ValueError: the API maps it to 503 (the embeddings server is a dependency)."""


def _touch(session: Session, *node_ids: uuid.UUID, created: bool = False) -> None:
    session.info.setdefault(_TOUCHED, {}).update(dict.fromkeys(node_ids))  # ordered set
    if created:
        session.info.setdefault(_CREATED, set()).update(node_ids)
    else:  # edited/deleted after its creation: may have stale staged chunks, so reconcile it
        session.info.get(_CREATED, set()).difference_update(node_ids)


def touched_ids(session: Session) -> list[uuid.UUID]:
    """Node ids the session's uncommitted writes touched, in first-touch order."""
    return list(session.info.get(_TOUCHED, ()))


def _stage_index(session: Session, files: list[File]):
    """Add-only indexing of `files` (committed or not); IndexingError on failure."""
    from kb.semantic_index.chunking import node_document
    from kb.semantic_index.indexer import stage_documents

    session.info[_STAGED] = True  # even a half-done staging must be settled
    try:
        return stage_documents([node_document(f) for f in files])
    except Exception as exc:
        raise IndexingError(f"indexing failed: {type(exc).__name__}: {exc}") from exc


def _upload_originals(session: Session, uploads: list[tuple[Any, str, Any]]) -> None:
    """Uploads (store, key, Original)s in parallel, recording every key that made it
    (for the undo) before re-raising the first failure."""
    if not uploads:
        return
    uploaded = session.info.setdefault(_UPLOADED, {})
    errors: list[BaseException] = []

    def put(upload) -> None:
        store, key, original = upload
        try:
            store.put_original(key, original)
            uploaded[key] = store
        except BaseException as exc:  # noqa: BLE001 -- collected, re-raised below
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=min(get_settings().blob_upload_workers, len(uploads))) as pool:
        list(pool.map(put, uploads))
    if errors:
        raise errors[0]


def stage(
    session: Session,
    *,
    files: list[File] = (),
    originals: list[tuple[Any, str, Any]] = (),
) -> None:
    """Pre-commit work for a bulk write (ingest), done before its DB transaction does
    anything, so the transaction stays short: index `files` -- transient `File`s with
    their final ids and fields -- and upload `originals` ((BlobStore, key, Original)).
    Cheapest-to-undo first: indexing, then S3. On failure, it undoes what it did (the
    rest of the transaction is left to the caller) and raises IndexingError /
    BlobStoreError; on success, the session's transaction settles it (commit keeps it,
    rollback undoes it)."""
    session.connection()  # begin the transaction whose end will settle this
    files = list(files)
    ids = [f.id for f in files]
    before = set(session.info.get(_UPLOADED, {}))
    try:
        if files:
            _touch(session, *ids, created=True)  # first: a half-done staging is undone by id
            _stage_index(session, files)
        _upload_originals(session, list(originals))
    except BaseException:
        mine = {k: s for k, s in session.info.get(_UPLOADED, {}).items() if k not in before}
        for key in mine:
            session.info[_UPLOADED].pop(key)
        _settle(ids, set(), mine, committed=False)
        raise


@event.listens_for(Session, "before_commit")
def _stage_touched(session: Session) -> None:
    ids = touched_ids(session)
    if not ids:
        return
    session.flush()
    files = list(
        session.scalars(
            select(File)
            .where(File.id.in_(ids), File.deleted_at.is_(None))
            .options(undefer(File.content))
        )
    )
    session.info[_LAST_STAGE] = _stage_index(session, files)


@event.listens_for(Session, "after_commit")
def _mark_committed(session: Session) -> None:
    session.info[_COMMITTED] = True


@event.listens_for(Session, "after_transaction_end")
def _end_transaction(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is not None:
        return
    ids = touched_ids(session)
    created = session.info.pop(_CREATED, set())
    staged = session.info.pop(_STAGED, False)
    uploaded = session.info.pop(_UPLOADED, {})
    committed = session.info.pop(_COMMITTED, False)
    session.info.pop(_TOUCHED, None)
    if staged or uploaded:
        _settle(ids, created, uploaded, committed)


def _settle(ids: list[uuid.UUID], created: set, uploaded: dict, committed: bool) -> None:
    """After the commit: drop stale chunks of edited/deleted nodes (created ones were
    staged exactly). After a rollback (or a COMMIT with unknown outcome): make the index
    match the DB again for every staged id, and delete the uploaded originals no
    committed row references. Never raises."""
    reconcile_ids = [i for i in ids if i not in created] if committed else ids
    if reconcile_ids:
        try:
            from kb.semantic_index.indexer import reconcile

            result = reconcile(reconcile_ids)
            for file_id, error in result.failed:
                log.warning("reconciling the index for %s failed: %s", file_id, error)
        except Exception:
            log.exception("reconciling the index for %d node(s) failed", len(reconcile_ids))
    if uploaded and not committed:
        try:
            _delete_unreferenced(uploaded)
        except Exception:
            log.exception("deleting %d uploaded original(s) failed", len(uploaded))


def _delete_unreferenced(uploaded: dict) -> None:
    from kb.storage.db import SessionLocal

    with SessionLocal() as s:
        kept = set(s.scalars(select(File.blob_key).where(File.blob_key.in_(list(uploaded)))))
    by_store: dict[int, tuple[Any, list[str]]] = {}
    for key, store in uploaded.items():
        if key not in kept:
            by_store.setdefault(id(store), (store, []))[1].append(key)
    for store, keys in by_store.values():
        store.delete_originals(keys)


@dataclass
class CommitResult:
    touched: list[uuid.UUID] = field(default_factory=list)
    # The staging IndexResult (chunks added / skipped before the commit), or None when
    # the commit touched nothing.
    indexed: object | None = None


def commit(session: Session) -> CommitResult:
    """Commit the session -- all or nothing across the DB, the semantic index and S3
    (see above). A plain `session.commit()` is just as atomic (the staging is an event);
    this adds the rollback on failure and reports what was touched and indexed. Raises
    IndexingError (nothing committed) if embedding/indexing failed."""
    ids = touched_ids(session)
    session.info.pop(_LAST_STAGE, None)
    try:
        session.commit()
    except BaseException:
        session.rollback()
        raise
    return CommitResult(touched=ids, indexed=session.info.pop(_LAST_STAGE, None))


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
    parent = dal.get_folder(session, parent_id, include_deleted=True) if parent_id is not None else None
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
    _touch(session, node.id, created=True)
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
    _touch(session, node.id, created=True)  # folders are never indexed; recorded so callers needn't care
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
    _touch(session, node.id)
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
    destination = dal.get_folder(session, new_parent_id, include_deleted=True) if new_parent_id is not None else None
    if destination is not None:
        _check(session, actor, Action.CREATE_INSIDE, destination)
    return dal.move_node(session, node_id, new_parent_id)


def delete_node(session: Session, node_id: uuid.UUID, *, actor: Actor = "human") -> None:
    """Needs DELETE on the node itself only (a skeleton's lock doesn't reach up: deleting
    a manual folder cascades through any skeletons inside it).

    Records the node plus exactly the descendants the cascade soft-deletes
    (dal.delete_node only touches still-active ones), so their chunks go."""
    _check(session, actor, Action.DELETE, _existing(session, node_id))
    touched = [node_id, *(d.id for d in dal.list_descendants(session, node_id))]
    dal.delete_node(session, node_id)
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
    if (node_id is None) == (child_manifest_id is None):  # else the DAL query has no filter and deletes every member (#31)
        raise FieldError("give exactly one of node_id / child_manifest_id")
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


def get_original(session: Session, node_id: uuid.UUID) -> tuple[Any, str, str] | None:
    """(stream, mime, filename) of a node's retained original (stream as returned by
    `BlobStore.get_original`; the caller closes it), or None if it has none
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
    body = store.get_original(node.blob_key)
    if body is None:
        return None
    resource = next((s.get("resource") for s in node.sources if isinstance(s, dict)), None)
    filename = resource.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if resource else node.title
    if resource and resource.startswith("file:"):
        filename = urllib.parse.unquote(filename)  # file URIs percent-encode non-ASCII names
    return body, node.blob_mime_type or "application/octet-stream", filename


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
# own and see committed state only. Writes never need them (every commit indexes, see
# "Commit"); they're the repair tools (POST /index/reindex, scripts/reindex.py).


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
