"""Tests for hierarchical permission resolution."""

import pytest
import pytest_asyncio

from app.models.user import User, PermissionLevel
from app.models.page import Page, PageStatus
from app.services.permissions import get_visible_page_ids, can_view_page, get_all_descendant_ids


@pytest_asyncio.fixture
async def sample_hierarchy(mock_db):
    """Create a page hierarchy: Animals -> Dog, Cat, Sheep; Dog -> Puppy."""
    pages = [
        {"page_id": "animals", "title": "Animals", "parent_id": None, "status": "published"},
        {"page_id": "dog", "title": "Dog", "parent_id": "animals", "status": "published"},
        {"page_id": "cat", "title": "Cat", "parent_id": "animals", "status": "published"},
        {"page_id": "sheep", "title": "Sheep", "parent_id": "animals", "status": "published"},
        {"page_id": "puppy", "title": "Puppy", "parent_id": "dog", "status": "published"},
        {"page_id": "plants", "title": "Plants", "parent_id": None, "status": "published"},
        {"page_id": "rose", "title": "Rose", "parent_id": "plants", "status": "published"},
    ]
    for p in pages:
        await mock_db.pages.insert_one(p)
    return pages


@pytest.mark.asyncio
async def test_admin_sees_all(mock_db, sample_hierarchy):
    admin = User(user_id="admin1", name="Admin", permission_level=PermissionLevel.admin)
    visible = await get_visible_page_ids(admin)
    assert "animals" in visible
    assert "dog" in visible
    assert "plants" in visible
    assert "rose" in visible
    assert len(visible) == 7


@pytest.mark.asyncio
async def test_permission_on_parent_grants_subtree(mock_db, sample_hierarchy):
    user = User(
        user_id="u1", name="User1",
        permission_level=PermissionLevel.read_only,
        page_permissions=["animals"]
    )
    visible = await get_visible_page_ids(user)
    assert "animals" in visible
    assert "dog" in visible
    assert "cat" in visible
    assert "sheep" in visible
    assert "puppy" in visible
    # Should NOT see plants tree
    assert "plants" not in visible
    assert "rose" not in visible


@pytest.mark.asyncio
async def test_permission_on_leaf(mock_db, sample_hierarchy):
    user = User(
        user_id="u2", name="User2",
        permission_level=PermissionLevel.read_only,
        page_permissions=["dog"]
    )
    visible = await get_visible_page_ids(user)
    assert "dog" in visible
    assert "puppy" in visible
    assert "animals" not in visible
    assert "cat" not in visible


@pytest.mark.asyncio
async def test_no_permissions_sees_nothing(mock_db, sample_hierarchy):
    user = User(
        user_id="u3", name="User3",
        permission_level=PermissionLevel.read_only,
        page_permissions=[]
    )
    visible = await get_visible_page_ids(user)
    assert len(visible) == 0


@pytest.mark.asyncio
async def test_can_view_page(mock_db, sample_hierarchy):
    user = User(
        user_id="u4", name="User4",
        permission_level=PermissionLevel.editor,
        page_permissions=["animals"]
    )
    assert await can_view_page(user, "dog") is True
    assert await can_view_page(user, "puppy") is True
    assert await can_view_page(user, "plants") is False


@pytest.mark.asyncio
async def test_multiple_permissions(mock_db, sample_hierarchy):
    user = User(
        user_id="u5", name="User5",
        permission_level=PermissionLevel.read_only,
        page_permissions=["dog", "plants"]
    )
    visible = await get_visible_page_ids(user)
    assert "dog" in visible
    assert "puppy" in visible
    assert "plants" in visible
    assert "rose" in visible
    assert "animals" not in visible
    assert "cat" not in visible


@pytest.mark.asyncio
async def test_deleted_pages_excluded_from_descendants(mock_db):
    await mock_db.pages.insert_one(
        {"page_id": "root", "title": "Root", "parent_id": None, "status": "published"}
    )
    await mock_db.pages.insert_one(
        {"page_id": "child", "title": "Child", "parent_id": "root", "status": "deleted"}
    )
    descendants = await get_all_descendant_ids("root")
    assert "child" not in descendants
