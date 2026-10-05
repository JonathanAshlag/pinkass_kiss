"""
Semantic search (kb.retrieval.semantic) and its wiring: service passthroughs, the
/manifests/{id}/semantic and /index/reindex routes, the index-after-write background
hook, and the agent tool.

Chunks are seeded straight into kb_chunks with `vector_store.add_documents` (metadata
file_id / heading / start_line / end_line), so these tests don't depend on
kb.semantic_index.indexer -- except `test_index_files_roundtrip`, which runs only when it exists.
Embeddings are `DeterministicFakeEmbedding`: a query equal to a chunk's text lands at
distance 0 from it, everything else is effectively random.
"""

import contextlib
import importlib.util
import os
import uuid

import pytest
from sqlalchemy import text

pytest.importorskip("langchain_postgres")

from langchain_core.documents import Document  # noqa: E402
from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402


@pytest.fixture(scope="module")
def store(migrated_db):
    from kb.semantic_index.vectorstore import EMBEDDING_DIM, build_index_store, set_index_store

    s = build_index_store(
        database_url=os.environ["TEST_DATABASE_URL"],
        embeddings=DeterministicFakeEmbedding(size=EMBEDDING_DIM),
        namespace=f"kb_chunks/test-search-{uuid.uuid4().hex[:8]}",
    )
    set_index_store(s)
    yield s
    set_index_store(None)


class KB:
    """Creates nodes/manifests (committed, so other sessions and the vector store see
    them), seeds chunks for them, and hard-deletes everything it made afterwards."""

    def __init__(self, store):
        from kb.storage.db import SessionLocal

        self.store = store
        self.session = SessionLocal()
        self.file_ids: list[uuid.UUID] = []
        self.manifest_ids: list[uuid.UUID] = []

    def node(self, title, content="", *, parent=None, kind="file", **fields):
        from kb import service

        node = service.create_file(
            self.session,
            parent_id=parent.id if parent is not None else None,
            kind=kind,
            title=title,
            content=content if kind == "file" or content else None,
            **fields,
        )
        self.session.commit()
        self.file_ids.append(node.id)
        return node

    def manifest(self, *members):
        from kb import service

        m = service.create_manifest(self.session, f"test-search-{uuid.uuid4().hex[:8]}")
        for member in members:
            service.add_manifest_member(self.session, m.id, file_id=member.id)
        self.session.commit()
        self.manifest_ids.append(m.id)
        return m

    def chunk(self, node, line_text, heading=None):
        """Seed one chunk whose text is `line_text`, located where that line sits in
        the DCI-rendered virtual file (so start_line matches read_lines)."""
        from kb.retrieval.dci import render_virtual_file

        lines = render_virtual_file(node).split("\n")
        line_no = lines.index(line_text) + 1
        self.store.vector_store.add_documents(
            [
                Document(
                    page_content=line_text,
                    metadata={
                        "file_id": str(node.id),
                        "heading": heading,
                        "start_line": line_no,
                        "end_line": line_no,
                    },
                )
            ]
        )
        return line_no

    @contextlib.contextmanager
    def tx(self):
        try:
            yield self.session
            self.session.commit()
        except BaseException:
            self.session.rollback()
            raise

    def close(self):
        self.session.rollback()
        with self.tx():
            if self.manifest_ids:
                self.session.execute(
                    text("DELETE FROM manifests WHERE id = ANY(:ids)"), {"ids": self.manifest_ids}
                )
            if self.file_ids:  # kb_chunks rows go with them (FK ON DELETE CASCADE)
                self.session.execute(
                    text("DELETE FROM files WHERE id = ANY(:ids)"), {"ids": self.file_ids}
                )
        self.session.close()


@pytest.fixture
def kb(store):
    k = KB(store)
    yield k
    k.close()


def search(kb, manifest, query, **kwargs):
    from kb import service

    with kb.tx():
        return service.semantic_search(kb.session, manifest.id, query, store=kb.store, **kwargs)


# --------------------------------------------------------------------------
# kb.retrieval.semantic via kb.service
# --------------------------------------------------------------------------


def test_manifest_scoping(kb):
    inside = kb.node("inside", "# In\n\nthe secret recipe is in here")
    outside = kb.node("outside", "# Out\n\nthe secret recipe is in here too")
    kb.chunk(inside, "the secret recipe is in here")
    kb.chunk(outside, "the secret recipe is in here too")
    m = kb.manifest(inside)

    # the out-of-scope chunk is an exact match for this query, yet never appears
    hits = search(kb, m, "the secret recipe is in here too", k=10)
    assert [h.file_id for h in hits] == [inside.id]
    assert hits[0].path == "inside" and hits[0].title == "inside"


def test_tag_and_status_filters(kb):
    folder = kb.node("box", kind="folder")
    a = kb.node("a", "alpha line", parent=folder, tags=["x", "y"], status="stable")
    b = kb.node("b", "beta line", parent=folder, tags=["x"], status="draft")
    c = kb.node("c", "gamma line", parent=folder, tags=["z"], status="stable")
    for node, line in ((a, "alpha line"), (b, "beta line"), (c, "gamma line")):
        kb.chunk(node, line)
    m = kb.manifest(folder)

    def ids(**kw):
        return {h.file_id for h in search(kb, m, "anything", k=10, **kw)}

    assert ids() == {a.id, b.id, c.id}
    assert ids(tags=["x"]) == {a.id, b.id}
    assert ids(tags=["x", "y"]) == {a.id}
    assert ids(status="stable") == {a.id, c.id}
    assert ids(tags=["x"], status="draft") == {b.id}
    assert ids(tags=["nope"]) == set()


def test_soft_deleted_file_excluded(kb):
    from kb import service

    keep = kb.node("keep", "kept text")
    gone = kb.node("gone", "deleted text")
    kb.chunk(keep, "kept text")
    kb.chunk(gone, "deleted text")
    m = kb.manifest(keep, gone)
    assert {h.file_id for h in search(kb, m, "deleted text")} == {keep.id, gone.id}

    with kb.tx():
        service.delete_node(kb.session, gone.id)
    # out of scope as soon as it's soft-deleted, even with its chunks still indexed ...
    assert {h.file_id for h in search(kb, m, "deleted text")} == {keep.id}
    # ... and once its chunks are gone too (what index_files does for a deleted id)
    kb.store.vector_store.delete(
        [
            doc.id
            for doc, _ in kb.store.vector_store.similarity_search_with_score(
                "deleted text", k=5, filter={"file_id": str(gone.id)}
            )
        ]
    )
    assert {h.file_id for h in search(kb, m, "deleted text")} == {keep.id}


def test_hit_path_roundtrips_into_read_lines(kb):
    from kb import service

    folder = kb.node("papers", kind="folder")
    doc = kb.node("doc.md", "# Methods\n\nwe fine-tune on wikitext\n\nmore text", parent=folder)
    line_no = kb.chunk(doc, "we fine-tune on wikitext", heading="Methods")
    m = kb.manifest(folder)

    (hit,) = search(kb, m, "we fine-tune on wikitext", k=1)
    assert (hit.path, hit.heading, hit.start_line, hit.end_line) == (
        "papers/doc.md",
        "Methods",
        line_no,
        line_no,
    )
    assert hit.score == pytest.approx(1.0, abs=1e-4)  # exact-text match: similarity 1
    with kb.tx():
        out = service.read_lines(kb.session, m.id, hit.path, offset=hit.start_line, limit=1)
    assert out.text.split("\n")[1] == f"{line_no}\twe fine-tune on wikitext"


def test_empty_scope_returns_nothing(kb):
    from kb.retrieval import semantic as search_mod

    folder = kb.node("only-a-folder", kind="folder")  # no content -> not searchable
    m_folder = kb.manifest(folder)
    m_empty = kb.manifest()

    class Exploding:
        @property
        def vector_store(self):
            raise AssertionError("empty scope must not query the index")

    for m in (m_folder, m_empty):
        with kb.tx():
            assert search_mod.semantic_search(kb.session, m.id, "q", store=Exploding()) == []
    # tags that match nothing in scope -> empty too
    with kb.tx():
        doc = kb.node("tagged", "x")
        m = kb.manifest(doc)
    with kb.tx():
        assert search_mod.semantic_search(kb.session, m.id, "q", tags=["nope"], store=Exploding()) == []


def test_unknown_manifest_raises(kb):
    from kb import service

    with pytest.raises(ValueError):
        with kb.tx():
            service.semantic_search(kb.session, uuid.uuid4(), "q", store=kb.store)


def test_narrow_manifest_gets_its_hits_among_many_files(kb):
    """Filtered HNSW without iterative scan visits only ef_search (40) candidates and
    then filters, so a narrow scope could come back short; the store applies pgvector's
    iterative scan to every search so it keeps going until k rows pass the filter.

    At this table size Postgres picks the exact file_id-index path for a narrow IN
    filter anyway, so this checks the outcome (all k narrow hits, sorted) plus that the
    iterative-scan option is wired in; the HNSW shortfall itself was demonstrated by
    hand on 20k rows (forced HNSW plan: 4/20 hits plain, 20/20 with iterative scan)."""
    from kb.semantic_index.vectorstore import _iterative_hnsw_options

    assert "hnsw.iterative_scan = relaxed_order" in _iterative_hnsw_options().to_parameter()
    noise_root = kb.node("noise", kind="folder")
    noise_lines = []
    for i in range(300):
        node = kb.node(f"n{i}", f"noise line {i}", parent=noise_root)
        noise_lines.append((node, f"noise line {i}"))
    kb.store.vector_store.add_documents(
        [
            Document(
                page_content=line,
                metadata={"file_id": str(node.id), "heading": None, "start_line": 7, "end_line": 7},
            )
            for node, line in noise_lines
        ]
    )
    narrow = [kb.node(f"narrow{i}", f"needle {i}") for i in range(2)]
    for i, node in enumerate(narrow):
        kb.chunk(node, f"needle {i}")
    m = kb.manifest(*narrow)

    # query = a noise chunk's exact text, so the global nearest neighbours are all noise
    hits = search(kb, m, "noise line 17", k=2)
    assert {h.file_id for h in hits} == {n.id for n in narrow}
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


# --------------------------------------------------------------------------
# REST routes
# --------------------------------------------------------------------------


@pytest.fixture
def client(store):
    from fastapi.testclient import TestClient

    from kb.api import app

    return TestClient(app)


def test_semantic_route(kb, client):
    doc = kb.node("route-doc", "# H\n\nroute me please")
    line_no = kb.chunk(doc, "route me please", heading="H")
    m = kb.manifest(doc)

    r = client.get(f"/manifests/{m.id}/semantic", params={"q": "route me please", "k": 3})
    assert r.status_code == 200, r.text
    (hit,) = r.json()
    assert hit["file_id"] == str(doc.id)
    assert (hit["path"], hit["heading"], hit["start_line"]) == ("route-doc", "H", line_no)

    r = client.get(f"/manifests/{m.id}/semantic", params={"q": "x", "tags": ["absent"]})
    assert r.status_code == 200 and r.json() == []
    assert client.get(f"/manifests/{uuid.uuid4()}/semantic", params={"q": "x"}).status_code == 404
    assert client.get(f"/manifests/{m.id}/semantic", params={"q": ""}).status_code == 422


def test_reindex_route(monkeypatch, client):
    from types import SimpleNamespace

    from kb import service

    calls = []
    result = SimpleNamespace(num_added=1, num_updated=0, num_skipped=2, num_deleted=0, failed=[])
    monkeypatch.setattr(service, "index_files", lambda ids: calls.append(("ids", ids)) or result)
    monkeypatch.setattr(service, "reindex_all", lambda: calls.append(("all",)) or result)

    fid = uuid.uuid4()
    r = client.post("/index/reindex", json={"file_ids": [str(fid)]})
    assert r.status_code == 200 and r.json()["num_skipped"] == 2
    assert client.post("/index/reindex").status_code == 200
    assert client.post("/index/reindex", json={}).status_code == 200
    assert calls == [("ids", [fid]), ("all",), ("all",)]


# --------------------------------------------------------------------------
# Index-after-write hook
# --------------------------------------------------------------------------


@pytest.fixture
def recorder(monkeypatch, kb):
    """Replaces service.index_files; each call records the ids plus how each node
    looks from a *fresh* session -- i.e. what was committed when indexing ran."""
    from kb import service
    from kb.storage.db import SessionLocal

    monkeypatch.setenv("KB_AUTO_INDEX", "1")
    calls = []

    def fake_index_files(file_ids, **kwargs):
        with SessionLocal() as fresh:
            seen = {}
            for fid in file_ids:
                node = service.get_node(fresh, fid, include_deleted=True)
                seen[fid] = None if node is None else (node.content, node.deleted_at is not None)
        calls.append((list(file_ids), seen))

    monkeypatch.setattr(service, "index_files", fake_index_files)
    return calls


def test_index_hook_runs_after_commit(kb, client, recorder):
    r = client.post("/files", json={"kind": "folder", "title": "hook-root"})
    assert r.status_code == 201
    root = uuid.UUID(r.json()["id"])
    kb.file_ids.append(root)
    r = client.post("/files", json={"parent_id": str(root), "title": "hook-doc", "content": "v1"})
    doc = uuid.UUID(r.json()["id"])
    kb.file_ids.append(doc)
    assert recorder[-1] == ([doc], {doc: ("v1", False)})

    client.patch(f"/files/{doc}", json={"content": "v2"})
    assert recorder[-1] == ([doc], {doc: ("v2", False)})

    # move: no reindex
    n_calls = len(recorder)
    client.post(f"/files/{doc}/move", json={"new_parent_id": None})
    client.post(f"/files/{doc}/move", json={"new_parent_id": str(root)})
    assert len(recorder) == n_calls

    # cascade delete: the folder and its (active) descendants, all seen as deleted
    assert client.delete(f"/files/{root}").status_code == 204
    ids, seen = recorder[-1]
    assert set(ids) == {root, doc} and all(deleted for _, deleted in seen.values())

    client.post(f"/files/{doc}/restore")
    assert recorder[-1] == ([doc], {doc: ("v2", False)})


def test_index_hook_on_ingest(kb, client, recorder):
    r = client.post(
        "/ingest",
        files=[("files", ("a.md", b"# A\n\nalpha")), ("files", ("b.md", b"# B\n\nbeta"))],
        data={"paths": ["up/a.md", "up/sub/b.md"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    created = {uuid.UUID(i) for i in [body["root_id"], *body["files_created"], *body["folders_created"]]}
    kb.file_ids.extend(created)
    ids, seen = recorder[-1]
    assert set(ids) == created and all(v is not None for v in seen.values())


def test_index_hook_failure_never_fails_request(kb, client, monkeypatch):
    from kb import service

    monkeypatch.setenv("KB_AUTO_INDEX", "1")

    def boom(file_ids, **kwargs):
        raise RuntimeError("embeddings server down")

    monkeypatch.setattr(service, "index_files", boom)
    r = client.post("/files", json={"title": "still-created", "content": "x"})
    assert r.status_code == 201
    kb.file_ids.append(uuid.UUID(r.json()["id"]))


def test_index_hook_disabled_by_env(kb, client, recorder, monkeypatch):
    monkeypatch.setenv("KB_AUTO_INDEX", "0")
    r = client.post("/files", json={"title": "no-index", "content": "x"})
    kb.file_ids.append(uuid.UUID(r.json()["id"]))
    assert recorder == []


# --------------------------------------------------------------------------
# Agent tool
# --------------------------------------------------------------------------


def test_agent_semantic_search_tool(kb):
    from kb.retrieval.agent_tools import AgentTools

    doc = kb.node("agent-doc", "# Setup\n\nwe train for ten epochs")
    line_no = kb.chunk(doc, "we train for ten epochs", heading="Setup")
    m = kb.manifest(doc)

    tools = AgentTools(m.id)
    out = tools.semantic_search("we train for ten epochs", k=3)
    first, snippet, *_, hint = out.split("\n")
    assert first == f"agent-doc:{line_no}-{line_no} [Setup] (1.00)"
    assert snippet.strip() == "we train for ten epochs"
    assert "read_lines" in hint
    assert AgentTools(uuid.uuid4()).semantic_search("x").startswith("error:")

    names = [t.name for t in tools.as_langchain()]
    assert names == ["list_paths", "search_lines", "read_lines"]
    lc = {t.name: t for t in tools.as_langchain(include_semantic=True)}
    assert "semantic_search" in lc and "search_lines" in lc["semantic_search"].description
    assert lc["semantic_search"].invoke({"query": "we train for ten epochs"}) == out


# --------------------------------------------------------------------------
# Through engineer A's indexer, when it exists
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("kb.semantic_index.indexer") is None, reason="kb.semantic_index.indexer not built yet"
)
def test_index_files_roundtrip(kb):
    from kb import service

    folder = kb.node("synced", kind="folder")
    doc = kb.node(
        "paper.md",
        "# Intro\n\nTransformers are great.\n\n## Data\n\nWe use the Penn Treebank corpus.",
        parent=folder,
    )
    m = kb.manifest(folder)
    result = service.index_files([folder.id, doc.id], store=kb.store)
    assert not result.failed and result.num_added >= 1

    hits = search(kb, m, "We use the Penn Treebank corpus.", k=5)
    assert hits and all(h.file_id == doc.id for h in hits)
    with kb.tx():
        for h in hits:
            out = service.read_lines(kb.session, m.id, h.path, offset=h.start_line, limit=1)
            assert out.text.startswith("synced/paper.md")

    with kb.tx():
        service.delete_node(kb.session, doc.id)
    service.index_files([doc.id], store=kb.store)  # deleted -> unindexed
    assert search(kb, m, "We use the Penn Treebank corpus.") == []
