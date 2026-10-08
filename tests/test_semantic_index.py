"""Semantic index (kb.semantic_index.chunking / kb.semantic_index.indexer): splitter/annotator tests (no DB),
plus DB-backed indexing tests against kb_chunks with fake embeddings."""

import uuid

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from kb.semantic_index.chunking import chunk_text, heading_paths, split_documents


def section(n: int, words: int = 120, tag: str = "") -> str:
    para = " ".join(f"w{n}_{i}{tag}" for i in range(words))
    return f"## Section {n}\n\n{para}\n\n{para[::-1]}\n"


def doc_of(text: str, offset: int = 0, title: str = "T") -> Document:
    return Document(page_content=text, metadata={"file_id": "x", "title": title, "line_offset": offset})


# --------------------------------------------------------------------------
# Splitter / annotator (no DB)
# --------------------------------------------------------------------------


def test_chunk_line_ranges_cover_chunk_text():
    text = "# Paper\n\nintro line\n\n" + "".join(section(i) for i in range(6))
    chunks = split_documents([doc_of(text)], chunk_size=400, chunk_overlap=50)
    assert len(chunks) > 6
    lines = text.split("\n")
    for c in chunks:
        assert set(c.metadata) == {"file_id", "title", "heading", "start_line", "end_line"}
        s, e = c.metadata["start_line"], c.metadata["end_line"]
        assert 1 <= s <= e <= len(lines)
        assert chunk_text(c) in "\n".join(lines[s - 1 : e])
        assert c.page_content.startswith("T > ")


def test_line_offset_shifts_lines():
    text = "a\nb\n\nc"
    [plain] = split_documents([doc_of(text)])
    [shifted] = split_documents([doc_of(text, offset=7)])
    assert (plain.metadata["start_line"], plain.metadata["end_line"]) == (1, 4)
    assert (shifted.metadata["start_line"], shifted.metadata["end_line"]) == (8, 11)


def test_heading_detection():
    text = (
        "pre\n# Methods\nm\n## Data\nd\n```\n# not a heading\n```\n### Deep ###\nx\n"
        "## Model\ny\n# Results\nz"
    )
    paths = heading_paths(text)
    lines = text.split("\n")
    at = dict(zip(lines, paths))
    assert at["pre"] is None
    assert at["# Methods"] == "Methods"
    assert at["d"] == "Methods > Data"
    assert at["# not a heading"] == "Methods > Data"
    assert at["x"] == "Methods > Data > Deep"
    assert at["y"] == "Methods > Model"
    assert at["z"] == "Results"

    chunks = split_documents([doc_of(text)], chunk_size=30, chunk_overlap=0)
    by_start = {c.metadata["start_line"]: c for c in chunks}
    assert by_start[1].metadata["heading"] is None
    assert by_start[9].metadata["heading"] == "Methods > Data > Deep"  # starts on its heading
    assert by_start[9].page_content.startswith("T > Methods > Data > Deep\n\n### Deep ###")


def test_no_heading_doc():
    chunks = split_documents([doc_of("just some text\nmore", title="Notes")])
    assert len(chunks) == 1
    assert chunks[0].metadata["heading"] is None
    assert chunks[0].page_content == "Notes\n\njust some text\nmore"
    assert chunk_text(chunks[0]) == "just some text\nmore"


def test_blank_doc_has_no_chunks():
    assert split_documents([doc_of("  \n\n ")]) == []


def test_openai_compatible_server_embeddings(monkeypatch):
    from langchain_openai import OpenAIEmbeddings

    from kb.semantic_index.vectorstore import _init_embeddings

    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "http://tei:8080/v1")
    monkeypatch.delenv("EMBEDDINGS_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDINGS_BATCH_SIZE", raising=False)
    emb = _init_embeddings("openai:nomic-ai/nomic-embed-text-v1.5")
    assert isinstance(emb, OpenAIEmbeddings)
    assert (emb.model, emb.openai_api_base) == ("nomic-ai/nomic-embed-text-v1.5", "http://tei:8080/v1")
    assert emb.check_embedding_ctx_length is False  # no tiktoken token ids sent to a non-OpenAI model
    assert emb.chunk_size == 32  # TEI's default --max-client-batch-size

    monkeypatch.setenv("EMBEDDINGS_BATCH_SIZE", "128")
    assert _init_embeddings("openai:m").chunk_size == 128


# --------------------------------------------------------------------------
# DB-backed (needs TEST_DATABASE_URL)
# --------------------------------------------------------------------------

FAIL_MARKER = "EMBEDDING-EXPLODES"


class FailingEmbedding(DeterministicFakeEmbedding):
    def embed_documents(self, texts):
        if any(FAIL_MARKER in t for t in texts):
            raise RuntimeError("embedding backend down")
        return super().embed_documents(texts)

    async def aembed_documents(self, texts):
        return self.embed_documents(texts)


@pytest.fixture(scope="module")
def store(migrated_db):
    from conftest import TEST_URL
    from kb.semantic_index.vectorstore import EMBEDDING_DIM, build_index_store, set_index_store

    s = build_index_store(database_url=TEST_URL, embeddings=DeterministicFakeEmbedding(size=EMBEDDING_DIM))
    set_index_store(s)
    yield s
    set_index_store(None)


@pytest.fixture(scope="module")
def failing_store(migrated_db):
    from conftest import TEST_URL
    from kb.semantic_index.vectorstore import EMBEDDING_DIM, build_index_store

    return build_index_store(database_url=TEST_URL, embeddings=FailingEmbedding(size=EMBEDDING_DIM))


@pytest.fixture
def db(migrated_db):
    from kb.storage.db import SessionLocal

    with SessionLocal() as s:
        yield s


def make(db, *, content="", title=None, parent_id=None, **cols):
    """A file (in a fresh root folder unless `parent_id` is given); returns its id.
    Written through the DAL, which records nothing for kb.service's commit-time
    indexing, so these tests drive the indexer (the repair tool) on their own."""
    from kb.storage import dal

    if parent_id is None:
        parent_id = dal.create_folder(db, parent_id=None, title=f"f-{uuid.uuid4().hex[:8]}").id
    node = dal.create_file(
        db, parent_id=parent_id, title=title or f"n-{uuid.uuid4().hex[:8]}", content=content, **cols
    )
    db.commit()
    return node.id


def chunks_of(file_id):
    from sqlalchemy import text

    from kb.storage.db import engine

    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT content, heading, start_line, end_line, langchain_metadata->>'title' AS title "
                "FROM kb_chunks WHERE file_id = :f ORDER BY start_line"
            ),
            {"f": file_id},
        ).all()


def test_create_then_index(db, store):
    from kb.semantic_index.indexer import index_files

    content = "# Top\n\n" + "".join(section(i) for i in range(4))
    fid = make(db, content=content)
    result = index_files([fid])
    rows = chunks_of(fid)
    assert result.failed == []
    assert result.num_added == len(rows) > 1
    assert rows[0].heading == "Top"
    assert rows[0].start_line == 8  # 7 frontmatter lines (---, id, type, kind, title, status, ---)
    assert all(r.start_line <= r.end_line for r in rows)


def test_reindex_unchanged_is_all_skipped(db, store):
    from kb.semantic_index.indexer import index_files

    fid = make(db, content="".join(section(i) for i in range(3)))
    first = index_files([fid])
    again = index_files([fid])
    assert (again.num_added, again.num_deleted, again.num_skipped) == (0, 0, first.num_added)


def test_reindex_unchanged_large_file_is_all_skipped(db, store):
    # > index()'s default batch_size (100): a file split across batches used to have its
    # later chunks dropped by the first batch's incremental cleanup and re-embedded
    # (reindex_all goes through the same per-file _index_one)
    from kb.semantic_index.indexer import index_files

    fid = make(db, content="".join(section(i) for i in range(80)))
    first = index_files([fid])
    assert first.num_added > 100
    again = index_files([fid])
    assert (again.num_added, again.num_deleted, again.num_skipped) == (0, 0, first.num_added)


def test_edit_replaces_changed_and_deletes_stale(db, store):
    from kb.storage import dal
    from kb.semantic_index.indexer import index_files

    fid = make(db, content="".join(section(i) for i in range(5)))
    first = index_files([fid])
    before = {r.content for r in chunks_of(fid)}

    dal.update_node(db, fid, content="".join(section(i) for i in range(3)) + section(3, tag="x"))
    db.commit()
    result = index_files([fid])
    after = {r.content for r in chunks_of(fid)}

    assert result.num_skipped > 0  # sections 0-2 untouched
    assert result.num_added > 0 and result.num_deleted > 0
    assert len(after) == first.num_added + result.num_added - result.num_deleted
    assert not any("w4_" in c for c in after)  # section 4 is gone
    assert any("w3_0x" in c for c in after)
    assert before & after


def test_retag_only_adds_nothing(db, store):
    from kb.storage import dal
    from kb.semantic_index.indexer import index_files

    fid = make(db, content="".join(section(i) for i in range(2)), tags=["a"])
    first = index_files([fid])
    dal.update_node(db, fid, tags=["b", "c"], status="stable")
    db.commit()
    result = index_files([fid])
    assert result.num_added == 0
    assert result.num_skipped == first.num_added


def test_soft_delete_then_restore(db, store):
    from kb.storage import dal
    from kb.semantic_index.indexer import index_files

    fid = make(db, content=section(1))
    n = index_files([fid]).num_added
    dal.delete_node(db, fid)
    db.commit()
    assert index_files([fid]).num_deleted == n
    assert chunks_of(fid) == []

    dal.restore_node(db, fid)
    db.commit()
    assert index_files([fid]).num_added == n
    assert len(chunks_of(fid)) == n


def test_missing_id_is_harmless(store):
    from kb.semantic_index.indexer import index_files

    result = index_files([uuid.uuid4()])
    assert result.failed == [] and result.num_deleted == 0


def test_folders_are_never_indexed(db, store):
    from kb import service
    from kb.semantic_index.indexer import index_files

    folder = service.create_folder(db, parent_id=None, title="box", description="what lives here")
    db.commit()
    result = index_files([folder.id])
    assert result.failed == [] and result.num_added == 0
    assert chunks_of(folder.id) == []


def test_embedding_failure_is_isolated(db, failing_store):
    from kb.semantic_index.indexer import index_files

    bad = make(db, content=f"this one has {FAIL_MARKER} inside")
    good = make(db, content="this one is fine")
    result = index_files([bad, good], store=failing_store)
    assert [fid for fid, _ in result.failed] == [bad]
    assert "embedding backend down" in result.failed[0][1]
    assert chunks_of(bad) == []
    assert len(chunks_of(good)) == 1


def test_reindex_all_removes_chunks_of_deleted_files(db, store):
    from kb.storage import dal
    from kb.semantic_index.indexer import index_files, reindex_all

    keep = make(db, content=section(1))
    drop = make(db, content=section(2))
    index_files([keep, drop])
    dal.delete_node(db, drop)  # soft delete without unindexing (the DAL records nothing)
    db.commit()
    assert chunks_of(drop)

    result = reindex_all()
    assert result.failed == []
    assert chunks_of(drop) == []
    assert chunks_of(keep)
    assert result.num_deleted >= 1


def test_unindex_files_returns_count(db, store):
    from kb.semantic_index.indexer import index_files, unindex_files

    fid = make(db, content="".join(section(i) for i in range(3)))
    n = index_files([fid]).num_added
    assert unindex_files([fid]) == n
    assert chunks_of(fid) == []
    assert unindex_files([fid]) == 0


def test_line_ranges_round_trip_through_read_lines(db, store):
    from kb import service
    from kb.semantic_index.chunking import chunk_text
    from kb.semantic_index.indexer import index_files

    content = "# Paper\n\nintro\n\n" + "".join(section(i, words=100) for i in range(6))
    fid = make(db, content=content, tags=["t1", "t2"], description="desc", aliases=["P"])
    m = service.create_manifest(db, f"idx-{uuid.uuid4().hex[:8]}")
    service.add_manifest_member(db, m.id, node_id=fid)
    db.commit()
    index_files([fid])

    rows = chunks_of(fid)
    assert len(rows) > 2
    for r in rows:
        out = service.read_lines(db, m.id, str(fid), offset=r.start_line, limit=r.end_line - r.start_line + 1)
        body = "\n".join(line.split("\t", 1)[1] for line in out.text.split("\n")[1:])
        doc = Document(page_content=r.content, metadata={"title": r.title, "heading": r.heading})
        assert chunk_text(doc) in body
