"""All-or-nothing writes (kb.service "Commit"): every commit indexes what it wrote
before it happens, and a failure anywhere -- embedding, S3, the DB, the commit itself --
leaves no rows, no chunks and no S3 objects behind. Fake embeddings and moto S3, through
the service interface (the API mapping is covered in test_semantic_search / test_ingest).
"""

import contextlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from test_ingest import make_pdf, write
from test_semantic_index import chunks_of, section, store  # noqa: F401 (store is a fixture)


@pytest.fixture
def db(migrated_db, store):  # noqa: F811
    from kb.storage.db import SessionLocal

    with SessionLocal() as s:
        yield s
        s.rollback()


def name(prefix="n"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def folder(db, parent_id=None):
    from kb import service

    return service.create_folder(db, parent_id=parent_id, title=name("f"))


def doc(db, parent_id, n_sections=2, content=None):
    from kb import service

    return service.create_file(
        db, parent_id=parent_id, title=name(), content=content or "".join(section(i) for i in range(n_sections))
    )


def records_of(file_id) -> int:
    from kb.storage.db import engine

    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM upsertion_record WHERE group_id = :g"), {"g": str(file_id)}
        ).scalar()


def exists(file_id) -> bool:
    from kb.storage.db import SessionLocal
    from kb.storage.models import File

    with SessionLocal() as s:
        return s.get(File, file_id) is not None


def objects(blob_store) -> list[str]:
    return [k for k, _ in blob_store.list_originals()]


@contextlib.contextmanager
def commit_fails_after_staging(target):
    """`target`'s DB commit fails after the index was staged: a listener that runs after
    kb.service's own before_commit (registered later, so it runs later) raises. Only for
    `target` -- LangChain's record manager commits its own sessions meanwhile."""

    def boom(session):
        if session is target:
            raise RuntimeError("connection lost during COMMIT")

    event.listen(Session, "before_commit", boom)
    try:
        yield
    finally:
        event.remove(Session, "before_commit", boom)


class CountingEmbeddings:
    """Counts embed_documents calls on the store's embeddings."""

    def __init__(self, store, monkeypatch):
        self.calls = 0
        cls = type(store.embeddings)  # a pydantic model: patch the class, not the instance
        real = cls.embed_documents

        def counted(embeddings, texts):
            self.calls += 1
            return real(embeddings, texts)

        monkeypatch.setattr(cls, "embed_documents", counted)


# --------------------------------------------------------------------------
# Successful writes
# --------------------------------------------------------------------------


def test_commit_indexes_before_it_returns(db):
    from kb import service

    f = folder(db)
    d = doc(db, f.id)
    assert service.touched_ids(db) == [f.id, d.id]

    result = service.commit(db)
    assert result.touched == [f.id, d.id]
    assert result.indexed.num_added == len(chunks_of(d.id)) > 0
    assert records_of(d.id) == len(chunks_of(d.id))
    assert service.touched_ids(db) == []


def test_plain_session_commit_is_just_as_atomic(db):
    d = doc(db, folder(db).id)
    db.commit()  # no kb.service.commit: the staging is a before_commit event
    assert chunks_of(d.id)


def test_edit_replaces_chunks_and_drops_stale_ones(db, store, monkeypatch):  # noqa: F811
    from kb import service

    d = doc(db, folder(db).id, n_sections=3)
    service.commit(db)
    service.update_node(db, d.id, content=section(0) + section(9))
    result = service.commit(db)
    texts = [r.content for r in chunks_of(d.id)]
    assert any("w9_" in t for t in texts) and not any("w1_" in t for t in texts)
    assert result.indexed.num_skipped > 0  # section 0 unchanged: not re-embedded
    assert records_of(d.id) == len(texts)

    service.update_node(db, d.id, tags=["a"])  # adds a frontmatter line: every chunk moves
    service.commit(db)
    counter = CountingEmbeddings(store, monkeypatch)
    service.update_node(db, d.id, tags=["b", "c"], status="stable")  # same line count: nothing to embed
    service.commit(db)
    assert counter.calls == 0


def test_created_then_edited_in_one_transaction_leaves_no_stale_chunks(db):
    from kb import service

    d = doc(db, folder(db).id, content=section(1))
    db.flush()
    service.stage(db, files=[d])  # e.g. an ingest staged it ...
    service.update_node(db, d.id, content=section(2))  # ... then it changed before the commit
    service.commit(db)
    texts = [r.content for r in chunks_of(d.id)]
    assert texts and all("w2_" in t for t in texts)


def test_folder_delete_unindexes_descendants(db):
    from kb import service

    top = folder(db)
    sub = folder(db, top.id)
    a, b = doc(db, top.id), doc(db, sub.id)
    service.commit(db)
    assert chunks_of(a.id) and chunks_of(b.id)

    service.delete_node(db, top.id)
    assert set(service.commit(db).touched) == {top.id, sub.id, a.id, b.id}
    assert chunks_of(a.id) == [] and chunks_of(b.id) == []

    service.restore_node(db, top.id)
    service.restore_node(db, sub.id)
    service.restore_node(db, b.id)
    service.commit(db)
    assert chunks_of(b.id) and chunks_of(a.id) == []  # restore is single-node


def test_move_does_not_touch_the_index(db):
    from kb import service

    src, dst = folder(db), folder(db)
    d = doc(db, src.id)
    service.commit(db)
    before = chunks_of(d.id)
    service.move_node(db, d.id, dst.id)
    assert service.commit(db).touched == []
    assert chunks_of(d.id) == before


def test_concurrent_crossing_folder_moves_cannot_make_a_cycle(db):
    """#17: A under B and B under A at once used to both pass the cycle check."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from kb import service
    from kb.storage.db import SessionLocal, engine
    from kb.storage.dal import TreeCycleError

    a, b = folder(db), folder(db)
    service.commit(db)
    service.move_node(db, a.id, b.id)  # holds the move lock until commit

    def move_b_under_a():
        with SessionLocal() as other:
            service.move_node(other, b.id, a.id)
            service.commit(other)

    with ThreadPoolExecutor(1) as pool:
        racing = pool.submit(move_b_under_a)
        with engine.connect() as conn:  # wait until the other move is blocked on the lock
            for _ in range(100):
                waiting = conn.execute(
                    text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
                ).scalar()
                if waiting:
                    break
                time.sleep(0.05)
        assert waiting, "the second move never waited on the lock"
        service.commit(db)
        with pytest.raises(TreeCycleError):
            racing.result(timeout=10)


def test_savepoint_rollback_keeps_outer_ids(db):
    from kb import service

    f = folder(db)
    with db.begin_nested() as sp:
        doc(db, f.id)
        sp.rollback()
    assert f.id in service.touched_ids(db)


def test_rollback_without_staging_does_nothing(db, monkeypatch):
    from kb.semantic_index import indexer

    calls = []
    monkeypatch.setattr(indexer, "reconcile", lambda ids, **kw: calls.append(ids))
    doc(db, folder(db).id)
    db.rollback()  # nothing was staged (no commit was attempted): nothing to undo
    assert calls == []


# --------------------------------------------------------------------------
# Failed writes leave nothing behind
# --------------------------------------------------------------------------


def test_failed_commit_undoes_staged_chunks(db):
    from kb import service

    d = doc(db, folder(db).id)
    with commit_fails_after_staging(db), pytest.raises(RuntimeError, match="COMMIT"):
        service.commit(db)
    assert not exists(d.id)
    assert chunks_of(d.id) == [] and records_of(d.id) == 0


def test_failed_edit_commit_restores_old_chunks_without_embedding(db, store, monkeypatch):  # noqa: F811
    from kb import service

    d = doc(db, folder(db).id, n_sections=3)
    service.commit(db)
    before = sorted(r.content for r in chunks_of(d.id))

    service.update_node(db, d.id, content=section(7) + section(8))
    counter = CountingEmbeddings(store, monkeypatch)
    with commit_fails_after_staging(db), pytest.raises(RuntimeError):
        service.commit(db)
    assert counter.calls == 1  # the staging embedded the new content; the undo embedded nothing
    assert sorted(r.content for r in chunks_of(d.id)) == before
    assert records_of(d.id) == len(before)


def test_indexing_failure_commits_nothing(db, monkeypatch):
    from kb import service
    from kb.semantic_index import indexer

    real = indexer.stage_documents

    def half_then_boom(docs, **kw):
        real(docs, **kw)  # chunks are in the index ...
        raise RuntimeError("embeddings server down")  # ... and then it fails

    monkeypatch.setattr(indexer, "stage_documents", half_then_boom)
    d = doc(db, folder(db).id)
    with pytest.raises(service.IndexingError, match="embeddings server down"):
        service.commit(db)
    assert not exists(d.id)
    assert chunks_of(d.id) == [] and records_of(d.id) == 0


def test_untracked_chunks_are_reconciled_away(db, store):  # noqa: F811
    # index() writes vectors before records: a failure in between leaves rows only
    # delete_untracked_chunks can see.
    from langchain_core.documents import Document

    from kb.semantic_index.indexer import reconcile

    ghost = uuid.uuid4()  # a file that never committed
    store.vector_store.add_documents(
        [Document(page_content="ghost", metadata={"file_id": str(ghost), "heading": None, "start_line": 1, "end_line": 1})]
    )
    assert len(chunks_of(ghost)) == 1 and records_of(ghost) == 0
    result = reconcile([ghost])
    assert chunks_of(ghost) == [] and result.num_deleted == 1


# --------------------------------------------------------------------------
# Ingest: rows + chunks + S3 originals, all or nothing
# --------------------------------------------------------------------------


@pytest.fixture
def papers(tmp_path):
    src = tmp_path / "papers"
    make_pdf(src / "one.pdf")
    make_pdf(src / "two.pdf")
    write(src / "notes.md", "# Notes\n\nplain markdown")
    return src


def planned_ids_from(monkeypatch):
    """Captures the ids ingest_folder plans, so a failed ingest can be checked by id."""
    from kb import service

    seen: list[uuid.UUID] = []
    real = service.stage

    def spy(session, *, files=(), originals=()):
        seen.extend(f.id for f in files)
        return real(session, files=files, originals=originals)

    monkeypatch.setattr(service, "stage", spy)
    return seen


def assert_nothing_left(ids, blob_store):
    assert ids
    for fid in ids:
        assert not exists(fid)
        assert chunks_of(fid) == [] and records_of(fid) == 0
    assert objects(blob_store) == []


def test_ingest_success_lands_everywhere(db, papers, blob_store):
    from kb import service
    from kb.ingest import ingest_folder

    report = ingest_folder(db, papers, blob_store=blob_store)
    result = service.commit(db)
    assert result.indexed.num_added == 0  # all embedded once, before the transaction

    assert len(report.files_created) == 3
    pdfs = [service.get_file(db, fid) for fid in report.files_created]
    pdfs = [n for n in pdfs if n.blob_key]
    assert len(pdfs) == 2
    assert sorted(objects(blob_store)) == sorted(n.blob_key for n in pdfs)
    for n in pdfs:
        assert n.blob_key == f"originals/{n.id}"
        assert blob_store.get_original(n.blob_key).read() == (papers / f"{n.title}.pdf").read_bytes()
    assert all(chunks_of(fid) for fid in report.files_created)


def test_ingest_embedding_failure(db, papers, blob_store, monkeypatch):
    from kb import service
    from kb.ingest import ingest_folder
    from kb.semantic_index import indexer

    ids = planned_ids_from(monkeypatch)
    monkeypatch.setattr(indexer, "stage_documents", lambda docs, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(service.IndexingError):
        ingest_folder(db, papers, blob_store=blob_store)
    db.rollback()
    assert_nothing_left(ids, blob_store)  # failed before any upload


def test_ingest_upload_failure_deletes_the_uploaded_ones(db, papers, blob_store, monkeypatch):
    from kb.ingest import ingest_folder
    from kb.storage.blobs import BlobStoreError

    ids = planned_ids_from(monkeypatch)
    real, puts = blob_store.put_original, []

    def second_fails(key, original):
        puts.append(key)
        if len(puts) == 2:
            raise BlobStoreError("bucket unreachable")
        real(key, original)

    monkeypatch.setattr(blob_store, "put_original", second_fails)
    with pytest.raises(BlobStoreError):
        ingest_folder(db, papers, blob_store=blob_store)
    db.rollback()
    assert len(puts) == 2
    assert_nothing_left(ids, blob_store)


def test_ingest_db_failure_after_staging(db, papers, blob_store, monkeypatch):
    from kb import service
    from kb.ingest import ingest_folder

    ids = planned_ids_from(monkeypatch)
    real, n = service.create_file, []

    def second_fails(*a, **kw):
        n.append(1)
        if len(n) == 2:
            raise service.FieldError("constraint violated")
        return real(*a, **kw)

    monkeypatch.setattr(service, "create_file", second_fails)
    with pytest.raises(service.FieldError):
        ingest_folder(db, papers, blob_store=blob_store)
    db.rollback()  # the caller's rollback settles: chunks and originals are undone
    assert_nothing_left(ids, blob_store)


def test_ingest_commit_failure(db, papers, blob_store, monkeypatch):
    from kb import service
    from kb.ingest import ingest_folder

    ids = planned_ids_from(monkeypatch)
    ingest_folder(db, papers, blob_store=blob_store)
    assert len(objects(blob_store)) == 2 and all(chunks_of(i) for i in ids)  # staged, not yet reachable
    with commit_fails_after_staging(db), pytest.raises(RuntimeError):
        service.commit(db)
    assert_nothing_left(ids, blob_store)


def test_ingest_dry_run_rollback_leaves_nothing(db, papers, blob_store, monkeypatch):
    from kb.ingest import ingest_folder

    ids = planned_ids_from(monkeypatch)
    ingest_folder(db, papers, blob_store=blob_store)
    db.rollback()
    assert_nothing_left(ids, blob_store)


def test_ingest_bad_file_does_nothing_at_all(db, papers, blob_store, monkeypatch):
    from kb.ingest import IngestFailed, ingest_folder

    write(papers / "broken.pdf", b"not a pdf")
    calls = []
    monkeypatch.setattr("kb.service.stage", lambda *a, **kw: calls.append(1))
    with pytest.raises(IngestFailed) as exc:
        ingest_folder(db, papers, blob_store=blob_store)
    assert [p.name for p, _ in exc.value.failures] == ["broken.pdf"]
    assert calls == [] and objects(blob_store) == []  # nothing embedded, nothing uploaded


# --------------------------------------------------------------------------
# Crash backstop (kb.maintenance / scripts/gc.py)
# --------------------------------------------------------------------------


def test_gc_originals(db, blob_store, tmp_path):
    from datetime import datetime, timezone

    from kb import service
    from kb.maintenance import gc_originals
    from kb.storage.blobs import Original, original_key

    pdf = Original.of(make_pdf(tmp_path / "x.pdf"))
    orphan, kept, deleted_row = original_key(uuid.uuid4()), None, None
    blob_store.put_original(orphan, pdf)
    f = folder(db)
    for attr in ("kept", "deleted_row"):
        fid = uuid.uuid4()
        service.create_file(db, parent_id=f.id, title=attr, content="x", id=fid, **pdf.columns(original_key(fid)))
        blob_store.put_original(original_key(fid), pdf)
        if attr == "deleted_row":
            service.delete_node(db, fid)  # soft-deleted: restore must still find its original
    service.commit(db)

    assert gc_originals(blob_store) == []  # everything is younger than the grace period
    later = datetime.now(timezone.utc) + timedelta(days=2)
    assert gc_originals(blob_store, dry_run=True, now=later) == [orphan]
    assert len(objects(blob_store)) == 3
    assert gc_originals(blob_store, now=later) == [orphan]
    assert len(objects(blob_store)) == 2 and orphan not in objects(blob_store)


def test_gc_chunks(db, store):  # noqa: F811
    from kb import service
    from kb.maintenance import gc_chunks

    d = doc(db, folder(db).id)
    db.flush()
    service.stage(db, files=[d])
    db.expunge_all()
    db.info.clear()  # a "crash": the transaction's end never settles the staging
    db.close()
    assert chunks_of(d.id) and not exists(d.id)

    ids, _ = gc_chunks(grace=timedelta(hours=1))
    assert d.id not in ids  # still within the grace period: could be an in-flight write
    ids, removed = gc_chunks(grace=timedelta(0))
    assert d.id in ids and removed >= 1
    assert chunks_of(d.id) == [] and records_of(d.id) == 0
