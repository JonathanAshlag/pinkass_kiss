"""Tests for the "request verification" flow (review request with trust_tier=verified)."""

import pytest
import pytest_asyncio

from app.models.page import PageCreate, TrustTier
from app.models.request import RequestStatus
from app.models.user import User, PermissionLevel
from app.services.pages import create_page, get_page
from app.services.requests import decide_request, request_page_verification
from app.services.workflows import create_workflow
from app.models.workflow import WorkflowCreate


@pytest_asyncio.fixture
async def verification_setup(page_repo, user_repo, wf_repo, req_repo):
    """A published page, plus an editor with a workflow, an admin with none, and a plain editor with none."""
    approver = User(user_id="v_approver", name="Approver", permission_level=PermissionLevel.editor)
    await user_repo.create(approver)

    wf = await create_workflow(
        WorkflowCreate(name="Verification workflow", steps=["v_approver"]),
        "admin",
        wf_repo,
    )

    editor = User(
        user_id="v_editor", name="Editor",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(editor)

    free_admin = User(user_id="v_admin", name="Admin", permission_level=PermissionLevel.admin)
    await user_repo.create(free_admin)

    free_editor = User(user_id="v_free_editor", name="FreeEditor", permission_level=PermissionLevel.editor)
    await user_repo.create(free_editor)

    page = await create_page(
        PageCreate(title="Verify Me", description="desc", content="Some content"),
        editor,
        page_repo,
    )

    return {
        "workflow": wf,
        "editor": editor,
        "free_admin": free_admin,
        "free_editor": free_editor,
        "page": page,
    }


@pytest.mark.asyncio
async def test_request_and_approve_verification(verification_setup, page_repo, req_repo, wf_repo, user_repo):
    editor = verification_setup["editor"]
    page = verification_setup["page"]

    req = await request_page_verification(page, editor, page_repo, req_repo, wf_repo, user_repo)

    result = await decide_request(
        req.request_id, "v_approver", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )
    assert result.status == RequestStatus.approved

    updated = await get_page(page.page_id, page_repo)
    assert updated.trust_tier == TrustTier.verified
    assert updated.verified_content_hash is not None
    assert updated.verified_at is not None
    assert updated.verified_by == "v_approver"
    # content untouched
    assert updated.content == "Some content"


@pytest.mark.asyncio
async def test_admin_without_workflow_self_provisions(verification_setup, page_repo, req_repo, wf_repo, user_repo):
    free_admin = verification_setup["free_admin"]
    page = verification_setup["page"]

    req = await request_page_verification(page, free_admin, page_repo, req_repo, wf_repo, user_repo)

    # workflow auto-provisioned and assigned to the admin
    refreshed_admin = await user_repo.get("v_admin")
    assert refreshed_admin.workflow_id is not None
    wf = await wf_repo.get(refreshed_admin.workflow_id)
    assert wf.steps == ["v_admin"]

    # the admin can immediately approve their own request
    result = await decide_request(
        req.request_id, "v_admin", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )
    assert result.status == RequestStatus.approved

    updated = await get_page(page.page_id, page_repo)
    assert updated.trust_tier == TrustTier.verified


@pytest.mark.asyncio
async def test_editor_without_workflow_is_rejected(verification_setup, page_repo, req_repo, wf_repo, user_repo):
    free_editor = verification_setup["free_editor"]
    page = verification_setup["page"]

    with pytest.raises(PermissionError):
        await request_page_verification(page, free_editor, page_repo, req_repo, wf_repo, user_repo)


@pytest.mark.asyncio
async def test_duplicate_request_is_rejected(verification_setup, page_repo, req_repo, wf_repo, user_repo):
    editor = verification_setup["editor"]
    page = verification_setup["page"]

    await request_page_verification(page, editor, page_repo, req_repo, wf_repo, user_repo)

    with pytest.raises(ValueError):
        await request_page_verification(page, editor, page_repo, req_repo, wf_repo, user_repo)
