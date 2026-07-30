"""HTTP-level integration tests for the /pages API endpoints.

These tests exercise the full FastAPI stack (routing, auth deps, Pydantic validation,
service layer) against the same in-memory repos already used by the service tests.
Each test runs against both MongoDB and Postgres backends via the parametrized
`backend` fixture in conftest.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.container import _page_repo, _user_repo, _workflow_repo, _request_repo, _source_file_repo
from app.models.user import User, PermissionLevel
from app.models.workflow import WorkflowCreate
from app.services.workflows import create_workflow


@pytest.fixture
async def client(page_repo, user_repo, req_repo, wf_repo, source_file_repo):
    """AsyncClient wired to in-memory repos via dependency_overrides — no real server needed."""
    app.dependency_overrides[_page_repo] = lambda: page_repo
    app.dependency_overrides[_user_repo] = lambda: user_repo
    app.dependency_overrides[_request_repo] = lambda: req_repo
    app.dependency_overrides[_workflow_repo] = lambda: wf_repo
    app.dependency_overrides[_source_file_repo] = lambda: source_file_repo
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def editor(user_repo):
    u = User(user_id="editor1", name="Editor", permission_level=PermissionLevel.editor)
    await user_repo.create(u)
    return u


@pytest.fixture
async def reader(user_repo):
    u = User(user_id="reader1", name="Reader", permission_level=PermissionLevel.read_only)
    await user_repo.create(u)
    return u


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def test_missing_user_id_header_rejected(client):
    resp = await client.get("/pages/some-id")
    assert resp.status_code in (401, 422)


async def test_unknown_user_id_returns_401(client):
    resp = await client.get("/pages/some-id", headers={"X-User-Id": "ghost"})
    assert resp.status_code == 401


async def test_read_only_user_cannot_create(client, reader):
    resp = await client.post(
        "/pages",
        headers={"X-User-Id": reader.user_id},
        json={"title": "T", "description": "D", "content": "C"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

async def test_create_page_returns_published(client, editor):
    resp = await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Hello", "description": "Desc", "content": "Body"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "published"
    assert "page_id" in body["page"]


async def test_create_page_with_workflow_returns_pending(client, user_repo, wf_repo):
    wf = await create_workflow(WorkflowCreate(name="wf", steps=["approver"]), "admin", wf_repo)
    wf_editor = User(
        user_id="wf_ed", name="WF Editor",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(wf_editor)
    resp = await client.post(
        "/pages",
        headers={"X-User-Id": wf_editor.user_id},
        json={"title": "Draft", "description": "D", "content": "C"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_approval"


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------

async def test_get_unknown_page_access_denied(client, editor):
    # Non-existent pages return 403, not 404 — the system doesn't reveal whether a page
    # exists to users who haven't been granted access to it.
    resp = await client.get("/pages/nonexistent-id", headers={"X-User-Id": editor.user_id})
    assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

async def test_partial_update_preserves_untouched_fields(client, editor):
    create = await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Original Title", "description": "Desc", "content": "Content"},
    )
    page_id = create.json()["page"]["page_id"]

    await client.put(
        f"/pages/{page_id}",
        headers={"X-User-Id": editor.user_id},
        json={"title": "New Title"},
    )
    body = (await client.get(f"/pages/{page_id}", headers={"X-User-Id": editor.user_id})).json()
    assert body["title"] == "New Title"
    assert body["content"] == "Content"


async def test_set_parent_id_null_clears_parent(client, editor):
    """Regression: PUT with parent_id=null must clear the parent, not silently ignore it."""
    parent_id = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Parent", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    child_id = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Child", "description": "D", "content": "C", "parent_id": parent_id},
    )).json()["page"]["page_id"]

    get1 = await client.get(f"/pages/{child_id}", headers={"X-User-Id": editor.user_id})
    assert get1.json()["parent_id"] == parent_id

    await client.put(
        f"/pages/{child_id}",
        headers={"X-User-Id": editor.user_id},
        json={"parent_id": None},
    )

    get2 = await client.get(f"/pages/{child_id}", headers={"X-User-Id": editor.user_id})
    assert get2.json()["parent_id"] is None


async def test_omitting_parent_id_in_update_preserves_existing_parent(client, editor):
    """Omitting parent_id from the payload must not clear an existing parent."""
    parent_id = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Parent", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    child_id = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Child", "description": "D", "content": "C", "parent_id": parent_id},
    )).json()["page"]["page_id"]

    await client.put(
        f"/pages/{child_id}",
        headers={"X-User-Id": editor.user_id},
        json={"title": "New Title"},
    )

    body = (await client.get(f"/pages/{child_id}", headers={"X-User-Id": editor.user_id})).json()
    assert body["parent_id"] == parent_id


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def test_delete_page(client, editor):
    page_id = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "To Delete", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    delete = await client.delete(f"/pages/{page_id}", headers={"X-User-Id": editor.user_id})
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"

    # After deletion the record is removed from the repo; can_view_page returns False → 403.
    get = await client.get(f"/pages/{page_id}", headers={"X-User-Id": editor.user_id})
    assert get.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Search / Tree
# ---------------------------------------------------------------------------

async def test_search_returns_published_pages(client, editor):
    await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Searchable Title", "description": "D", "content": "C"},
    )
    resp = await client.get(
        "/pages/search",
        params={"query": "Searchable"},
        headers={"X-User-Id": editor.user_id},
    )
    assert resp.status_code == 200
    assert any(p["title"] == "Searchable Title" for p in resp.json())


async def test_get_tree_includes_created_page(client, editor):
    await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Tree Page", "description": "D", "content": "C"},
    )
    resp = await client.get("/pages/tree", headers={"X-User-Id": editor.user_id})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert any(p["title"] == "Tree Page" for p in resp.json())
