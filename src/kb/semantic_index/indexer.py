"""Keeps the semantic index (`kb_chunks`) in step with the canonical `files` table.

- `stage_documents(docs)` -- add-only indexing of documents built from (possibly
                          uncommitted) nodes: embeds and inserts new chunks, deletes
                          nothing, and *raises* on failure. kb.service runs it before
                          every commit, so a write is indexed before it's visible.
- `reconcile(ids)`     -- make the index hold exactly the committed state of those
                          nodes: `index_files` plus removal of chunk rows the record
                          manager doesn't know about. kb.service's undo after a failed
                          write and its cleanup after a successful one.
- `index_files(ids)`   -- for any ids a write touched: active files are
                          (re)indexed incrementally; deleted / missing ones and folders
                          are unindexed. Never raises for a per-file problem.
- `unindex_files(ids)` -- drop every chunk of those files.
- `reindex_all()`      -- rebuild from every active file, then remove
                          every chunk not (re)confirmed by this run ("full" cleanup).

All but `stage_documents` read the DB through their own sessions (`session_factory`,
default `SessionLocal`), so they see committed state only. They call the
PGVectorStore's sync methods -- don't call them from inside a running event loop (use
a threadpool / sync route).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from langchain_core.indexing import index
from sqlalchemy import text

from kb.semantic_index.chunking import FileNodeLoader, split_documents
from kb.semantic_index.vectorstore import IndexStore, chunk_key_encoder, get_index_store
from kb.settings import get_settings

log = logging.getLogger(__name__)



@dataclass
class IndexResult:
    num_added: int = 0
    num_updated: int = 0
    num_skipped: int = 0
    num_deleted: int = 0
    failed: list[tuple[uuid.UUID, str]] = field(default_factory=list)

    def _add(self, stats: dict) -> None:
        self.num_added += stats.get("num_added", 0)
        self.num_updated += stats.get("num_updated", 0)
        self.num_skipped += stats.get("num_skipped", 0)
        self.num_deleted += stats.get("num_deleted", 0)


def _loader(file_ids, session_factory) -> FileNodeLoader:
    if session_factory is None:
        return FileNodeLoader(file_ids)
    return FileNodeLoader(file_ids, session_factory=session_factory)


def stage_documents(docs, *, store: IndexStore | None = None) -> IndexResult:
    """Add-only indexing of loader-shaped documents (`chunking.node_document`): embeds
    and inserts every chunk the record manager doesn't have yet, deletes nothing.
    Chunks that already exist (unchanged content, or staged earlier in the same write)
    are skipped, never re-embedded. Raises on any failure: the caller aborts the write
    and `reconcile`s."""
    result = IndexResult()
    chunks = split_documents(docs)
    if not chunks:
        return result
    store = store or get_index_store()
    result._add(
        index(
            chunks,
            store.record_manager,
            store.vector_store,
            cleanup=None,
            source_id_key="file_id",  # still recorded as the group_id, for reconcile/unindex
            key_encoder=chunk_key_encoder,
            # No cleanup, so no one-batch-per-file rule (see _index_one): batches span files.
            batch_size=get_settings().stage_batch_size,
        )
    )
    return result


def _index_one(store: IndexStore, file_id: uuid.UUID, docs, result: IndexResult) -> None:
    """(Re)index one file's loader docs, recording a failure instead of raising."""
    try:
        chunks = split_documents(docs)
        if not chunks:  # whitespace-only content: nothing to index, drop what's there
            result.num_deleted += _unindex(store, [file_id])
            return
        result._add(
            index(
                chunks,
                store.record_manager,
                store.vector_store,
                cleanup="incremental",
                source_id_key="file_id",
                key_encoder=chunk_key_encoder,
                # One batch per file: incremental cleanup runs after *each* batch and drops
                # the file's chunks not yet seen, so a file split across batches (default
                # 100) would lose and re-embed its later chunks on every run.
                batch_size=len(chunks),
            )
        )
    except Exception as exc:  # noqa: BLE001 -- per-file isolation is the contract
        log.exception("indexing file %s failed", file_id)
        result.failed.append((file_id, f"{type(exc).__name__}: {exc}"))


def _unindex(store: IndexStore, file_ids: list[uuid.UUID]) -> int:
    if not file_ids:
        return 0
    rm = store.record_manager
    keys = rm.list_keys(group_ids=[str(fid) for fid in file_ids])
    if keys:
        store.vector_store.delete(keys)
        rm.delete_keys(keys)
    return len(keys)


def delete_untracked_chunks(
    file_ids: Iterable[uuid.UUID] | None, *, store: IndexStore | None = None
) -> int:
    """Delete `kb_chunks` rows the record manager has no record of, for these files
    (None: all of them). index() writes the vectors first and the records second, so a
    failure in between leaves rows that record-based cleanup can't see."""
    store = store or get_index_store()
    rm = store.record_manager
    params: dict = {"ns": rm.namespace}
    where = ""
    if file_ids is not None:
        params["ids"] = list(dict.fromkeys(uuid.UUID(str(i)) for i in file_ids))
        if not params["ids"]:
            return 0
        where = "c.file_id = ANY(:ids) AND "
    with rm.engine.begin() as conn:
        return conn.execute(
            text(
                f"DELETE FROM kb_chunks c WHERE {where}NOT EXISTS (SELECT 1 FROM upsertion_record r"
                " WHERE r.namespace = :ns AND r.key = c.langchain_id::text)"
            ),
            params,
        ).rowcount


def index_files(
    file_ids: Iterable[uuid.UUID],
    *,
    store: IndexStore | None = None,
    session_factory=None,
) -> IndexResult:
    """(Re)index the given nodes. Accepts any touched ids: ids that are soft-deleted,
    missing, folders, or have blank content are unindexed (counted in `num_deleted`);
    the rest are indexed with `cleanup="incremental"`, so unchanged chunks are
    skipped and stale ones deleted. Per-file failures land in `failed`, never raise."""
    ids = list(dict.fromkeys(uuid.UUID(str(i)) for i in file_ids))
    result = IndexResult()
    if not ids:
        return result
    store = store or get_index_store()

    try:
        docs = list(_loader(ids, session_factory).lazy_load())
    except Exception as exc:  # noqa: BLE001
        log.exception("loading files for indexing failed")
        result.failed.extend((fid, f"{type(exc).__name__}: {exc}") for fid in ids)
        return result

    by_id: dict[uuid.UUID, list] = {}
    for doc in docs:
        by_id.setdefault(uuid.UUID(doc.metadata["file_id"]), []).append(doc)

    gone = [fid for fid in ids if fid not in by_id]
    try:
        result.num_deleted += _unindex(store, gone)
    except Exception as exc:  # noqa: BLE001
        log.exception("unindexing files failed")
        result.failed.extend((fid, f"{type(exc).__name__}: {exc}") for fid in gone)

    for fid, file_docs in by_id.items():
        _index_one(store, fid, file_docs, result)
    return result


def reconcile(
    file_ids: Iterable[uuid.UUID], *, store: IndexStore | None = None, session_factory=None
) -> IndexResult:
    """Make the index hold exactly the committed state of these nodes: `index_files`
    (unindexes missing/deleted ones, drops stale chunks of the rest, embeds only what's
    missing) plus `delete_untracked_chunks`. State-based, so it's the right undo after a
    rollback *and* the right cleanup after a commit -- even when it's unknown which of
    the two happened (a connection lost during COMMIT). Never raises per file."""
    ids = list(dict.fromkeys(uuid.UUID(str(i)) for i in file_ids))
    store = store or get_index_store()
    result = index_files(ids, store=store, session_factory=session_factory)
    try:
        result.num_deleted += delete_untracked_chunks(ids, store=store)
    except Exception as exc:  # noqa: BLE001
        log.exception("deleting untracked chunks failed")
        result.failed.extend((fid, f"{type(exc).__name__}: {exc}") for fid in ids)
    return result


def unindex_files(file_ids: Iterable[uuid.UUID], *, store: IndexStore | None = None) -> int:
    """Remove every chunk of the given files. Returns the number of chunks removed."""
    ids = list(dict.fromkeys(uuid.UUID(str(i)) for i in file_ids))
    if not ids:
        return 0
    return _unindex(store or get_index_store(), ids)


def reindex_all(*, store: IndexStore | None = None, session_factory=None) -> IndexResult:
    """Rebuild the index from every active file, with "full" cleanup:
    afterwards the index holds chunks only for those nodes (chunks of deleted,
    folders and vanished nodes are removed).

    Semantically `index(all_chunks, cleanup="full")`, but run file by file so one
    failing file doesn't abort the rebuild: each file is indexed incrementally, then
    every record not touched since the run started is deleted -- except records of
    files that failed, whose previous chunks are kept rather than lost -- and so is every
    chunk row without a record (`delete_untracked_chunks`).
    """
    store = store or get_index_store()
    rm = store.record_manager
    result = IndexResult()
    started = rm.get_time()

    by_id: dict[uuid.UUID, list] = {}
    for doc in _loader(None, session_factory).lazy_load():
        by_id.setdefault(uuid.UUID(doc.metadata["file_id"]), []).append(doc)
    for fid, file_docs in by_id.items():
        _index_one(store, fid, file_docs, result)

    keep = set(rm.list_keys(group_ids=[str(fid) for fid, _ in result.failed])) if result.failed else set()
    stale = [k for k in rm.list_keys(before=started) if k not in keep]
    for i in range(0, len(stale), 1000):
        batch = stale[i : i + 1000]
        store.vector_store.delete(batch)
        rm.delete_keys(batch)
    result.num_deleted += len(stale)
    result.num_deleted += delete_untracked_chunks(None, store=store)
    return result
