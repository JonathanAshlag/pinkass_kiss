"""Tests for classification-based permission resolution."""

import pytest
import pytest_asyncio

from app.models.user import User, PermissionLevel
from app.models.page import Page, PageStatus, ClassificationTriangle
from app.services.permissions import can_view_page


@pytest_asyncio.fixture
async def sample_pages(page_repo):
    pages = [
        Page(page_id="public", title="Public", description="", status=PageStatus.published, created_by="test"),
        Page(page_id="secret", title="Secret", description="", status=PageStatus.published, created_by="test",
             classification=[ClassificationTriangle(id="sec", level=2)]),
        Page(page_id="top-secret", title="Top Secret", description="", status=PageStatus.published, created_by="test",
             classification=[ClassificationTriangle(id="sec", level=4)]),
        Page(page_id="multi", title="Multi", description="", status=PageStatus.published, created_by="test",
             classification=[ClassificationTriangle(id="sec", level=1), ClassificationTriangle(id="ops", level=3)]),
        Page(page_id="deleted", title="Deleted", description="", status=PageStatus.deleted, created_by="test"),
    ]
    for page in pages:
        await page_repo.create(page)


def _patch_triangles(monkeypatch, triangles):
    from app.services import permissions as perms_module

    async def mock_get(user_id):
        return triangles

    monkeypatch.setattr(perms_module, "get_user_triangles", mock_get)


@pytest.mark.asyncio
async def test_unclassified_page_visible_to_all(sample_pages, monkeypatch, page_repo):
    _patch_triangles(monkeypatch, [])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.read_only)
    assert await can_view_page(user, "public", page_repo) is True


@pytest.mark.asyncio
async def test_classified_page_blocked_without_triangles(sample_pages, monkeypatch, page_repo):
    _patch_triangles(monkeypatch, [])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.read_only)
    assert await can_view_page(user, "secret", page_repo) is False


@pytest.mark.asyncio
async def test_classified_page_accessible_with_sufficient_level(sample_pages, monkeypatch, page_repo):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=3)])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.read_only)
    assert await can_view_page(user, "secret", page_repo) is True


@pytest.mark.asyncio
async def test_classified_page_blocked_with_insufficient_level(sample_pages, monkeypatch, page_repo):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=1)])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.read_only)
    assert await can_view_page(user, "secret", page_repo) is False


@pytest.mark.asyncio
async def test_admin_still_blocked_by_classification(sample_pages, monkeypatch, page_repo):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=1)])
    admin = User(user_id="a1", name="Admin", permission_level=PermissionLevel.admin)
    assert await can_view_page(admin, "top-secret", page_repo) is False


@pytest.mark.asyncio
async def test_multi_triangle_requires_all(sample_pages, monkeypatch, page_repo):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=4)])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.editor)
    assert await can_view_page(user, "multi", page_repo) is False


@pytest.mark.asyncio
async def test_multi_triangle_passes_with_all(sample_pages, monkeypatch, page_repo):
    _patch_triangles(monkeypatch, [
        ClassificationTriangle(id="sec", level=1),
        ClassificationTriangle(id="ops", level=3),
    ])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.editor)
    assert await can_view_page(user, "multi", page_repo) is True


@pytest.mark.asyncio
async def test_nonexistent_page_returns_false(sample_pages, monkeypatch, page_repo):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=4)])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.admin)
    assert await can_view_page(user, "does-not-exist", page_repo) is False
