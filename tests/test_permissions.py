"""Files/folders split + kb.policy enforcement through kb.service and the /nodes API."""

import uuid

import pytest


@pytest.fixture
def db_session(migrated_db):
    from kb.storage.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(migrated_db, monkeypatch):
    from fastapi.testclient import TestClient

    from kb.api import app

    return TestClient(app)


def name(prefix="n"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------
# Tree shape (kb.storage.dal via kb.service)
# --------------------------------------------------------------------------


def test_files_live_in_folders(db_session):
    from kb import service

    root = service.create_folder(db_session, parent_id=None, title=name())
    doc = service.create_file(db_session, parent_id=root.id, title="doc", content="x")
    assert (root.kind, doc.kind, root.agent_locked) == ("manual", "manual", False)
    assert service.get_node(db_session, doc.id) is doc and service.get_folder(db_session, root.id) is root

    with pytest.raises(service.FieldError):
        service.create_file(db_session, parent_id=None, title="orphan", content="x")
    with pytest.raises(service.FieldError):
        service.create_file(db_session, parent_id=doc.id, title="inside a file", content="x")
    with pytest.raises(service.FieldError):
        service.move_node(db_session, doc.id, None)  # files can't be roots
    with pytest.raises(service.FieldError):
        service.update_node(db_session, root.id, content="folders have none")


def test_children_and_descendants_span_both_tables(db_session):
    from kb import service

    root = service.create_folder(db_session, parent_id=None, title=name())
    sub = service.create_folder(db_session, parent_id=root.id, title="sub")
    a = service.create_file(db_session, parent_id=root.id, title="a", content="a")
    b = service.create_file(db_session, parent_id=sub.id, title="b", content="b")

    assert service.list_children(db_session, root.id) == [sub, a]  # folders first
    assert set(service.list_descendants(db_session, root.id)) == {sub, a, b}
    assert service.list_descendants(db_session, a.id) == []

    service.delete_node(db_session, root.id)
    assert all(n.deleted_at is not None for n in (root, sub, a, b))


def test_manifest_members_by_node_id(db_session):
    from kb import service

    root = service.create_folder(db_session, parent_id=None, title=name())
    a = service.create_file(db_session, parent_id=root.id, title="a", content="a")
    m = service.create_manifest(db_session, name("m"))
    service.add_manifest_member(db_session, m.id, node_id=root.id)
    (member,) = service.list_manifest_members(db_session, m.id)
    assert (member.folder_id, member.file_id, member.node_id) == (root.id, None, root.id)
    assert service.resolve_manifest(db_session, m.id) == {root, a}

    service.remove_manifest_member(db_session, m.id, node_id=root.id)
    service.add_manifest_member(db_session, m.id, node_id=a.id)
    assert service.resolve_manifest(db_session, m.id) == {a}


# --------------------------------------------------------------------------
# Enforcement (kb.policy via kb.service)
# --------------------------------------------------------------------------


def test_skeleton_is_unlocked_by_switching_kind(db_session):
    from kb import service

    skeleton = service.create_folder(db_session, parent_id=None, title=name(), kind="skeleton")
    other = service.create_folder(db_session, parent_id=None, title=name())

    # inside a skeleton: create and edit freely, even move/delete what's in it
    doc = service.create_file(db_session, parent_id=skeleton.id, title="doc", content="v1")
    service.update_node(db_session, doc.id, content="v2", title="renamed")
    service.update_node(db_session, skeleton.id, description="edited", tags=["t"])

    # the skeleton itself: no rename / move / delete
    for attempt in (
        lambda: service.update_node(db_session, skeleton.id, title="nope"),
        lambda: service.move_node(db_session, skeleton.id, other.id),
        lambda: service.delete_node(db_session, skeleton.id),
    ):
        with pytest.raises(service.PermissionDenied, match="skeleton"):
            attempt()
    # re-sending the current title isn't a rename
    service.update_node(db_session, skeleton.id, title=skeleton.title)

    service.update_node(db_session, skeleton.id, kind="manual")
    service.update_node(db_session, skeleton.id, title="now renamable")
    service.move_node(db_session, skeleton.id, other.id)
    service.delete_node(db_session, other.id)


def test_auto_updated_is_rejected_on_write(db_session):
    from kb import service

    root = service.create_folder(db_session, parent_id=None, title=name())
    doc = service.create_file(db_session, parent_id=root.id, title="doc", content="x")
    with pytest.raises(service.PermissionDenied, match="auto_updated"):
        service.create_folder(db_session, parent_id=None, title=name(), kind="auto_updated")
    with pytest.raises(service.PermissionDenied, match="auto_updated"):
        service.update_node(db_session, doc.id, kind="auto_updated")


def test_auto_updated_folder_is_locked(db_session):
    """auto_updated can't be set through the service, so plant one with the dal (as a
    future job would) and check nothing can touch it or move into it."""
    from kb import service
    from kb.storage import dal

    auto = dal.create_folder(db_session, parent_id=None, title=name(), kind="auto_updated")
    inside = dal.create_file(db_session, parent_id=auto.id, title="generated", content="x")
    elsewhere = service.create_folder(db_session, parent_id=None, title=name())
    doc = service.create_file(db_session, parent_id=elsewhere.id, title="doc", content="x")

    for attempt in (
        lambda: service.create_file(db_session, parent_id=auto.id, title="new", content="x"),
        lambda: service.move_node(db_session, doc.id, auto.id),
        lambda: service.update_node(db_session, inside.id, content="edited"),
        lambda: service.delete_node(db_session, inside.id),
        lambda: service.update_node(db_session, auto.id, kind="manual"),
    ):
        with pytest.raises(service.PermissionDenied):
            attempt()


def test_agent_lock(db_session):
    from kb import service

    root = service.create_folder(db_session, parent_id=None, title=name())
    locked = service.create_folder(db_session, parent_id=root.id, title="ops", agent_locked=True)
    doc = service.create_file(db_session, parent_id=locked.id, title="runbook", content="x")

    service.update_node(db_session, doc.id, content="human edit")  # humans unaffected
    with pytest.raises(service.PermissionDenied, match="locked for agents"):
        service.update_node(db_session, doc.id, content="agent edit", actor="agent")
    with pytest.raises(service.PermissionDenied, match="locked for agents"):
        service.create_file(db_session, parent_id=locked.id, title="x", content="x", actor="agent")
    with pytest.raises(service.PermissionDenied, match="locked for agents"):
        service.move_node(db_session, doc.id, root.id, actor="agent")
    with pytest.raises(service.PermissionDenied, match="agents cannot"):
        service.update_node(db_session, root.id, agent_locked=True, actor="agent")

    service.create_file(db_session, parent_id=root.id, title="fine", content="x", actor="agent")

    # not recursive: a sub-folder of the locked folder, and what's in it, stay open
    sub = service.create_folder(db_session, parent_id=locked.id, title="drafts")
    deep = service.create_file(db_session, parent_id=sub.id, title="note", content="x", actor="agent")
    service.update_node(db_session, deep.id, content="agent edit", actor="agent")
    service.update_node(db_session, sub.id, title="renamed", actor="agent")


# --------------------------------------------------------------------------
# /nodes API
# --------------------------------------------------------------------------


def test_nodes_api_golden_path(client):
    r = client.post("/nodes", json={"type": "folder", "title": name("api"), "kind": "skeleton"})
    assert r.status_code == 201, r.text
    folder = r.json()
    assert (folder["type"], folder["kind"], folder["agent_locked"], folder["content"]) == ("folder", "skeleton", False, None)

    r = client.post("/nodes", json={"parent_id": folder["id"], "title": "doc", "content": "v1"})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert (doc["type"], doc["kind"], doc["status"]) == ("file", "manual", "draft")

    assert client.patch(f"/nodes/{doc['id']}", json={"content": "v2"}).status_code == 200
    assert client.patch(f"/nodes/{folder['id']}", json={"title": "x"}).status_code == 403
    assert client.delete(f"/nodes/{folder['id']}").status_code == 403
    assert client.patch(f"/nodes/{folder['id']}", json={"kind": "auto_updated"}).status_code == 403
    assert client.patch(f"/nodes/{folder['id']}", json={"kind": "manual"}).status_code == 200
    assert client.patch(f"/nodes/{folder['id']}", json={"title": name("renamed")}).status_code == 200

    children = client.get(f"/nodes/{folder['id']}/children").json()
    assert [(c["id"], c["type"]) for c in children] == [(doc["id"], "file")]
    folders = client.get("/nodes", params={"type": "folder", "kind": "manual"}).json()
    assert folder["id"] in {f["id"] for f in folders}

    assert client.delete(f"/nodes/{folder['id']}").status_code == 204


def test_nodes_api_shape_errors(client):
    folder = client.post("/nodes", json={"type": "folder", "title": name("api")}).json()
    doc = client.post("/nodes", json={"parent_id": folder["id"], "title": "d", "content": "x"}).json()

    assert client.post("/nodes", json={"title": "root file", "content": "x"}).status_code == 422
    assert client.post("/nodes", json={"parent_id": folder["id"], "title": "no content"}).status_code == 422
    assert client.post("/nodes", json={"type": "folder", "title": "f", "content": "x"}).status_code == 422
    assert client.post("/nodes", json={"parent_id": doc["id"], "title": "in a file", "content": "x"}).status_code == 422
    assert client.patch(f"/nodes/{folder['id']}", json={"status": "stable"}).status_code == 422
    assert client.post(f"/nodes/{doc['id']}/move", json={"new_parent_id": None}).status_code == 422
    assert client.post("/nodes", json={"parent_id": str(uuid.uuid4()), "title": "x", "content": "x"}).status_code == 404


def test_restore_needs_an_active_parent(db_session):
    """Restoring into a soft-deleted folder used to skip the permission check (the parent
    lookup ignored deleted rows), so an agent could restore into an agent-locked folder."""
    from kb import service

    root = service.create_folder(db_session, parent_id=None, title=name())
    locked = service.create_folder(db_session, parent_id=root.id, title="locked", agent_locked=True)
    doc = service.create_file(db_session, parent_id=locked.id, title="doc", content="x")
    service.delete_node(db_session, locked.id)

    for actor in ("agent", "human"):
        with pytest.raises(service.FieldError):
            service.restore_node(db_session, doc.id, actor=actor)

    service.restore_node(db_session, locked.id)
    with pytest.raises(service.PermissionDenied):
        service.restore_node(db_session, doc.id, actor="agent")
    assert service.restore_node(db_session, doc.id).deleted_at is None
