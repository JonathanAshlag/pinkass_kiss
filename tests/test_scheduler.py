"""Tests for the daily expiry job."""

import pytest
import pytest_asyncio
from datetime import date, timedelta

from app.models.user import User, PermissionLevel
from app.models.page import PageStatus
from app.models.workflow import WorkflowCreate
from app.services.workflows import create_workflow
from app.scheduler.jobs import check_expired_pages


@pytest_asyncio.fixture
async def expiry_setup(mock_db):
    """Set up expired pages with workflow."""
    # Create workflow
    wf = await create_workflow(
        WorkflowCreate(name="Review WF", steps=["reviewer1"]),
        "admin"
    )

    # Create reviewer
    reviewer = User(user_id="reviewer1", name="Reviewer", permission_level=PermissionLevel.editor)
    await mock_db.users.insert_one(reviewer.model_dump(mode="json"))

    # Create page owner with workflow
    owner = User(
        user_id="owner1", name="Owner",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await mock_db.users.insert_one(owner.model_dump(mode="json"))

    # Create expired page
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await mock_db.pages.insert_one({
        "page_id": "expired_page",
        "title": "Expired Page",
        "parent_id": None,
        "content": "Needs review",
        "status": PageStatus.published.value,
        "next_approval_date": yesterday,
        "created_by": "owner1",
        "history": [],
    })

    # Create non-expired page
    future = (date.today() + timedelta(days=30)).isoformat()
    await mock_db.pages.insert_one({
        "page_id": "future_page",
        "title": "Future Page",
        "parent_id": None,
        "content": "Not yet",
        "status": PageStatus.published.value,
        "next_approval_date": future,
        "created_by": "owner1",
        "history": [],
    })

    # Create page with no expiry
    await mock_db.pages.insert_one({
        "page_id": "no_expiry",
        "title": "No Expiry",
        "parent_id": None,
        "content": "Never expires",
        "status": PageStatus.published.value,
        "next_approval_date": None,
        "created_by": "owner1",
        "history": [],
    })

    return {"workflow": wf, "owner": owner}


@pytest.mark.asyncio
async def test_expired_page_gets_review_request(mock_db, expiry_setup):
    processed = await check_expired_pages()
    assert processed == 1

    # Check that a request was created
    req = await mock_db.requests.find_one({"page_id": "expired_page"})
    assert req is not None
    assert req["type"] == "review"
    assert req["status"] == "pending"

    # Check page status changed
    page = await mock_db.pages.find_one({"page_id": "expired_page"})
    assert page["status"] == PageStatus.pending_approval.value


@pytest.mark.asyncio
async def test_future_page_not_affected(mock_db, expiry_setup):
    await check_expired_pages()

    req = await mock_db.requests.find_one({"page_id": "future_page"})
    assert req is None

    page = await mock_db.pages.find_one({"page_id": "future_page"})
    assert page["status"] == PageStatus.published.value


@pytest.mark.asyncio
async def test_no_duplicate_review_requests(mock_db, expiry_setup):
    """Running the job twice should not create duplicate requests."""
    await check_expired_pages()
    await check_expired_pages()

    cursor = mock_db.requests.find({"page_id": "expired_page"})
    count = 0
    async for _ in cursor:
        count += 1
    assert count == 1


@pytest.mark.asyncio
async def test_page_without_workflow_owner_skipped(mock_db):
    """Pages whose owner has no workflow are skipped."""
    owner = User(user_id="no_wf_owner", name="NoWF", permission_level=PermissionLevel.editor)
    await mock_db.users.insert_one(owner.model_dump(mode="json"))

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await mock_db.pages.insert_one({
        "page_id": "orphan_expired",
        "title": "Orphan",
        "parent_id": None,
        "content": "No workflow",
        "status": PageStatus.published.value,
        "next_approval_date": yesterday,
        "created_by": "no_wf_owner",
        "history": [],
    })

    processed = await check_expired_pages()
    assert processed == 0
