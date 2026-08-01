"""Tests for the LLM ingestion pipeline (extract → dedup → create/merge)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.llm.pipeline import PageIngestOutcome, run_ingestion_pipeline
from app.models.page import PageCreate, TrustTier
from app.models.user import User, PermissionLevel
from app.services.mutations import PublishedResult
from app.services.pages import create_page


@pytest.fixture
def editor():
    return User(user_id="editor1", name="Editor", permission_level=PermissionLevel.editor)


@pytest.fixture
def pipeline_kwargs(page_repo, req_repo):
    return dict(
        text="Full document text",
        content_parts=[{"type": "text", "text": "Full document text"}],
        filename="doc.pdf",
        file_id_str="file123",
        initial_trust_tier=TrustTier.unverified.value,
        page_repo=page_repo,
        req_repo=req_repo,
    )


@pytest.mark.asyncio
async def test_pipeline_creates_page_for_new_candidate(editor, pipeline_kwargs):
    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Security Policy", "description": "MFA and access guidelines"}
        ])),
        patch("app.llm.pipeline.generate_page_content", AsyncMock(return_value={
            "content": "## Security Policy\nAll access requires MFA."
        })),
    ):
        results = await run_ingestion_pipeline(user=editor, **pipeline_kwargs)

    assert len(results) == 1
    outcome = results[0]
    assert isinstance(outcome, PageIngestOutcome)
    assert outcome.action == "created"
    assert outcome.title == "Security Policy"
    assert outcome.status == "published"
    assert outcome.page_id


@pytest.mark.asyncio
async def test_pipeline_skips_candidates_without_title(editor, pipeline_kwargs):
    with patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
        {"title": "", "description": "No title here"},
        {"title": "  ", "description": "Also blank"},
    ])):
        results = await run_ingestion_pipeline(user=editor, **pipeline_kwargs)

    assert results == []


@pytest.mark.asyncio
async def test_pipeline_returns_empty_for_no_candidates(editor, pipeline_kwargs):
    with patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[])):
        results = await run_ingestion_pipeline(user=editor, **pipeline_kwargs)

    assert results == []


@pytest.mark.asyncio
async def test_pipeline_merges_duplicate_with_new_info(editor, page_repo, req_repo, pipeline_kwargs):
    existing = await create_page(
        PageCreate(title="Security Policy", description="Existing", content="Old content"),
        editor,
        page_repo,
    )

    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Security Policy", "description": "Updated policy"}
        ])),
        patch("app.llm.pipeline.judge_duplicate", AsyncMock(return_value={
            "is_duplicate": True,
            "matched_page_id": existing.page_id,
            "confidence": "high",
        })),
        patch("app.llm.pipeline.merge_content", AsyncMock(return_value={
            "has_new_info": True,
            "merged_content": "Old content\n\nNew MFA requirement added.",
        })),
        patch.object(page_repo, "find_similar_for_dedup", AsyncMock(return_value=[
            {"page_id": existing.page_id, "title": "Security Policy",
             "description": "Existing", "content": "Old content"},
        ])),
    ):
        results = await run_ingestion_pipeline(user=editor, **pipeline_kwargs)

    assert len(results) == 1
    outcome = results[0]
    assert outcome.action == "merged"
    assert outcome.page_id == existing.page_id
    assert outcome.status == "published"


@pytest.mark.asyncio
async def test_pipeline_links_duplicate_with_no_new_info(editor, page_repo, req_repo, pipeline_kwargs):
    existing = await create_page(
        PageCreate(title="Security Policy", description="Existing", content="Complete content"),
        editor,
        page_repo,
    )

    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Security Policy", "description": "Same policy"}
        ])),
        patch("app.llm.pipeline.judge_duplicate", AsyncMock(return_value={
            "is_duplicate": True,
            "matched_page_id": existing.page_id,
            "confidence": "high",
        })),
        patch("app.llm.pipeline.merge_content", AsyncMock(return_value={
            "has_new_info": False,
            "merged_content": None,
        })),
        patch.object(page_repo, "find_similar_for_dedup", AsyncMock(return_value=[
            {"page_id": existing.page_id, "title": "Security Policy",
             "description": "Existing", "content": "Complete content"},
        ])),
    ):
        results = await run_ingestion_pipeline(user=editor, **pipeline_kwargs)

    assert len(results) == 1
    outcome = results[0]
    assert outcome.action == "linked"
    assert outcome.page_id == existing.page_id
    assert outcome.status == "linked"


@pytest.mark.asyncio
async def test_pipeline_ignores_low_confidence_duplicate(editor, page_repo, req_repo, pipeline_kwargs):
    existing = await create_page(
        PageCreate(title="Security Policy", description="Existing", content="Content"),
        editor,
        page_repo,
    )

    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Security Policy", "description": "Maybe related"}
        ])),
        patch("app.llm.pipeline.judge_duplicate", AsyncMock(return_value={
            "is_duplicate": True,
            "matched_page_id": existing.page_id,
            "confidence": "low",  # low confidence → treat as new page
        })),
        patch("app.llm.pipeline.generate_page_content", AsyncMock(return_value={
            "content": "New page content."
        })),
        patch.object(page_repo, "find_similar_for_dedup", AsyncMock(return_value=[
            {"page_id": existing.page_id, "title": "Security Policy",
             "description": "Existing", "content": "Content"},
        ])),
    ):
        results = await run_ingestion_pipeline(user=editor, **pipeline_kwargs)

    assert len(results) == 1
    assert results[0].action == "created"


@pytest.mark.asyncio
async def test_pipeline_handles_multiple_candidates(editor, pipeline_kwargs):
    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Topic A", "description": "First topic"},
            {"title": "Topic B", "description": "Second topic"},
        ])),
        patch("app.llm.pipeline.generate_page_content", AsyncMock(return_value={"content": "Content"})),
    ):
        results = await run_ingestion_pipeline(user=editor, **pipeline_kwargs)

    assert len(results) == 2
    titles = {r.title for r in results}
    assert titles == {"Topic A", "Topic B"}
    assert all(r.action == "created" for r in results)
