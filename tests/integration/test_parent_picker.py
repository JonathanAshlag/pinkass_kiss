"""Tests for parent-page linking — the service layer behind the parent picker UI."""

import pytest

from app.models.page import PageCreate, PageUpdate, PageStatus
from app.models.user import User, PermissionLevel
from app.services.mutations import apply_page_mutation
from app.services.pages import create_page, get_page, fuzzy_search_pages
from app.models.request import RequestType


@pytest.fixture
async def editor(user_repo):
    user = User(user_id="editor1", name="Editor", permission_level=PermissionLevel.editor)
    await user_repo.create(user)
    return user


@pytest.fixture
async def parent_page(editor, page_repo):
    return await create_page(
        PageCreate(title="Parent Page", description="A parent", content="parent content"),
        editor,
        page_repo,
    )


# ---------------------------------------------------------------------------
# Fuzzy search (the API backing the picker)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fuzzy_search_returns_published_pages(editor, page_repo):
    await create_page(PageCreate(title="Alpha Page", description="d", content="c"), editor, page_repo)
    await create_page(PageCreate(title="Beta Page", description="d", content="c"), editor, page_repo)

    results = await fuzzy_search_pages("Alpha", editor, page_repo)

    titles = [p.title for p in results]
    assert "Alpha Page" in titles


@pytest.mark.asyncio
async def test_fuzzy_search_empty_when_no_pages(editor, page_repo):
    results = await fuzzy_search_pages("anything", editor, page_repo)
    assert results == []


@pytest.mark.asyncio
async def test_fuzzy_search_only_returns_published(editor, page_repo, user_repo, wf_repo, req_repo):
    from app.models.workflow import WorkflowCreate
    from app.services.workflows import create_workflow

    wf = await create_workflow(WorkflowCreate(name="wf", steps=["approver"]), "admin", wf_repo)
    wf_editor = User(
        user_id="wf_editor", name="WF Editor",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(wf_editor)

    # draft page (created by editor with workflow)
    await create_page(PageCreate(title="Draft Page", description="d", content="c"), wf_editor, page_repo)
    # published page
    await create_page(PageCreate(title="Published Page", description="d", content="c"), editor, page_repo)

    results = await fuzzy_search_pages("Page", editor, page_repo)
    statuses = {p.status for p in results}
    assert PageStatus.draft not in statuses
    assert PageStatus.published in statuses


# ---------------------------------------------------------------------------
# Create page with parent_id (what happens after picker selection)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_page_with_parent_id(editor, parent_page, page_repo, req_repo):
    result = await apply_page_mutation(
        RequestType.create, editor, page_repo, req_repo,
        data=PageCreate(
            title="Child Page",
            description="A child",
            content="child content",
            parent_id=parent_page.page_id,
        ),
    )
    assert result.status == "published"
    child = await get_page(result.page["page_id"], page_repo)
    assert child.parent_id == parent_page.page_id


@pytest.mark.asyncio
async def test_create_page_without_parent_is_root(editor, page_repo, req_repo):
    result = await apply_page_mutation(
        RequestType.create, editor, page_repo, req_repo,
        data=PageCreate(title="Root Page", description="d", content="c"),
    )
    page = await get_page(result.page["page_id"], page_repo)
    assert page.parent_id is None


# ---------------------------------------------------------------------------
# Edit page parent_id (what picker selection feeds into on save)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_sets_parent_id(editor, parent_page, page_repo, req_repo):
    child = await create_page(
        PageCreate(title="Child", description="d", content="c"),
        editor, page_repo,
    )
    assert child.parent_id is None

    await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(parent_id=parent_page.page_id),
        page_id=child.page_id,
    )
    updated = await get_page(child.page_id, page_repo)
    assert updated.parent_id == parent_page.page_id


@pytest.mark.asyncio
async def test_edit_changes_parent_id(editor, page_repo, req_repo):
    parent_a = await create_page(
        PageCreate(title="Parent A", description="d", content="c"), editor, page_repo,
    )
    parent_b = await create_page(
        PageCreate(title="Parent B", description="d", content="c"), editor, page_repo,
    )
    child = await create_page(
        PageCreate(title="Child", description="d", content="c", parent_id=parent_a.page_id),
        editor, page_repo,
    )

    await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(parent_id=parent_b.page_id),
        page_id=child.page_id,
    )
    updated = await get_page(child.page_id, page_repo)
    assert updated.parent_id == parent_b.page_id


@pytest.mark.asyncio
async def test_edit_clears_parent_id(editor, parent_page, page_repo, req_repo):
    child = await create_page(
        PageCreate(title="Child", description="d", content="c", parent_id=parent_page.page_id),
        editor, page_repo,
    )
    assert child.parent_id == parent_page.page_id

    await apply_page_mutation(
        RequestType.edit, editor, page_repo, req_repo,
        data=PageUpdate(parent_id=None),
        page_id=child.page_id,
    )
    updated = await get_page(child.page_id, page_repo)
    assert updated.parent_id is None


@pytest.mark.asyncio
async def test_multiple_children_share_same_parent(editor, parent_page, page_repo, req_repo):
    child_ids = []
    for i in range(3):
        result = await apply_page_mutation(
            RequestType.create, editor, page_repo, req_repo,
            data=PageCreate(
                title=f"Child {i}",
                description="d",
                content="c",
                parent_id=parent_page.page_id,
            ),
        )
        child_ids.append(result.page["page_id"])

    for cid in child_ids:
        page = await get_page(cid, page_repo)
        assert page.parent_id == parent_page.page_id
