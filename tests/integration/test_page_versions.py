"""Tests for page version snapshots, deleted-page archival, and bundle version pinning."""

import pytest
import pytest_asyncio

from app.models.user import User, PermissionLevel
from app.models.page import PageCreate, PageUpdate, PageStatus
from app.models.bundle import BundleEntry, ContentForm
from app.models.request import RequestType, RequestStatus, CreatePayload, EditPayload, DeletePayload
from app.models.workflow import WorkflowCreate
from app.services.pages import create_page, update_page, delete_page, get_page
from app.services.workflows import create_workflow
from app.services.requests import create_request, decide_request
from app.services.bundles import upsert_bundle, fetch_bundle_text


@pytest_asyncio.fixture
async def editor(user_repo):
    user = User(user_id="ver_editor", name="VerEditor", permission_level=PermissionLevel.editor)
    await user_repo.create(user)
    return user


@pytest_asyncio.fixture
async def admin_user(user_repo):
    user = User(user_id="ver_admin", name="VerAdmin", permission_level=PermissionLevel.admin)
    await user_repo.create(user)
    return user


@pytest_asyncio.fixture
async def workflow_setup(page_repo, user_repo, wf_repo, req_repo):
    """A free (non-workflow) editor plus a workflow-gated editor sharing one approver."""
    approver = User(user_id="ver_approver", name="VerApprover", permission_level=PermissionLevel.editor)
    await user_repo.create(approver)
    wf = await create_workflow(WorkflowCreate(name="One-step", steps=["ver_approver"]), "admin", wf_repo)
    wf_editor = User(
        user_id="ver_wf_editor", name="VerWfEditor",
        permission_level=PermissionLevel.editor, workflow_id=wf.workflow_id,
    )
    await user_repo.create(wf_editor)
    free_editor = User(user_id="ver_free_editor", name="VerFreeEditor", permission_level=PermissionLevel.editor)
    await user_repo.create(free_editor)
    return {"workflow": wf, "wf_editor": wf_editor, "free_editor": free_editor}


# --- Direct create/edit produce versions ------------------------------------------------

@pytest.mark.asyncio
async def test_direct_create_produces_v1(editor, page_repo):
    page = await create_page(PageCreate(title="VTest", description="d", content="V1"), editor, page_repo)

    versions = await page_repo.get_versions(page.page_id)
    assert len(versions) == 1
    assert versions[0].version_id == f"{page.page_id}-v1"
    assert versions[0].version_number == 1
    assert versions[0].action == "create"
    assert versions[0].content == "V1"

    fetched = await get_page(page.page_id, page_repo)
    assert fetched.current_version_number == 1


@pytest.mark.asyncio
async def test_direct_edit_produces_v2(editor, page_repo):
    page = await create_page(PageCreate(title="VTest2", description="d", content="V1"), editor, page_repo)
    await update_page(page.page_id, PageUpdate(content="V2"), editor, page_repo)

    versions = await page_repo.get_versions(page.page_id)
    assert [v.version_number for v in versions] == [1, 2]
    assert versions[1].content == "V2"
    assert versions[1].action == "edit"

    version = await page_repo.get_version(versions[1].version_id)
    assert version is not None
    assert version.content == "V2"


# --- Draft / workflow-gated writes don't get a version until they go live ---------------

@pytest.mark.asyncio
async def test_draft_page_has_no_version_until_approved(workflow_setup, page_repo, req_repo, wf_repo):
    wf_editor = workflow_setup["wf_editor"]
    page = await create_page(
        PageCreate(title="Draft Version Test", description="d", content="Draft content"), wf_editor, page_repo,
    )
    assert page.status == PageStatus.draft
    assert await page_repo.get_versions(page.page_id) == []

    req = await create_request(
        RequestType.create, page.page_id, wf_editor, req_repo=req_repo, page_repo=page_repo,
        proposed_content=CreatePayload(title="Draft Version Test", description="d", content="Draft content"),
    )
    result = await decide_request(
        req.request_id, "ver_approver", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )
    assert result.status == RequestStatus.approved

    published = await get_page(page.page_id, page_repo)
    assert published.status == PageStatus.published

    versions = await page_repo.get_versions(page.page_id)
    assert len(versions) == 1
    assert versions[0].action == "approve"
    assert versions[0].content == "Draft content"


@pytest.mark.asyncio
async def test_workflow_approved_edit_produces_version(workflow_setup, page_repo, req_repo, wf_repo):
    wf_editor = workflow_setup["wf_editor"]
    free_editor = workflow_setup["free_editor"]

    page = await create_page(
        PageCreate(title="WF Edit Test", description="d", content="Original"), free_editor, page_repo,
    )
    assert page.status == PageStatus.published
    assert len(await page_repo.get_versions(page.page_id)) == 1

    req = await create_request(
        RequestType.edit, page.page_id, wf_editor, req_repo=req_repo, page_repo=page_repo,
        proposed_content=EditPayload(content="Edited via workflow"),
    )
    await decide_request(
        req.request_id, "ver_approver", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )

    versions = await page_repo.get_versions(page.page_id)
    assert len(versions) == 2
    assert versions[1].content == "Edited via workflow"
    assert versions[1].action == "approve"

    published = await get_page(page.page_id, page_repo)
    assert published.content == "Edited via workflow"


@pytest.mark.asyncio
async def test_rejected_edit_produces_no_new_version(workflow_setup, page_repo, req_repo, wf_repo):
    wf_editor = workflow_setup["wf_editor"]
    free_editor = workflow_setup["free_editor"]

    page = await create_page(
        PageCreate(title="WF Reject Test", description="d", content="Original"), free_editor, page_repo,
    )
    assert len(await page_repo.get_versions(page.page_id)) == 1

    req = await create_request(
        RequestType.edit, page.page_id, wf_editor, req_repo=req_repo, page_repo=page_repo,
        proposed_content=EditPayload(content="Should not land"),
    )
    await decide_request(
        req.request_id, "ver_approver", "reject",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )

    assert len(await page_repo.get_versions(page.page_id)) == 1


# --- Delete archives the full chain -------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_archives_full_chain(editor, page_repo):
    page = await create_page(PageCreate(title="DelTest", description="d", content="V1"), editor, page_repo)
    await update_page(page.page_id, PageUpdate(content="V2"), editor, page_repo)

    await delete_page(page.page_id, editor, page_repo)

    assert await get_page(page.page_id, page_repo) is None
    assert await page_repo.get_versions(page.page_id) == []

    archived = await page_repo.get_deleted_page(page.page_id)
    assert archived is not None
    assert archived["page"]["title"] == "DelTest"
    assert len(archived["versions"]) == 2
    assert len(archived["history"]) == 2

    listed = await page_repo.list_deleted_pages()
    assert any(d["page_id"] == page.page_id for d in listed)


@pytest.mark.asyncio
async def test_workflow_approved_delete_archives_page(workflow_setup, page_repo, req_repo, wf_repo):
    wf_editor = workflow_setup["wf_editor"]
    free_editor = workflow_setup["free_editor"]

    page = await create_page(PageCreate(title="WF Del Test", description="d", content="X"), free_editor, page_repo)

    req = await create_request(
        RequestType.delete, page.page_id, wf_editor, req_repo=req_repo, page_repo=page_repo,
        proposed_content=DeletePayload(),
    )
    await decide_request(
        req.request_id, "ver_approver", "approve",
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
    )

    assert await get_page(page.page_id, page_repo) is None
    archived = await page_repo.get_deleted_page(page.page_id)
    assert archived is not None


# --- Bundle version pinning ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_bundle_pinned_entry_freezes_content(editor, admin_user, page_repo, bundle_repo):
    page = await create_page(PageCreate(title="Bundle Pin Test", description="d", content="V1"), editor, page_repo)
    v1_id = f"{page.page_id}-v1"

    await upsert_bundle(
        "pin-test-bundle",
        [BundleEntry(page_id=page.page_id, content_form=ContentForm.full_info, version_id=v1_id)],
        admin_user, bundle_repo, page_repo,
    )

    await update_page(page.page_id, PageUpdate(content="V2"), editor, page_repo)

    _, rendered = await fetch_bundle_text("pin-test-bundle", admin_user, bundle_repo, page_repo)
    assert "V1" in rendered
    assert "V2" not in rendered


@pytest.mark.asyncio
async def test_bundle_unpinned_entry_tracks_latest(editor, admin_user, page_repo, bundle_repo):
    page = await create_page(PageCreate(title="Bundle Latest Test", description="d", content="V1"), editor, page_repo)

    await upsert_bundle(
        "latest-test-bundle",
        [BundleEntry(page_id=page.page_id, content_form=ContentForm.full_info, version_id=None)],
        admin_user, bundle_repo, page_repo,
    )

    await update_page(page.page_id, PageUpdate(content="V2"), editor, page_repo)

    _, rendered = await fetch_bundle_text("latest-test-bundle", admin_user, bundle_repo, page_repo)
    assert "V2" in rendered
    assert "V1" not in rendered
