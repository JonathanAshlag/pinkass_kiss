"""Keeps the semantic index (`kb_chunks`) in step with the canonical `files` table.

- `index_files(ids)`   -- for any ids a write touched: active nodes with content are
                          (re)indexed incrementally; deleted / missing / content-less
                          ones are unindexed. Never raises for a per-file problem.
- `unindex_files(ids)` -- drop every chunk of those files.
- `reindex_all()`      -- rebuild from every active node with content, then remove
                          every chunk not (re)confirmed by this run ("full" cleanup).

All three read the DB through their own sessions (`session_factory`, default
`SessionLocal`), so call them *after* the write that touched the nodes has committed.
They call the PGVectorStore's sync methods -- don't call them from inside a running
event loop (use a threadpool / sync route).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from langchain_core.indexing import index

from kb.semantic_index.chunking import FileNodeLoader, split_documents
from kb.semantic_index.vectorstore import IndexStore, chunk_key_encoder, get_index_store

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


def index_files(
    file_ids: Iterable[uuid.UUID],
    *,
    store: IndexStore | None = None,
    session_factory=None,
) -> IndexResult:
    """(Re)index the given nodes. Accepts any touched ids: ids that are soft-deleted,
    missing, or have null/blank content are unindexed (counted in `num_deleted`);
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


def unindex_files(file_ids: Iterable[uuid.UUID], *, store: IndexStore | None = None) -> int:
    """Remove every chunk of the given files. Returns the number of chunks removed."""
    ids = list(dict.fromkeys(uuid.UUID(str(i)) for i in file_ids))
    if not ids:
        return 0
    return _unindex(store or get_index_store(), ids)


def reindex_all(*, store: IndexStore | None = None, session_factory=None) -> IndexResult:
    """Rebuild the index from every active node with content, with "full" cleanup:
    afterwards the index holds chunks only for those nodes (chunks of deleted,
    content-less or vanished nodes are removed).

    Semantically `index(all_chunks, cleanup="full")`, but run file by file so one
    failing file doesn't abort the rebuild: each file is indexed incrementally, then
    every record not touched since the run started is deleted -- except records of
    files that failed, whose previous chunks are kept rather than lost.
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
    return result
