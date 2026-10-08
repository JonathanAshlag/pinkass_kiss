"""
Semantic (embedding) search over the derived `kb_chunks` index, scoped to a manifest.

The canonical data stays in `files`; this module only reads chunks that
`kb.semantic_index.indexer` derived from it. Every hit is mapped back to the DCI path of its file
(the same path `kb.retrieval.dci.read_lines` accepts), and its `start_line`/`end_line` are line
numbers in the virtual file `kb.okf.render_virtual_file` renders (the one owner of that text
and its line coordinates, which DCI reads too) -- so an agent can go straight from a hit to
`read_lines(path, offset=start_line)`.

Scope = the files the manifest resolves to (`kb.storage.dal.resolve_manifest`),
optionally intersected with a tags/status `query_metadata` filter. Soft-deleted nodes
-- and files that never committed -- drop out of the scope even if they have chunks.

Filtered HNSW: the scope becomes a `file_id IN (...)` filter on the vector query. Plain
HNSW visits only `ef_search` candidates and filters afterwards, so a narrow manifest in
a large index could get fewer than `k` hits (or none). The vector store is built with
pgvector's iterative scan (`hnsw.iterative_scan = relaxed_order`, see
`kb.semantic_index.vectorstore`), which keeps scanning until `k` rows pass the filter; relaxed order
means rows may come back slightly unsorted, so hits are re-sorted here.

Read-time validation: a hit is returned only if its chunk text is still at its lines of
the file's current virtual file. Writes stage new chunks *before* they commit (see
kb.service) and drop stale ones *after*, so for a moment an edited file can have chunks
of uncommitted or replaced content; those never reach the caller. To keep `k` hits
when some are dropped, `OVERFETCH` x `k` rows are fetched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from kb import okf
from kb.storage import dal
from kb.storage.models import File
from kb.retrieval.dci import _Scope

SNIPPET_CHARS = 240
OVERFETCH = 2


@dataclass
class SearchHit:
    file_id: uuid.UUID
    path: str  # DCI path, valid for read_lines/search_lines in the same manifest
    title: str
    heading: str | None
    start_line: int  # 1-based, in the virtual file (kb.okf.render_virtual_file)
    end_line: int
    snippet: str
    score: float  # cosine similarity (1 - cosine distance): higher = closer, in [-1, 1]


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 1].rstrip() + "…"


def semantic_search(
    session: Session,
    manifest_id: uuid.UUID,
    query: str,
    *,
    k: int = 8,
    tags: list[str] | None = None,
    status: str | None = None,
    store=None,
) -> list[SearchHit]:
    """The `k` chunks closest to `query` among the manifest's files
    (optionally only those with all `tags` / with `status`), best first. ValueError for
    an unknown manifest or k < 1. Empty scope -> [] without touching the index."""
    if k < 1:
        raise ValueError("k must be >= 1")
    scope = _Scope(session, manifest_id)  # ValueError on unknown manifest
    in_scope = {node_id: node for node_id, node in scope.nodes.items() if isinstance(node, File)}
    if tags or status is not None:
        allowed = {n.id for n in dal.query_metadata(session, tags=tags, status=status)}
        in_scope = {node_id: node for node_id, node in in_scope.items() if node_id in allowed}
    if not in_scope or not query.strip():
        return []

    from kb.semantic_index.chunking import chunk_text  # strips the "<title> > <heading>" context prefix

    if store is None:
        from kb.semantic_index.vectorstore import get_index_store

        store = get_index_store()
    results = store.vector_store.similarity_search_with_score(
        query, k=k * OVERFETCH, filter={"file_id": {"$in": [str(i) for i in in_scope]}}
    )

    lines: dict[uuid.UUID, list[str]] = {}  # rendered once per hit file

    def current(node: File, doc) -> bool:
        if node.id not in lines:
            lines[node.id] = okf.render_virtual_file(node).split("\n")
        start, end = int(doc.metadata["start_line"]), int(doc.metadata["end_line"])
        return chunk_text(doc) in "\n".join(lines[node.id][start - 1 : end])

    hits: list[SearchHit] = []
    for doc, distance in results:
        meta = doc.metadata
        file_id = meta["file_id"]
        if not isinstance(file_id, uuid.UUID):
            file_id = uuid.UUID(str(file_id))
        node = in_scope.get(file_id)
        if node is None or not current(node, doc):  # out of scope / stale or uncommitted
            continue
        hits.append(
            SearchHit(
                file_id=file_id,
                path=scope.path(node),
                title=node.title,
                heading=meta.get("heading"),
                start_line=int(meta["start_line"]),
                end_line=int(meta["end_line"]),
                snippet=_snippet(chunk_text(doc)),
                score=1.0 - float(distance),
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)  # iterative scan is relaxed_order
    return hits[:k]
