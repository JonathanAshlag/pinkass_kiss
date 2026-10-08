"""Wiring for the semantic index: a PGVectorStore bound to `kb_chunks` (created by
migration 0002, never by this module) plus a SQLRecordManager over `upsertion_record`.

Nothing here connects at import time; `get_index_store()` builds lazily and caches.
Does not import `kb.storage.dal` / `kb.service`.

Env vars:
- `DATABASE_URL`      -- same `postgresql+psycopg://...` URL as the rest of the app. The
                         vector store uses it through an *async* SQLAlchemy engine
                         (psycopg3 async); the record manager through a sync one.
- `EMBEDDINGS_MODEL`  -- `init_embeddings` spec, default `ollama:nomic-embed-text`;
                         `fake` = DeterministicFakeEmbedding (offline dev, tests). Every
                         write embeds before it commits, so some model must be reachable.
                         It names the record manager's namespace, so changing it means a
                         full re-embed (`scripts/reindex.py --all`).
- `EMBEDDINGS_BASE_URL` -- set with an `openai:<model>` spec to use any OpenAI-compatible
                         embeddings server instead of OpenAI: TEI or vLLM, e.g.
                         `http://localhost:8090/v1`. `<model>` is the served model name
                         (vLLM checks it, TEI ignores it). `EMBEDDINGS_API_KEY` if the
                         server wants one; `EMBEDDINGS_BATCH_SIZE` (default 32, TEI's
                         default `--max-client-batch-size`) caps inputs per request.
- `EMBEDDING_DIM`     -- vector size, default 768. Must match `kb_chunks.embedding`
                         (the migration reads the same var when it creates the table).

Verified facts (langchain-postgres 0.0.18, langchain-classic 1.0.8, langchain-core 1.6):

- Use the plain sync methods (`add_documents`, `delete`, `similarity_search_with_score`,
  ...). `PGEngine.from_connection_string` runs them on a shared background event loop
  thread, so they work from sync code (FastAPI sync routes, scripts, pytest) -- but do
  NOT call them from inside a running event loop on that same thread's loop; from async
  code use the `a*` variants instead.
- Chunk ids (`langchain_id`) are a `uuid` column. Of `index()`'s built-in key encoders
  only the default "sha1" yields uuids (and emits a SHA-1 UserWarning); "sha256" /
  "sha512" / "blake2b" yield hex digests that the uuid column rejects. Pass
  `key_encoder=chunk_key_encoder` (below: sha256 -> uuid5) to `index()`/`aindex()`.
- Typical call: `index(docs, store.record_manager, store.vector_store,
  cleanup="incremental", source_id_key="file_id", key_encoder=chunk_key_encoder)`
  -- re-running unchanged docs reports them as `num_skipped`; re-indexing a file with
  fewer chunks deletes the stale ones (`num_deleted`).
- Metadata `file_id` must be a `str` (not `uuid.UUID`): it is the record manager's
  `group_id` (VARCHAR) when `source_id_key="file_id"`, it must be JSON-serializable for
  hashing, and PGVectorStore's `$in` filter only accepts str/int/float values. Filter
  like `filter={"file_id": {"$in": [str(id), ...]}}` (a `uuid.UUID` inside `$in`
  raises NotImplementedError; plain equality `{"file_id": str(id)}` also works).
- `similarity_search_with_score` returns cosine *distance* (lower = closer, 0..2).
- `add_documents` inserts row by row (one connection round-trip per chunk).
- Metadata keys in `METADATA_COLUMNS` go to their own columns; anything else lands in
  the `langchain_metadata` JSON column. Search results return both merged into
  `Document.metadata`, with `file_id` read back as a `uuid.UUID` from the column.
- `kb_chunks.file_id` has no FK (migration 0003): chunks are staged before their file
  row commits, from these classes' own connections. Soft delete touches neither chunks
  nor `upsertion_record` rows. `index([], ...,
  cleanup="incremental")` can't remove a file whose docs are all gone (incremental only
  cleans groups present in the batch) -- instead do
  `keys = record_manager.list_keys(group_ids=[str(file_id)])`,
  `vector_store.delete(keys)`, `record_manager.delete_keys(keys)`.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.indexing import RecordManager
from langchain_core.vectorstores import VectorStore

CHUNKS_TABLE = "kb_chunks"
METADATA_COLUMNS = ["file_id", "heading", "start_line", "end_line"]
EMBEDDING_DIM: int = int(os.environ.get("EMBEDDING_DIM", "768"))
DEFAULT_EMBEDDINGS_MODEL = "ollama:nomic-embed-text"

_CHUNK_ID_NAMESPACE = uuid.UUID("6f1d7c62-3a0e-4b8e-9a51-0b6c1f6a9e21")


def chunk_key_encoder(doc: Document) -> str:
    """Deterministic uuid (as str) from a chunk's content + metadata. Pass this as
    `key_encoder=` to `langchain_core.indexing.index()`: kb_chunks.langchain_id is a
    uuid column, and index()'s built-in hex-digest encoders aren't valid uuids."""
    meta = json.dumps(doc.metadata or {}, sort_keys=True, default=str)
    digest = hashlib.sha256(
        hashlib.sha256(doc.page_content.encode()).hexdigest().encode()
        + hashlib.sha256(meta.encode()).hexdigest().encode()
    ).hexdigest()
    return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, digest))


@dataclass
class IndexStore:
    vector_store: VectorStore  # a PGVectorStore bound to kb_chunks
    record_manager: RecordManager  # SQLRecordManager, namespace f"kb_chunks/{model}"
    embeddings: Embeddings


def _embeddings_model() -> str:
    return os.environ.get("EMBEDDINGS_MODEL") or DEFAULT_EMBEDDINGS_MODEL


def _init_embeddings(model: str) -> Embeddings:
    if model == "fake":
        from langchain_core.embeddings import DeterministicFakeEmbedding

        return DeterministicFakeEmbedding(size=EMBEDDING_DIM)
    base_url = os.environ.get("EMBEDDINGS_BASE_URL", "").strip()
    if base_url and model.startswith("openai:"):
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=model.removeprefix("openai:"),
            base_url=base_url,
            api_key=os.environ.get("EMBEDDINGS_API_KEY") or "unused",
            # Off: it pre-tokenizes with OpenAI's tiktoken and sends token ids, which is
            # wrong for any other model's tokenizer. The server tokenizes the text itself.
            check_embedding_ctx_length=False,
            chunk_size=int(os.environ.get("EMBEDDINGS_BATCH_SIZE") or 32),
        )
    from langchain.embeddings import init_embeddings

    return init_embeddings(model)


def _iterative_hnsw_options():
    """Query options PGVectorStore applies (`SET LOCAL`, per search) before each
    similarity search: pgvector >= 0.8 iterative HNSW scan, so a filtered search (e.g.
    `file_id IN (<manifest scope>)`) keeps walking the graph until it has `k` rows
    that pass the filter instead of returning fewer than `k` (plain HNSW only visits
    `ef_search` candidates, then filters). `relaxed_order` may return rows slightly out
    of distance order -- callers re-sort (kb.retrieval.semantic does)."""
    from langchain_postgres.v2.indexes import HNSWQueryOptions

    class IterativeHNSWQueryOptions(HNSWQueryOptions):
        def to_parameter(self) -> list[str]:
            return [*super().to_parameter(), "hnsw.iterative_scan = relaxed_order"]

    return IterativeHNSWQueryOptions()


def build_index_store(
    *,
    database_url: str | None = None,
    embeddings: Embeddings | None = None,
    namespace: str | None = None,
) -> IndexStore:
    """Bind a PGVectorStore to the (already migrated) `kb_chunks` table and a
    SQLRecordManager to `upsertion_record`. Connects once to introspect kb_chunks."""
    from langchain_classic.indexes import SQLRecordManager
    from langchain_postgres import PGEngine, PGVectorStore

    if database_url is None:
        from kb.storage.db import get_database_url

        database_url = get_database_url()
    model = _embeddings_model()
    if embeddings is None:
        embeddings = _init_embeddings(model)
    if namespace is None:
        namespace = f"{CHUNKS_TABLE}/{model}"

    pg_engine = PGEngine.from_connection_string(database_url)
    vector_store = PGVectorStore.create_sync(
        pg_engine,
        embeddings,
        CHUNKS_TABLE,
        metadata_columns=METADATA_COLUMNS,
        index_query_options=_iterative_hnsw_options(),
    )
    # Table is owned by migration 0002 -- never call record_manager.create_schema().
    record_manager = SQLRecordManager(namespace, db_url=database_url)
    return IndexStore(vector_store=vector_store, record_manager=record_manager, embeddings=embeddings)


_store: IndexStore | None = None
_lock = threading.Lock()


def get_index_store() -> IndexStore:
    """The process-wide IndexStore, built on first use from env config."""
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = build_index_store()
    return _store


def set_index_store(store: IndexStore | None) -> None:
    """Override (tests) or reset (None -> rebuilt lazily) the cached IndexStore."""
    global _store
    with _lock:
        _store = store
