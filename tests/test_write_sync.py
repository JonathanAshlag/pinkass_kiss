"""kb.service.commit: writes record the ids they touch, and the commit brings the
semantic index in step (fake embeddings, through the service interface, not HTTP)."""

import uuid

import pytest

from test_semantic_index import chunks_of, section, store  # noqa: F401 (store is a fixture)


@pytest.fixture
def db(migrated_db, store, monkeypatch):  # noqa: F811
    from kb.storage.db import SessionLocal

    monkeypatch.setenv("KB_AUTO_INDEX", "1")
    with SessionLocal() as s:
        yield s
        s.rollback()


def name(prefix="n"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def folder(db, parent_id=None):
    from kb import service

    return service.create_folder(db, parent_id=parent_id, title=name("f"))


def doc(db, parent_id, n_sections=2):
    from kb import service

    return service.create_file(
        db, parent_id=parent_id, title=name(), content="".join(section(i) for i in range(n_sections))
    )


def test_create_and_commit_indexes(db):
    from kb import service

    f = folder(db)
    d = doc(db, f.id)
    assert service.touched_ids(db) == [f.id, d.id]

    result = service.commit(db)  # KB_AUTO_INDEX=1
    assert result.touched == [f.id, d.id]
    assert result.indexed.failed == []
    assert result.indexed.num_added == len(chunks_of(d.id)) > 0
    assert service.touched_ids(db) == []  # taken by the commit


def test_update_and_restore_reindex(db):
    from kb import service

    d = doc(db, folder(db).id)
    service.commit(db)
    service.update_node(db, d.id, content=section(9))
    assert service.commit(db).touched == [d.id]
    assert all("w9_" in r.content for r in chunks_of(d.id))

    service.delete_node(db, d.id)
    service.commit(db)
    assert chunks_of(d.id) == []
    service.restore_node(db, d.id)
    assert service.commit(db).touched == [d.id]
    assert chunks_of(d.id) != []


def test_folder_delete_unindexes_descendants(db):
    from kb import service

    top = folder(db)
    sub = folder(db, top.id)
    a, b = doc(db, top.id), doc(db, sub.id)
    service.commit(db)
    assert chunks_of(a.id) and chunks_of(b.id)

    service.delete_node(db, top.id)
    result = service.commit(db)
    assert set(result.touched) == {top.id, sub.id, a.id, b.id}
    assert result.indexed.num_deleted > 0
    assert chunks_of(a.id) == [] and chunks_of(b.id) == []


def test_rollback_records_nothing(db):
    from kb import service

    f = folder(db)
    doc(db, f.id)
    db.rollback()
    assert service.touched_ids(db) == []

    kept = doc(db, folder(db).id)
    db.commit()  # a plain commit drops the ids too (nothing indexed)
    assert service.touched_ids(db) == [] and chunks_of(kept.id) == []
    assert service.commit(db).touched == []


def test_savepoint_rollback_keeps_outer_ids(db):
    from kb import service

    f = folder(db)
    with db.begin_nested() as sp:
        doc(db, f.id)
        sp.rollback()
    assert f.id in service.touched_ids(db)


@pytest.mark.parametrize("env, index", [("0", None), ("1", False)])
def test_indexing_can_be_skipped(db, monkeypatch, env, index):
    from kb import service

    monkeypatch.setenv("KB_AUTO_INDEX", env)
    d = doc(db, folder(db).id)
    result = service.commit(db, index=index)
    assert d.id in result.touched and result.indexed is None
    assert chunks_of(d.id) == []


def test_index_true_overrides_env(db, monkeypatch):
    from kb import service

    monkeypatch.setenv("KB_AUTO_INDEX", "0")
    d = doc(db, folder(db).id)
    assert service.commit(db, index=True).indexed.num_added > 0
    assert chunks_of(d.id) != []


def test_move_does_not_touch_the_index(db, monkeypatch):
    from kb import service

    src, dst = folder(db), folder(db)
    d = doc(db, src.id)
    service.commit(db)
    before = chunks_of(d.id)

    calls = []
    monkeypatch.setattr(service, "index_files", lambda ids, **kw: calls.append(ids))
    service.move_node(db, d.id, dst.id)
    assert service.commit(db).touched == []
    assert calls == [] and chunks_of(d.id) == before


def test_schedule_defers_indexing_until_called(db):
    from kb import service

    d = doc(db, folder(db).id)
    scheduled = []
    result = service.commit(db, schedule=lambda fn, ids: scheduled.append((fn, ids)))
    assert result.indexed is None and chunks_of(d.id) == []

    [(fn, ids)] = scheduled
    assert ids == result.touched
    fn(ids)
    assert chunks_of(d.id) != []


def test_index_failure_is_logged_not_raised(db, monkeypatch, caplog):
    from kb import service

    def boom(ids, **kw):
        raise RuntimeError("embeddings server down")

    monkeypatch.setattr(service, "index_files", boom)
    # alembic's fileConfig (run by migrated_db) disables loggers that already existed
    monkeypatch.setattr(service.log, "disabled", False)
    d = doc(db, folder(db).id)
    result = service.commit(db)
    assert result.indexed is None
    assert "indexing failed" in caplog.text
    assert service.get_node(db, d.id) is not None  # the write itself stuck
