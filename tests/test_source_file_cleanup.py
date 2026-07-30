"""Tests for source-file cleanup on page deletion."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.page import PageCreate
from app.models.request import RequestType, RequestStatus
from app.models.user import User, PermissionLevel
from app.models.workflow import WorkflowCreate
from app.services.mutations import apply_page_mutation
from app.services.pages import create_page, get_page
from app.services.requests import create_request, decide_request
from app.services.source_files import cleanup_source_file_for_page
from app.services.workflows import create_workflow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_source_file(source_file_repo, file_id: str, page_ids: list[str]):
    await source_file_repo.create(
        file_id=file_id,
        filename="test.pdf",
        content_type="application/pdf",
        uploaded_by="admin",
        extracted_text="some text",
    )
    await source_file_repo.set_page_ids(file_id, page_ids)


# ---------------------------------------------------------------------------
# Unit tests for the cleanup service
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_removes_source_file_when_last_page_deleted(source_file_repo):
    file_id = "aabbccdd11223344aabbccdd"
    page_id = "page-a"

    await _seed_source_file(source_file_repo, file_id, [page_id])

    with patch("app.services.source_files.get_gridfs") as mock_gfs:
        mock_bucket = AsyncMock()
        mock_gfs.return_value = mock_bucket

        await cleanup_source_file_for_page(page_id, source_file_repo)

        mock_bucket.delete.assert_awaited_once()

    record = await source_file_repo.get_for_page(page_id)
    assert record is None


@pytest.mark.asyncio
async def test_cleanup_keeps_source_file_when_other_pages_remain(source_file_repo):
    file_id = "aabbccdd11223344aabbccdd"
    page_a, page_b = "page-a", "page-b"

    await _seed_source_file(source_file_repo, file_id, [page_a, page_b])

    with patch("app.services.source_files.get_gridfs") as mock_gfs:
        mock_bucket = AsyncMock()
        mock_gfs.return_value = mock_bucket

        await cleanup_source_file_for_page(page_a, source_file_repo)

        mock_bucket.delete.assert_not_awaited()

    record = await source_file_repo.get_for_page(page_b)
    assert record is not None
    assert page_a not in record["generated_page_ids"]
    assert page_b in record["generated_page_ids"]


@pytest.mark.asyncio
async def test_cleanup_is_noop_for_page_without_source_file(source_file_repo):
    with patch("app.services.source_files.get_gridfs") as mock_gfs:
        mock_bucket = AsyncMock()
        mock_gfs.return_value = mock_bucket

        await cleanup_source_file_for_page("orphan-page-id", source_file_repo)

        mock_bucket.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Integration: direct delete (no workflow)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_direct_delete_cleans_up_source_file(page_repo, source_file_repo):
    editor = User(user_id="ed1", name="Ed", permission_level=PermissionLevel.editor)
    page = await create_page(
        PageCreate(title="T", description="D", content="C"),
        editor,
        page_repo,
    )

    file_id = "aabbccdd11223344aabbccdd"
    await _seed_source_file(source_file_repo, file_id, [page.page_id])

    with patch("app.services.source_files.get_gridfs") as mock_gfs:
        mock_bucket = AsyncMock()
        mock_gfs.return_value = mock_bucket

        result = await apply_page_mutation(
            RequestType.delete, editor, page_repo, req_repo=None, page_id=page.page_id
        )
        assert result["status"] == "deleted"

        await cleanup_source_file_for_page(page.page_id, source_file_repo)

        mock_bucket.delete.assert_awaited_once()

    assert await source_file_repo.get_for_page(page.page_id) is None


# ---------------------------------------------------------------------------
# Integration: workflow-approved delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_approved_delete_cleans_up_source_file(
    page_repo, source_file_repo, user_repo, wf_repo, req_repo
):
    approver = User(user_id="ap1", name="Approver", permission_level=PermissionLevel.editor)
    await user_repo.create(approver)

    wf = await create_workflow(
        WorkflowCreate(name="One-step", steps=["ap1"]),
        "admin",
        wf_repo,
    )

    editor = User(
        user_id="ed2", name="Ed2",
        permission_level=PermissionLevel.editor,
        workflow_id=wf.workflow_id,
    )
    await user_repo.create(editor)

    page = await create_page(
        PageCreate(title="T", description="D", content="C"),
        editor,
        page_repo,
    )

    file_id = "aabbccdd11223344aabbccdd"
    await _seed_source_file(source_file_repo, file_id, [page.page_id])

    req = await create_request(
        RequestType.delete, page.page_id, editor,
        req_repo=req_repo, page_repo=page_repo,
    )

    with patch("app.services.source_files.get_gridfs") as mock_gfs:
        mock_bucket = AsyncMock()
        mock_gfs.return_value = mock_bucket

        result = await decide_request(
            req.request_id, "ap1", "approve",
            req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
            source_file_repo=source_file_repo,
        )

        assert result.status == RequestStatus.approved
        mock_bucket.delete.assert_awaited_once()

    assert await get_page(page.page_id, page_repo) is None
    assert await source_file_repo.get_for_page(page.page_id) is None
