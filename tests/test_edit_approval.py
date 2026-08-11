"""Tests for forcing approval on edits to already-verified pages."""

import pytest
import pytest_asyncio

from app.models.page import PageCreate, PageUpdate, TrustTier
from app.models.request import RequestStatus
from app.models.user import User, PermissionLevel
from app.services.pages import compute_content_hash, create_page, get_page
from app.services.mutations import apply_page_mutation, PendingResult, PublishedResult
from app.services.requests import decide_request
from app.services.workflows import create_workflow
from app.models.workflow import WorkflowCreate
from app.models.request import RequestType


@pytest_asyncio.fixture
async def edit_setup(page_repo, user_repo, wf_repo, req_repo):
    """A verified page, plus an editor with a workflow, an admin with none, and a plain editor with none."""
    approver = User(user_id="e_approver", name="Approver", permission_level=PermissionLevel.editor)
    await user_repo.create(approver)

    wf = await create_workflow(
        WorkflowCreate(name="Edit workflow", steps=["e_approver"]),
        "admin",
        wf_repo,
    )

    editor = User(
        user_id="e_editor", name="Editor",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(editor)

    free_admin = User(user_id="e_admin", name="Admin", permission_level=PermissionLevel.admin)
    await user_repo.create(free_admin)

    free_editor = User(user_id="e_free_editor", name="FreeEditor", permission_level=PermissionLevel.editor)
    await user_repo.create(free_editor)

    page = await create_page(
        PageCreate(title="Verified Page", description="desc", content="Original content"),
        editor,
        page_repo,
    )
    original_hash = compute_content_hash("Original content")
    await page_repo.update_fields(page.page_id, {
        "trust_tier": TrustTier.verified.value,
        "verified_content_hash": original_hash,
        "verified_at": "2026-01-01T00:00:00+00:00",
        "verified_by": "e_approver",
    })
    page = await get_page(page.page_id, page_repo)

    return {
        "workflow": wf,
        "editor": editor,
        "free_admin": free_admin,
        "free_editor": free_editor,
        "page": page,
    }


@pytest.mark.asyncio
async def test_edit_on_verified_page_is_pending_and_repins_on_approval(
    edit_setup, page_repo, req_repo, wf_repo, user_repo,
):
    editor = edit_setup["editor"]
    page = edit_setup["page"]

    result = await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(content="Changed content"), page_id=page.page_id,
        page=page, wf_repo=wf_repo, user_repo=user_repo,
    )
    assert isinstance(result, PendingResult)

    # content untouched while pending
    untouched = await get_page(page.page_id, page_repo)
    assert untouched.content == "Original content"
    assert untouched.trust_tier == TrustTier.verified

    approved = await decide_request(
        result.request_id, "e_approver", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )
    assert approved.status == RequestStatus.approved

    updated = await get_page(page.page_id, page_repo)
    assert updated.content == "Changed content"
    assert updated.trust_tier == TrustTier.verified
    assert updated.verified_content_hash == compute_content_hash("Changed content")
    assert updated.verified_by == "e_approver"


@pytest.mark.asyncio
async def test_admin_without_workflow_self_provisions_for_edit(
    edit_setup, page_repo, req_repo, wf_repo, user_repo,
):
    free_admin = edit_setup["free_admin"]
    page = edit_setup["page"]

    result = await apply_page_mutation(
        RequestType.edit, free_admin, page_repo, req_repo,
        data=PageUpdate(content="Admin edit"), page_id=page.page_id,
        page=page, wf_repo=wf_repo, user_repo=user_repo,
    )
    assert isinstance(result, PendingResult)

    refreshed_admin = await user_repo.get("e_admin")
    assert refreshed_admin.workflow_id is not None

    approved = await decide_request(
        result.request_id, "e_admin", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )
    assert approved.status == RequestStatus.approved

    updated = await get_page(page.page_id, page_repo)
    assert updated.trust_tier == TrustTier.verified
    assert updated.verified_content_hash == compute_content_hash("Admin edit")


@pytest.mark.asyncio
async def test_editor_without_workflow_is_rejected_for_edit(
    edit_setup, page_repo, req_repo, wf_repo, user_repo,
):
    free_editor = edit_setup["free_editor"]
    page = edit_setup["page"]

    with pytest.raises(PermissionError):
        await apply_page_mutation(
            RequestType.edit, free_editor, page_repo, req_repo,
            data=PageUpdate(content="Nope"), page_id=page.page_id,
            page=page, wf_repo=wf_repo, user_repo=user_repo,
        )


@pytest.mark.asyncio
async def test_editor_without_workflow_publishes_directly_on_unverified_page(
    edit_setup, page_repo, req_repo, wf_repo, user_repo,
):
    """Regression guard: non-verified pages keep today's behavior (no forced approval)."""
    free_editor = edit_setup["free_editor"]
    unverified_page = await create_page(
        PageCreate(title="Plain Page", description="desc", content="Plain content"),
        free_editor,
        page_repo,
    )

    result = await apply_page_mutation(
        RequestType.edit, free_editor, page_repo, req_repo,
        data=PageUpdate(content="Directly changed"), page_id=unverified_page.page_id,
        page=unverified_page, wf_repo=wf_repo, user_repo=user_repo,
    )
    assert isinstance(result, PublishedResult)

    updated = await get_page(unverified_page.page_id, page_repo)
    assert updated.content == "Directly changed"
