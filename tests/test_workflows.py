"""Tests for workflow approval sequence."""

import pytest
import pytest_asyncio

from app.models.user import User, PermissionLevel
from app.models.page import PageCreate, PageStatus
from app.models.request import RequestType, RequestStatus
from app.services.pages import create_page, get_page
from app.services.workflows import create_workflow
from app.services.requests import create_request, decide_request
from app.models.workflow import WorkflowCreate


@pytest_asyncio.fixture
async def workflow_setup(mock_db):
    """Set up users and a two-step workflow."""
    # Create approvers
    approver1 = User(user_id="approver1", name="Approver1", permission_level=PermissionLevel.editor)
    approver2 = User(user_id="approver2", name="Approver2", permission_level=PermissionLevel.editor)
    await mock_db.users.insert_one(approver1.model_dump(mode="json"))
    await mock_db.users.insert_one(approver2.model_dump(mode="json"))

    # Create workflow
    wf = await create_workflow(
        WorkflowCreate(name="Two-step", steps=["approver1", "approver2"]),
        "admin"
    )

    # Create editor with workflow
    editor = User(
        user_id="editor1", name="Editor",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await mock_db.users.insert_one(editor.model_dump(mode="json"))

    # Create editor without workflow
    free_editor = User(
        user_id="free_editor", name="FreeEditor",
        permission_level=PermissionLevel.editor,
    )
    await mock_db.users.insert_one(free_editor.model_dump(mode="json"))

    return {"workflow": wf, "editor": editor, "free_editor": free_editor}


@pytest.mark.asyncio
async def test_no_workflow_auto_publish(mock_db, workflow_setup):
    """Editor without workflow publishes directly."""
    free_editor = workflow_setup["free_editor"]
    page = await create_page(
        PageCreate(title="Direct Page", content="Content"),
        free_editor
    )
    assert page.status == PageStatus.published


@pytest.mark.asyncio
async def test_workflow_creates_draft(mock_db, workflow_setup):
    """Editor with workflow creates draft, not published."""
    editor = workflow_setup["editor"]
    page = await create_page(
        PageCreate(title="Draft Page", content="Content"),
        editor
    )
    assert page.status == PageStatus.draft


@pytest.mark.asyncio
async def test_full_approval_chain(mock_db, workflow_setup):
    """Two approvals in sequence publish the page."""
    editor = workflow_setup["editor"]
    wf = workflow_setup["workflow"]

    page = await create_page(
        PageCreate(title="Chain Page", content="Hello"),
        editor
    )

    req = await create_request(
        RequestType.create, page.page_id, editor,
        proposed_content={"title": "Chain Page", "content": "Hello"},
    )

    # Step 1: approver1 approves
    result = await decide_request(req.request_id, "approver1", "approve", "Looks good")
    assert result.current_step == 1
    assert result.status == RequestStatus.pending

    # Step 2: approver2 approves
    result = await decide_request(req.request_id, "approver2", "approve", "Final OK")
    assert result.status == RequestStatus.approved

    # Page should be published
    page = await get_page(page.page_id)
    assert page.status == PageStatus.published


@pytest.mark.asyncio
async def test_mid_chain_rejection(mock_db, workflow_setup):
    """Rejection at step 1 rejects the entire request."""
    editor = workflow_setup["editor"]
    wf = workflow_setup["workflow"]

    page = await create_page(
        PageCreate(title="Reject Page", content="Bad content"),
        editor
    )

    req = await create_request(
        RequestType.create, page.page_id, editor,
        proposed_content={"title": "Reject Page", "content": "Bad content"},
    )

    # Step 1: approver1 rejects
    result = await decide_request(req.request_id, "approver1", "reject", "Not good")
    assert result.status == RequestStatus.rejected

    # approver2 cannot decide anymore
    result2 = await decide_request(req.request_id, "approver2", "approve")
    assert result2 is None

    # Page should be rejected
    page = await get_page(page.page_id)
    assert page.status == PageStatus.rejected


@pytest.mark.asyncio
async def test_wrong_approver_cannot_decide(mock_db, workflow_setup):
    """Only the current step's approver can decide."""
    editor = workflow_setup["editor"]

    page = await create_page(
        PageCreate(title="Auth Page", content="Content"),
        editor
    )

    req = await create_request(
        RequestType.create, page.page_id, editor,
        proposed_content={"title": "Auth Page", "content": "Content"},
    )

    # approver2 tries to decide at step 0 (should fail)
    result = await decide_request(req.request_id, "approver2", "approve")
    assert result is None

    # editor tries to decide (should fail)
    result = await decide_request(req.request_id, "editor1", "approve")
    assert result is None
