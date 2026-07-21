"""Tests for classification triangle access control."""

import pytest
import pytest_asyncio

from app.models.page import ClassificationTriangle, Page, PageCreate, PageUpdate, PageStatus
from app.models.user import User, PermissionLevel
from app.services.classification import get_user_triangles, user_satisfies_classification
from app.services.pages import create_page, update_page, get_page, find_pages, get_page_tree
from app.services.permissions import can_view_page


# ---------------------------------------------------------------------------
# Pure logic: user_satisfies_classification
# ---------------------------------------------------------------------------

class TestUserSatisfiesClassification:
    def test_empty_page_classification_always_passes(self):
        user_tri = [ClassificationTriangle(id="a", level=2)]
        assert user_satisfies_classification(user_tri, []) is True

    def test_empty_user_triangles_fails_if_page_has_classification(self):
        page_tri = [ClassificationTriangle(id="a", level=1)]
        assert user_satisfies_classification([], page_tri) is False

    def test_exact_match(self):
        user_tri = [ClassificationTriangle(id="sec", level=3)]
        page_tri = [ClassificationTriangle(id="sec", level=3)]
        assert user_satisfies_classification(user_tri, page_tri) is True

    def test_user_higher_level_passes(self):
        user_tri = [ClassificationTriangle(id="sec", level=4)]
        page_tri = [ClassificationTriangle(id="sec", level=2)]
        assert user_satisfies_classification(user_tri, page_tri) is True

    def test_user_lower_level_fails(self):
        user_tri = [ClassificationTriangle(id="sec", level=1)]
        page_tri = [ClassificationTriangle(id="sec", level=3)]
        assert user_satisfies_classification(user_tri, page_tri) is False

    def test_user_missing_triangle_id_fails(self):
        user_tri = [ClassificationTriangle(id="alpha", level=4)]
        page_tri = [ClassificationTriangle(id="beta", level=1)]
        assert user_satisfies_classification(user_tri, page_tri) is False

    def test_multiple_triangles_all_match(self):
        user_tri = [
            ClassificationTriangle(id="a", level=3),
            ClassificationTriangle(id="b", level=2),
        ]
        page_tri = [
            ClassificationTriangle(id="a", level=2),
            ClassificationTriangle(id="b", level=1),
        ]
        assert user_satisfies_classification(user_tri, page_tri) is True

    def test_multiple_triangles_one_fails(self):
        user_tri = [
            ClassificationTriangle(id="a", level=3),
            ClassificationTriangle(id="b", level=1),
        ]
        page_tri = [
            ClassificationTriangle(id="a", level=2),
            ClassificationTriangle(id="b", level=2),
        ]
        assert user_satisfies_classification(user_tri, page_tri) is False

    def test_user_extra_triangles_ignored(self):
        user_tri = [
            ClassificationTriangle(id="a", level=4),
            ClassificationTriangle(id="b", level=4),
            ClassificationTriangle(id="c", level=4),
        ]
        page_tri = [ClassificationTriangle(id="a", level=1)]
        assert user_satisfies_classification(user_tri, page_tri) is True

    def test_both_empty(self):
        assert user_satisfies_classification([], []) is True


# ---------------------------------------------------------------------------
# External API client
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_user_triangles_disabled_when_no_url(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "classification_api_url", "")
    result = await get_user_triangles("user1")
    assert result == []


@pytest.mark.asyncio
async def test_get_user_triangles_returns_parsed_triangles(monkeypatch):
    from app.config import settings
    from app.services import classification as cls_module

    monkeypatch.setattr(settings, "classification_api_url", "http://mock-api")

    async def mock_get(self, url, **kwargs):
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"triangles": [{"id": "sec", "level": 3}, {"id": "ops", "level": 1}]}
        return MockResp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await get_user_triangles("user1")
    assert len(result) == 2
    assert result[0].id == "sec"
    assert result[0].level == 3
    assert result[1].id == "ops"
    assert result[1].level == 1


@pytest.mark.asyncio
async def test_get_user_triangles_returns_empty_on_error(monkeypatch):
    from app.config import settings
    from app.services import classification as cls_module

    monkeypatch.setattr(settings, "classification_api_url", "http://mock-api")

    async def mock_get(self, url, **kwargs):
        raise Exception("connection refused")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await get_user_triangles("user1")
    assert result == []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def classified_pages(mock_db):
    """Create pages with different classification levels."""
    admin = User(user_id="admin1", name="Admin", permission_level=PermissionLevel.admin)
    await mock_db.users.insert_one(admin.model_dump(mode="json"))

    # Unclassified page (accessible to all)
    page_open = Page(
        page_id="open-page",
        title="Open Page",
        description="No classification",
        content="Public info",
        status=PageStatus.published,
        created_by="admin1",
    )
    # Page with low classification
    page_low = Page(
        page_id="low-page",
        title="Low Secret",
        description="Low classification",
        content="Slightly secret",
        classification=[ClassificationTriangle(id="sec", level=1)],
        status=PageStatus.published,
        created_by="admin1",
    )
    # Page with high classification
    page_high = Page(
        page_id="high-page",
        title="Top Secret",
        description="High classification",
        content="Very secret",
        classification=[ClassificationTriangle(id="sec", level=4)],
        status=PageStatus.published,
        created_by="admin1",
    )
    # Page with multiple triangles
    page_multi = Page(
        page_id="multi-page",
        title="Multi Classified",
        description="Multiple triangles",
        content="Needs both",
        classification=[
            ClassificationTriangle(id="sec", level=2),
            ClassificationTriangle(id="ops", level=3),
        ],
        status=PageStatus.published,
        created_by="admin1",
    )
    for page in [page_open, page_low, page_high, page_multi]:
        await mock_db.pages.insert_one(page.model_dump(mode="json"))

    return {"open": page_open, "low": page_low, "high": page_high, "multi": page_multi}


def _patch_user_triangles(monkeypatch, triangles: list[ClassificationTriangle]):
    """Patch get_user_triangles to return fixed triangles."""
    from app.services import classification as cls_module

    async def mock_get(user_id):
        return triangles

    monkeypatch.setattr(cls_module, "get_user_triangles", mock_get)
    # Also patch the import in pages module
    from app.services import pages as pages_module
    monkeypatch.setattr(pages_module, "get_user_triangles", mock_get)
    # And in permissions module
    from app.services import permissions as perms_module
    monkeypatch.setattr(perms_module, "get_user_triangles", mock_get)


# ---------------------------------------------------------------------------
# can_view_page integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_can_view_page_classification_filtering(mock_db, classified_pages, monkeypatch):
    user = User(
        user_id="u1", name="User1",
        permission_level=PermissionLevel.read_only,
    )
    _patch_user_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=2)])

    assert await can_view_page(user, "open-page") is True
    assert await can_view_page(user, "low-page") is True
    assert await can_view_page(user, "high-page") is False  # needs level 4


@pytest.mark.asyncio
async def test_can_view_page_admin_still_blocked_by_classification(mock_db, classified_pages, monkeypatch):
    admin = User(user_id="admin1", name="Admin", permission_level=PermissionLevel.admin)
    _patch_user_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=1)])

    assert await can_view_page(admin, "open-page") is True
    assert await can_view_page(admin, "low-page") is True  # level 1 == level 1
    assert await can_view_page(admin, "high-page") is False  # admin but lacks level 4


@pytest.mark.asyncio
async def test_can_view_page_unclassified_page_no_triangles_needed(mock_db, classified_pages, monkeypatch):
    user = User(
        user_id="u1", name="User1",
        permission_level=PermissionLevel.read_only,
    )
    _patch_user_triangles(monkeypatch, [])  # no triangles at all

    assert await can_view_page(user, "open-page") is True  # no classification needed


@pytest.mark.asyncio
async def test_can_view_page_multi_triangle_requirement(mock_db, classified_pages, monkeypatch):
    user = User(
        user_id="u1", name="User1",
        permission_level=PermissionLevel.admin,
    )

    # Has sec but not ops
    _patch_user_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=4)])
    assert await can_view_page(user, "multi-page") is False

    # Has both with sufficient levels
    _patch_user_triangles(monkeypatch, [
        ClassificationTriangle(id="sec", level=2),
        ClassificationTriangle(id="ops", level=3),
    ])
    assert await can_view_page(user, "multi-page") is True


# ---------------------------------------------------------------------------
# Bulk query filtering (find_pages / _find_raw_docs)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_pages_filters_by_classification(mock_db, classified_pages, monkeypatch):
    admin = User(user_id="admin1", name="Admin", permission_level=PermissionLevel.admin)
    _patch_user_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=2)])

    results = await find_pages("", user=admin)
    page_ids = {p.page_id for p in results}

    assert "open-page" in page_ids
    assert "low-page" in page_ids  # level 1, user has 2
    assert "high-page" not in page_ids  # level 4, user has 2
    assert "multi-page" not in page_ids  # needs ops triangle too


@pytest.mark.asyncio
async def test_find_pages_system_user_sees_all_with_full_clearance(mock_db, classified_pages, monkeypatch):
    """System user with full classification clearance can see all pages."""
    system_user = User(user_id="system", name="System", permission_level=PermissionLevel.admin)
    _patch_user_triangles(monkeypatch, [
        ClassificationTriangle(id="sec", level=4),
        ClassificationTriangle(id="ops", level=4),
    ])

    results = await find_pages("", user=system_user)
    page_ids = {p.page_id for p in results}

    assert "open-page" in page_ids
    assert "low-page" in page_ids
    assert "high-page" in page_ids
    assert "multi-page" in page_ids


# ---------------------------------------------------------------------------
# get_page_tree filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_page_tree_filters_classified_pages(mock_db, classified_pages, monkeypatch):
    admin = User(user_id="admin1", name="Admin", permission_level=PermissionLevel.admin)
    _patch_user_triangles(monkeypatch, [ClassificationTriangle(id="sec", level=1)])

    tree = await get_page_tree(admin)
    page_ids = {p["page_id"] for p in tree}

    assert "open-page" in page_ids
    assert "low-page" in page_ids  # level 1 matches
    assert "high-page" not in page_ids  # level 4 > user's 1
    assert "multi-page" not in page_ids  # missing ops


# ---------------------------------------------------------------------------
# Page CRUD with classification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_page_persists_classification(mock_db):
    user = User(user_id="creator", name="Creator", permission_level=PermissionLevel.admin)
    await mock_db.users.insert_one(user.model_dump(mode="json"))

    data = PageCreate(
        title="Classified Doc",
        description="A classified document",
        content="Secret content",
        classification=[
            ClassificationTriangle(id="alpha", level=2),
            ClassificationTriangle(id="beta", level=3),
        ],
    )
    page = await create_page(data, user)

    assert len(page.classification) == 2
    assert page.classification[0].id == "alpha"
    assert page.classification[0].level == 2
    assert page.classification[1].id == "beta"
    assert page.classification[1].level == 3

    # Verify persisted in DB
    stored = await get_page(page.page_id)
    assert len(stored.classification) == 2


@pytest.mark.asyncio
async def test_update_page_changes_classification(mock_db):
    user = User(user_id="creator", name="Creator", permission_level=PermissionLevel.admin)
    await mock_db.users.insert_one(user.model_dump(mode="json"))

    data = PageCreate(
        title="Doc",
        description="Original",
        content="Content",
        classification=[ClassificationTriangle(id="x", level=1)],
    )
    page = await create_page(data, user)

    update_data = PageUpdate(
        classification=[
            ClassificationTriangle(id="x", level=3),
            ClassificationTriangle(id="y", level=2),
        ]
    )
    updated = await update_page(page.page_id, update_data, user)
    assert len(updated.classification) == 2
    assert updated.classification[0].id == "x"
    assert updated.classification[0].level == 3


@pytest.mark.asyncio
async def test_create_page_without_classification_defaults_empty(mock_db):
    user = User(user_id="creator", name="Creator", permission_level=PermissionLevel.admin)
    await mock_db.users.insert_one(user.model_dump(mode="json"))

    data = PageCreate(title="Normal", description="Normal page", content="Hello")
    page = await create_page(data, user)
    assert page.classification == []


# ---------------------------------------------------------------------------
# External API failure scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_failure_blocks_classified_pages(mock_db, classified_pages, monkeypatch):
    """When external API fails, user gets empty triangles — classified pages inaccessible."""
    from app.config import settings
    monkeypatch.setattr(settings, "classification_api_url", "http://broken-api")

    import httpx

    async def mock_get(self, url, **kwargs):
        raise Exception("timeout")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    admin = User(user_id="admin1", name="Admin", permission_level=PermissionLevel.admin)

    # Unclassified page should still be accessible
    assert await can_view_page(admin, "open-page") is True
    # Classified pages should be blocked
    assert await can_view_page(admin, "low-page") is False
    assert await can_view_page(admin, "high-page") is False
