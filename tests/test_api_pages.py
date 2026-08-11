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
from app.IP.tags import ALLOWED_TAGS


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
    assert body["page"]["page_id"] == "Hello"


async def test_create_page_with_duplicate_title_returns_409(client, editor):
    first = await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Dup Title", "description": "Desc", "content": "Body"},
    )
    assert first.status_code == 200

    second = await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Dup Title", "description": "Other", "content": "Other body"},
    )
    assert second.status_code == 409


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
# Request verification
# ---------------------------------------------------------------------------

@pytest.fixture
async def admin(user_repo):
    u = User(user_id="admin1", name="Admin", permission_level=PermissionLevel.admin)
    await user_repo.create(u)
    return u


async def test_request_verification_with_workflow_returns_pending(client, user_repo, wf_repo, editor):
    wf = await create_workflow(WorkflowCreate(name="v-wf", steps=["approver"]), "admin", wf_repo)
    wf_editor = User(
        user_id="wf_ed2", name="WF Editor 2",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(wf_editor)

    page_id = (await client.post(
        "/pages",
        headers={"X-User-Id": wf_editor.user_id},
        json={"title": "To Verify", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    resp = await client.post(
        f"/pages/{page_id}/request-verification",
        headers={"X-User-Id": wf_editor.user_id},
    )
    assert resp.status_code == 200
    assert resp.json()["type"] == "review"
    assert resp.json()["status"] == "pending"


async def test_request_verification_editor_without_workflow_returns_403(client, editor):
    page_id = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "No WF Page", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    resp = await client.post(
        f"/pages/{page_id}/request-verification",
        headers={"X-User-Id": editor.user_id},
    )
    assert resp.status_code == 403


async def test_request_verification_admin_without_workflow_self_provisions(client, admin):
    page_id = (await client.post(
        "/pages",
        headers={"X-User-Id": admin.user_id},
        json={"title": "Admin Page", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    resp = await client.post(
        f"/pages/{page_id}/request-verification",
        headers={"X-User-Id": admin.user_id},
    )
    assert resp.status_code == 200
    req = resp.json()
    assert req["type"] == "review"

    decide = await client.post(
        f"/approvals/{req['request_id']}/decide",
        headers={"X-User-Id": admin.user_id},
        json={"decision": "approve"},
    )
    assert decide.status_code == 200
    assert decide.json()["status"] == "approved"

    page = await client.get(f"/pages/{page_id}", headers={"X-User-Id": admin.user_id})
    assert page.json()["trust_tier"] == "verified"


async def test_request_verification_already_verified_returns_400(client, admin):
    page_id = (await client.post(
        "/pages",
        headers={"X-User-Id": admin.user_id},
        json={"title": "Twice Verified", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    first = await client.post(
        f"/pages/{page_id}/request-verification",
        headers={"X-User-Id": admin.user_id},
    )
    await client.post(
        f"/approvals/{first.json()['request_id']}/decide",
        headers={"X-User-Id": admin.user_id},
        json={"decision": "approve"},
    )

    second = await client.post(
        f"/pages/{page_id}/request-verification",
        headers={"X-User-Id": admin.user_id},
    )
    assert second.status_code == 400


async def test_editing_verified_page_without_workflow_returns_403(client, admin, editor):
    page_id = (await client.post(
        "/pages",
        headers={"X-User-Id": admin.user_id},
        json={"title": "Locked Down", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    verify_req = (await client.post(
        f"/pages/{page_id}/request-verification",
        headers={"X-User-Id": admin.user_id},
    )).json()
    await client.post(
        f"/approvals/{verify_req['request_id']}/decide",
        headers={"X-User-Id": admin.user_id},
        json={"decision": "approve"},
    )

    resp = await client.put(
        f"/pages/{page_id}",
        headers={"X-User-Id": editor.user_id},
        json={"content": "Sneaky edit"},
    )
    assert resp.status_code == 403


async def test_editing_verified_page_creates_pending_request_and_repins_on_approval(client, admin):
    page_id = (await client.post(
        "/pages",
        headers={"X-User-Id": admin.user_id},
        json={"title": "Verified And Editable", "description": "D", "content": "Before"},
    )).json()["page"]["page_id"]

    verify_req = (await client.post(
        f"/pages/{page_id}/request-verification",
        headers={"X-User-Id": admin.user_id},
    )).json()
    await client.post(
        f"/approvals/{verify_req['request_id']}/decide",
        headers={"X-User-Id": admin.user_id},
        json={"decision": "approve"},
    )

    resp = await client.put(
        f"/pages/{page_id}",
        headers={"X-User-Id": admin.user_id},
        json={"content": "After"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_approval"

    unchanged = await client.get(f"/pages/{page_id}", headers={"X-User-Id": admin.user_id})
    assert unchanged.json()["content"] == "Before"

    decide = await client.post(
        f"/approvals/{resp.json()['request_id']}/decide",
        headers={"X-User-Id": admin.user_id},
        json={"decision": "approve"},
    )
    assert decide.status_code == 200

    final = await client.get(f"/pages/{page_id}", headers={"X-User-Id": admin.user_id})
    assert final.json()["content"] == "After"
    assert final.json()["trust_tier"] == "verified"
    assert final.json()["trust_is_stale"] is False


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


async def test_search_matches_alias(client, editor):
    await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={
            "title": "Canine Overview",
            "description": "D",
            "content": "C",
            "aliases": ["Canis lupus familiaris"],
        },
    )
    resp = await client.get(
        "/pages/search",
        params={"query": "Canis"},
        headers={"X-User-Id": editor.user_id},
    )
    assert resp.status_code == 200
    assert any(p["title"] == "Canine Overview" for p in resp.json())


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

async def test_create_and_update_aliases_round_trip(client, editor):
    create = await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Aliased", "description": "D", "content": "C", "aliases": ["Alpha", "Beta"]},
    )
    page_id = create.json()["page"]["page_id"]

    resp = await client.get(f"/pages/{page_id}", headers={"X-User-Id": editor.user_id})
    assert resp.json()["aliases"] == ["Alpha", "Beta"]

    await client.put(
        f"/pages/{page_id}",
        headers={"X-User-Id": editor.user_id},
        json={"aliases": ["Gamma"]},
    )
    resp = await client.get(f"/pages/{page_id}", headers={"X-User-Id": editor.user_id})
    assert resp.json()["aliases"] == ["Gamma"]


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

async def test_create_page_with_valid_tags(client, editor):
    tag_a, tag_b = ALLOWED_TAGS[0], ALLOWED_TAGS[1]
    create = await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Tagged", "description": "D", "content": "C", "tags": [tag_a, tag_b]},
    )
    page_id = create.json()["page"]["page_id"]

    resp = await client.get(f"/pages/{page_id}", headers={"X-User-Id": editor.user_id})
    assert resp.json()["tags"] == [tag_a, tag_b]

    tag_c = ALLOWED_TAGS[2]
    await client.put(
        f"/pages/{page_id}",
        headers={"X-User-Id": editor.user_id},
        json={"tags": [tag_c]},
    )
    resp = await client.get(f"/pages/{page_id}", headers={"X-User-Id": editor.user_id})
    assert resp.json()["tags"] == [tag_c]


async def test_create_page_rejects_unknown_tag(client, editor):
    resp = await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Bad Tag", "description": "D", "content": "C", "tags": ["not-a-real-tag"]},
    )
    assert resp.status_code == 422


async def test_search_filters_by_tags(client, editor):
    tag_match, tag_other = ALLOWED_TAGS[0], ALLOWED_TAGS[1]
    await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Tag Match Page", "description": "D", "content": "C", "tags": [tag_match]},
    )
    await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Tag Match Other", "description": "D", "content": "C", "tags": [tag_other]},
    )
    resp = await client.get(
        "/pages/search",
        params={"query": "Tag Match", "tags": [tag_match]},
        headers={"X-User-Id": editor.user_id},
    )
    assert resp.status_code == 200
    titles = [p["title"] for p in resp.json()]
    assert "Tag Match Page" in titles
    assert "Tag Match Other" not in titles


async def test_list_allowed_tags_endpoint(client, editor):
    resp = await client.get("/pages/tags", headers={"X-User-Id": editor.user_id})
    assert resp.status_code == 200
    assert resp.json()["tags"] == ALLOWED_TAGS


# ---------------------------------------------------------------------------
# Tag inheritance
# ---------------------------------------------------------------------------

@pytest.fixture
async def approver(user_repo):
    u = User(user_id="approver1", name="Approver", permission_level=PermissionLevel.editor)
    await user_repo.create(u)
    return u


@pytest.fixture
async def wf_editor(user_repo, wf_repo, approver):
    wf = await create_workflow(WorkflowCreate(name="one-step", steps=[approver.user_id]), "admin", wf_repo)
    u = User(
        user_id="wf_editor1", name="WF Editor",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(u)
    return u


async def test_create_child_inherits_parent_tags(client, editor):
    tag_a, tag_b = ALLOWED_TAGS[0], ALLOWED_TAGS[1]
    parent = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Parent", "description": "D", "content": "C", "tags": [tag_a]},
    )).json()["page"]["page_id"]

    child = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Child", "description": "D", "content": "C", "parent_id": parent, "tags": [tag_b]},
    )).json()["page"]["page_id"]

    resp = await client.get(f"/pages/{child}", headers={"X-User-Id": editor.user_id})
    assert sorted(resp.json()["tags"]) == sorted({tag_a, tag_b})


async def test_reparent_only_merges_new_parent_tags(client, editor):
    tag_a, tag_b = ALLOWED_TAGS[0], ALLOWED_TAGS[1]
    parent = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Parent2", "description": "D", "content": "C", "tags": [tag_a]},
    )).json()["page"]["page_id"]

    child = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Child2", "description": "D", "content": "C", "tags": [tag_b]},
    )).json()["page"]["page_id"]

    await client.put(
        f"/pages/{child}",
        headers={"X-User-Id": editor.user_id},
        json={"parent_id": parent},
    )
    resp = await client.get(f"/pages/{child}", headers={"X-User-Id": editor.user_id})
    assert sorted(resp.json()["tags"]) == sorted({tag_a, tag_b})


async def test_editing_parent_tags_does_not_cascade_to_existing_children(client, editor):
    tag_a, tag_c = ALLOWED_TAGS[0], ALLOWED_TAGS[2]
    parent = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Parent3", "description": "D", "content": "C", "tags": [tag_a]},
    )).json()["page"]["page_id"]

    child = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "Child3", "description": "D", "content": "C", "parent_id": parent},
    )).json()["page"]["page_id"]

    resp = await client.get(f"/pages/{child}", headers={"X-User-Id": editor.user_id})
    assert resp.json()["tags"] == [tag_a]

    await client.put(
        f"/pages/{parent}",
        headers={"X-User-Id": editor.user_id},
        json={"tags": [tag_a, tag_c]},
    )
    resp = await client.get(f"/pages/{child}", headers={"X-User-Id": editor.user_id})
    assert resp.json()["tags"] == [tag_a]


async def test_workflow_edit_merges_tags_and_reparent_at_approval(client, editor, wf_editor, approver):
    tag_a, tag_b = ALLOWED_TAGS[0], ALLOWED_TAGS[1]
    parent = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "WFParent", "description": "D", "content": "C", "tags": [tag_a]},
    )).json()["page"]["page_id"]

    child = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "WFChild", "description": "D", "content": "C"},
    )).json()["page"]["page_id"]

    edit_resp = await client.put(
        f"/pages/{child}",
        headers={"X-User-Id": wf_editor.user_id},
        json={"parent_id": parent, "tags": [tag_b]},
    )
    assert edit_resp.json()["status"] == "pending_approval"
    request_id = edit_resp.json()["request_id"]

    decide = await client.post(
        f"/approvals/{request_id}/decide",
        headers={"X-User-Id": approver.user_id},
        json={"decision": "approve"},
    )
    assert decide.status_code == 200

    resp = await client.get(f"/pages/{child}", headers={"X-User-Id": editor.user_id})
    assert resp.json()["parent_id"] == parent
    assert sorted(resp.json()["tags"]) == sorted({tag_a, tag_b})


async def test_workflow_create_unaffected_by_pending_parent_tag_edit(client, editor, wf_editor, approver):
    tag_a, tag_b, tag_c = ALLOWED_TAGS[0], ALLOWED_TAGS[1], ALLOWED_TAGS[2]
    parent = (await client.post(
        "/pages",
        headers={"X-User-Id": editor.user_id},
        json={"title": "WFParent2", "description": "D", "content": "C", "tags": [tag_a]},
    )).json()["page"]["page_id"]

    create_resp = await client.post(
        "/pages",
        headers={"X-User-Id": wf_editor.user_id},
        json={"title": "WFChild2", "description": "D", "content": "C", "parent_id": parent, "tags": [tag_b]},
    )
    assert create_resp.json()["status"] == "pending_approval"
    request_id = create_resp.json()["request_id"]
    child = create_resp.json()["page"]["page_id"]

    # Parent's tags change while the create request is still pending — must not leak in on approval.
    await client.put(
        f"/pages/{parent}",
        headers={"X-User-Id": editor.user_id},
        json={"tags": [tag_a, tag_c]},
    )

    decide = await client.post(
        f"/approvals/{request_id}/decide",
        headers={"X-User-Id": approver.user_id},
        json={"decision": "approve"},
    )
    assert decide.status_code == 200

    resp = await client.get(f"/pages/{child}", headers={"X-User-Id": editor.user_id})
    assert sorted(resp.json()["tags"]) == sorted({tag_a, tag_b})


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
