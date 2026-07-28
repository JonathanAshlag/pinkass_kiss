"""Tests for workflow approval sequence."""

import pytest
import pytest_asyncio

from app.models.user import User, PermissionLevel
from app.models.page import PageCreate, PageStatus
from app.models.request import RequestType, RequestStatus, CreatePayload
from app.services.pages import create_page, get_page
from app.services.workflows import create_workflow
from app.services.requests import create_request, decide_request
from app.models.workflow import WorkflowCreate


@pytest_asyncio.fixture
async def workflow_setup(page_repo, user_repo, wf_repo, req_repo):
    """Set up users and a two-step workflow."""
    approver1 = User(user_id="approver1", name="Approver1", permission_level=PermissionLevel.editor)
    approver2 = User(user_id="approver2", name="Approver2", permission_level=PermissionLevel.editor)
    await user_repo.create(approver1)
    await user_repo.create(approver2)

    wf = await create_workflow(
        WorkflowCreate(name="Two-step", steps=["approver1", "approver2"]),
        "admin",
        wf_repo,
    )

    editor = User(
        user_id="editor1", name="Editor",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(editor)

    free_editor = User(
        user_id="free_editor", name="FreeEditor",
        permission_level=PermissionLevel.editor,
    )
    await user_repo.create(free_editor)

    return {"workflow": wf, "editor": editor, "free_editor": free_editor}


@pytest.mark.asyncio
async def test_no_workflow_auto_publish(workflow_setup, page_repo, user_repo, wf_repo, req_repo):
    free_editor = workflow_setup["free_editor"]
    page = await create_page(
        PageCreate(title="Direct Page", description="A direct page", content="Content"),
        free_editor,
        page_repo,
    )
    assert page.status == PageStatus.published


@pytest.mark.asyncio
async def test_workflow_creates_draft(workflow_setup, page_repo, user_repo, wf_repo, req_repo):
    editor = workflow_setup["editor"]
    page = await create_page(
        PageCreate(title="Draft Page", description="A draft page", content="Content"),
        editor,
        page_repo,
    )
    assert page.status == PageStatus.draft


@pytest.mark.asyncio
async def test_full_approval_chain(workflow_setup, page_repo, user_repo, wf_repo, req_repo):
    editor = workflow_setup["editor"]

    page = await create_page(
        PageCreate(title="Chain Page", description="A chain page", content="Hello"),
        editor,
        page_repo,
    )

    req = await create_request(
        RequestType.create, page.page_id, editor,
        req_repo=req_repo,
        page_repo=page_repo,
        proposed_content=CreatePayload(title="Chain Page", description="A chain page", content="Hello"),
    )

    result = await decide_request(
        req.request_id, "approver1", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
        comment="Looks good",
    )
    assert result.current_step == 1
    assert result.status == RequestStatus.pending

    result = await decide_request(
        req.request_id, "approver2", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
        comment="Final OK",
    )
    assert result.status == RequestStatus.approved

    page = await get_page(page.page_id, page_repo)
    assert page.status == PageStatus.published


@pytest.mark.asyncio
async def test_mid_chain_rejection(workflow_setup, page_repo, user_repo, wf_repo, req_repo):
    editor = workflow_setup["editor"]

    page = await create_page(
        PageCreate(title="Reject Page", description="A reject page", content="Bad content"),
        editor,
        page_repo,
    )

    req = await create_request(
        RequestType.create, page.page_id, editor,
        req_repo=req_repo,
        page_repo=page_repo,
        proposed_content=CreatePayload(title="Reject Page", description="A reject page", content="Bad content"),
    )

    result = await decide_request(
        req.request_id, "approver1", "reject",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
        comment="Not good",
    )
    assert result.status == RequestStatus.rejected

    result2 = await decide_request(
        req.request_id, "approver2", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )
    assert result2 is None

    page = await get_page(page.page_id, page_repo)
    assert page.status == PageStatus.rejected


@pytest.mark.asyncio
async def test_wrong_approver_cannot_decide(workflow_setup, page_repo, user_repo, wf_repo, req_repo):
    editor = workflow_setup["editor"]

    page = await create_page(
        PageCreate(title="Auth Page", description="An auth page", content="Content"),
        editor,
        page_repo,
    )

    req = await create_request(
        RequestType.create, page.page_id, editor,
        req_repo=req_repo,
        page_repo=page_repo,
        proposed_content=CreatePayload(title="Auth Page", description="An auth page", content="Content"),
    )

    result = await decide_request(
        req.request_id, "approver2", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )
    assert result is None

    result = await decide_request(
        req.request_id, "editor1", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )
    assert result is None
