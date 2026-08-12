"""Tests for page history logging."""

import pytest
import pytest_asyncio

from app.models.user import User, PermissionLevel
from app.models.page import PageCreate, PageUpdate
from app.services.pages import create_page, update_page, delete_page, get_page


@pytest_asyncio.fixture
async def editor(user_repo):
    user = User(user_id="hist_editor", name="HistEditor", permission_level=PermissionLevel.editor)
    await user_repo.create(user)
    return user


@pytest.mark.asyncio
async def test_create_logs_history(editor, page_repo):
    page = await create_page(PageCreate(title="Test", description="Test page", content="Initial"), editor, page_repo)
    assert len(page.history) == 1
    assert page.history[0].action == "create"
    assert page.history[0].user_id == editor.user_id
    assert page.history[0].snapshot == "Initial"


@pytest.mark.asyncio
async def test_edit_appends_history(editor, page_repo):
    page = await create_page(PageCreate(title="Test", description="Test page", content="V1"), editor, page_repo)
    updated = await update_page(page.page_id, PageUpdate(content="V2"), editor, page_repo)

    assert len(updated.history) == 2
    assert updated.history[0].action == "create"
    assert updated.history[1].action == "edit"
    assert updated.history[1].user_id == editor.user_id


@pytest.mark.asyncio
async def test_delete_removes_page(editor, page_repo):
    page = await create_page(PageCreate(title="Test", description="Test page", content="Delete me"), editor, page_repo)
    await delete_page(page.page_id, editor, page_repo)

    assert await get_page(page.page_id, page_repo) is None


@pytest.mark.asyncio
async def test_multiple_edits_accumulate(editor, page_repo):
    page = await create_page(PageCreate(title="Multi", description="Multi page", content="V1"), editor, page_repo)
    await update_page(page.page_id, PageUpdate(content="V2"), editor, page_repo)
    await update_page(page.page_id, PageUpdate(title="Multi Updated"), editor, page_repo)
    await update_page(page.page_id, PageUpdate(content="V3"), editor, page_repo)

    page = await get_page(page.page_id, page_repo)
    assert len(page.history) == 4
    actions = [h.action for h in page.history]
    assert actions == ["create", "edit", "edit", "edit"]
