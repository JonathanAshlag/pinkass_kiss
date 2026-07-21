"""Tests for classification-based permission resolution."""

import pytest
import pytest_asyncio

from app.models.user import User, PermissionLevel
from app.models.page import Page, PageStatus, ClassificationTriangle
from app.services.permissions import can_view_page


@pytest_asyncio.fixture
async def sample_pages(mock_db):
    """Create pages with varying classification levels."""
    pages = [
        {"page_id": "public", "title": "Public", "status": "published", "classification": []},
        {"page_id": "secret", "title": "Secret", "status": "published",
         "classification": [{"id": "sec", "level": 2}]},
        {"page_id": "top-secret", "title": "Top Secret", "status": "published",
         "classification": [{"id": "sec", "level": 4}]},
        {"page_id": "multi", "title": "Multi", "status": "published",
         "classification": [{"id": "sec", "level": 1}, {"id": "ops", "level": 3}]},
        {"page_id": "deleted", "title": "Deleted", "status": "deleted", "classification": []},
    ]
    for p in pages:
        await mock_db.pages.insert_one(p)
    return pages


def _patch_triangles(monkeypatch, triangles):
    from app.services import permissions as perms_module

    async def mock_get(user_id):
        return triangles

    monkeypatch.setattr(perms_module, "get_user_triangles", mock_get)


@pytest.mark.asyncio
async def test_unclassified_page_visible_to_all(mock_db, sample_pages, monkeypatch):
    _patch_triangles(monkeypatch, [])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.read_only)
    assert await can_view_page(user, "public") is True


@pytest.mark.asyncio
async def test_classified_page_blocked_without_triangles(mock_db, sample_pages, monkeypatch):
    _patch_triangles(monkeypatch, [])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.read_only)
    assert await can_view_page(user, "secret") is False


@pytest.mark.asyncio
async def test_classified_page_accessible_with_sufficient_level(mock_db, sample_pages, monkeypatch):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=3)])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.read_only)
    assert await can_view_page(user, "secret") is True


@pytest.mark.asyncio
async def test_classified_page_blocked_with_insufficient_level(mock_db, sample_pages, monkeypatch):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=1)])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.read_only)
    assert await can_view_page(user, "secret") is False


@pytest.mark.asyncio
async def test_admin_still_blocked_by_classification(mock_db, sample_pages, monkeypatch):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=1)])
    admin = User(user_id="a1", name="Admin", permission_level=PermissionLevel.admin)
    assert await can_view_page(admin, "top-secret") is False


@pytest.mark.asyncio
async def test_multi_triangle_requires_all(mock_db, sample_pages, monkeypatch):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=4)])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.editor)
    assert await can_view_page(user, "multi") is False  # missing ops


@pytest.mark.asyncio
async def test_multi_triangle_passes_with_all(mock_db, sample_pages, monkeypatch):
    _patch_triangles(monkeypatch, [
        ClassificationTriangle(id="sec", level=1),
        ClassificationTriangle(id="ops", level=3),
    ])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.editor)
    assert await can_view_page(user, "multi") is True


@pytest.mark.asyncio
async def test_nonexistent_page_returns_false(mock_db, sample_pages, monkeypatch):
    _patch_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=4)])
    user = User(user_id="u1", name="User1", permission_level=PermissionLevel.admin)
    assert await can_view_page(user, "does-not-exist") is False
