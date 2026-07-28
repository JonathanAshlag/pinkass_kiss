"""Tests for the daily expiry job.

Scheduler tests use MongoDB-specific repos directly because background_repos()
always resolves to the MongoDB mock in the test environment.
"""

import pytest
import pytest_asyncio
from datetime import date, timedelta

from app.models.user import User, PermissionLevel
from app.models.page import Page, PageStatus
from app.models.workflow import WorkflowCreate
from app.services.workflows import create_workflow
from app.scheduler.jobs import check_expired_pages


@pytest_asyncio.fixture
async def expiry_setup(mock_db):
    """Set up expired pages with workflow using MongoDB repos directly."""
    from app.storage.mongo.workflows import MongoWorkflowRepository
    from app.storage.mongo.users import MongoUserRepository
    from app.storage.mongo.pages import MongoPageRepository

    wf_repo = MongoWorkflowRepository(mock_db)
    user_repo = MongoUserRepository(mock_db)
    page_repo = MongoPageRepository(mock_db)

    wf = await create_workflow(
        WorkflowCreate(name="Review WF", steps=["reviewer1"]),
        "admin",
        wf_repo,
    )

    await user_repo.create(User(user_id="reviewer1", name="Reviewer", permission_level=PermissionLevel.editor))
    owner = User(
        user_id="owner1", name="Owner",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(owner)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    future = (date.today() + timedelta(days=30)).isoformat()

    await page_repo.create(Page(
        page_id="expired_page", title="Expired Page", description="",
        content="Needs review", status=PageStatus.published,
        next_approval_date=date.today() - timedelta(days=1),
        created_by="owner1",
    ))
    await page_repo.create(Page(
        page_id="future_page", title="Future Page", description="",
        content="Not yet", status=PageStatus.published,
        next_approval_date=date.today() + timedelta(days=30),
        created_by="owner1",
    ))
    await page_repo.create(Page(
        page_id="no_expiry", title="No Expiry", description="",
        content="Never expires", status=PageStatus.published,
        created_by="owner1",
    ))

    return {"workflow": wf, "owner": owner}


@pytest.mark.asyncio
async def test_expired_page_gets_review_request(mock_db, expiry_setup):
    processed = await check_expired_pages()
    assert processed == 1

    req = await mock_db.requests.find_one({"page_id": "expired_page"})
    assert req is not None
    assert req["type"] == "review"
    assert req["status"] == "pending"

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
    await check_expired_pages()
    await check_expired_pages()

    cursor = mock_db.requests.find({"page_id": "expired_page"})
    count = 0
    async for _ in cursor:
        count += 1
    assert count == 1


@pytest.mark.asyncio
async def test_page_without_workflow_owner_skipped(mock_db):
    from app.storage.mongo.users import MongoUserRepository
    from app.storage.mongo.pages import MongoPageRepository

    user_repo = MongoUserRepository(mock_db)
    page_repo = MongoPageRepository(mock_db)

    owner = User(user_id="no_wf_owner", name="NoWF", permission_level=PermissionLevel.editor)
    await user_repo.create(owner)

    await page_repo.create(Page(
        page_id="orphan_expired", title="Orphan", description="",
        content="No workflow", status=PageStatus.published,
        next_approval_date=date.today() - timedelta(days=1),
        created_by="no_wf_owner",
    ))

    processed = await check_expired_pages()
    assert processed == 0
