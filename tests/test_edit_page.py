"""Tests for the edit page flow (PUT /pages/{page_id})."""

import pytest

from app.models.page import PageCreate, PageUpdate, PageStatus
from app.models.user import User, PermissionLevel
from app.models.workflow import WorkflowCreate
from app.services.mutations import apply_page_mutation
from app.services.pages import create_page, get_page
from app.services.workflows import create_workflow
from app.models.request import RequestType


@pytest.fixture
async def editor(user_repo):
    user = User(user_id="editor1", name="Editor", permission_level=PermissionLevel.editor)
    await user_repo.create(user)
    return user


@pytest.fixture
async def editor_with_workflow(user_repo, wf_repo):
    wf = await create_workflow(
        WorkflowCreate(name="Two-step", steps=["approver1"]),
        "admin",
        wf_repo,
    )
    user = User(
        user_id="wf_editor", name="WF Editor",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(user)
    return user


@pytest.fixture
async def existing_page(editor, page_repo):
    return await create_page(
        PageCreate(title="Original Title", description="Original desc", content="Original content"),
        editor,
        page_repo,
    )


@pytest.mark.asyncio
async def test_edit_page_updates_title(editor, existing_page, page_repo, req_repo):
    result = await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(title="New Title"),
        page_id=existing_page.page_id,
    )
    assert result.status == "published"
    updated = await get_page(existing_page.page_id, page_repo)
    assert updated.title == "New Title"


@pytest.mark.asyncio
async def test_edit_page_updates_content(editor, existing_page, page_repo, req_repo):
    result = await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(content="Updated content"),
        page_id=existing_page.page_id,
    )
    assert result.status == "published"
    updated = await get_page(existing_page.page_id, page_repo)
    assert updated.content == "Updated content"


@pytest.mark.asyncio
async def test_edit_page_partial_update_preserves_unchanged_fields(editor, existing_page, page_repo, req_repo):
    await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(title="Only Title Changed"),
        page_id=existing_page.page_id,
    )
    updated = await get_page(existing_page.page_id, page_repo)
    assert updated.title == "Only Title Changed"
    assert updated.content == "Original content"
    assert updated.description == "Original desc"


@pytest.mark.asyncio
async def test_edit_page_with_workflow_creates_pending_request(editor_with_workflow, existing_page, page_repo, req_repo):
    result = await apply_page_mutation(
        RequestType.edit, editor_with_workflow, page_repo, req_repo,
        data=PageUpdate(title="Proposed Title"),
        page_id=existing_page.page_id,
    )
    assert result.status == "pending_approval"
    assert result.request_id
    # Original page should be unchanged
    original = await get_page(existing_page.page_id, page_repo)
    assert original.title == "Original Title"


@pytest.mark.asyncio
async def test_edit_page_records_history_entry(editor, existing_page, page_repo, req_repo):
    await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(content="Changed content"),
        page_id=existing_page.page_id,
    )
    updated = await get_page(existing_page.page_id, page_repo)
    actions = [h.action for h in updated.history]
    assert any("edit" in a.lower() for a in actions)


@pytest.mark.asyncio
async def test_edit_page_page_remains_published(editor, existing_page, page_repo, req_repo):
    result = await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(description="New description"),
        page_id=existing_page.page_id,
    )
    assert result.page["status"] == PageStatus.published
