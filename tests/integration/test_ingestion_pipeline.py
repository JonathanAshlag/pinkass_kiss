"""Tests for the LLM ingestion pipeline (extract → dedup → propose create/merge/link candidates)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.llm import batch_store
from app.llm.pipeline import run_ingestion_pipeline
from app.models.page import PageCreate
from app.models.user import User, PermissionLevel
from app.services.pages import create_page


@pytest.fixture(autouse=True)
def _clear_batch_store():
    batch_store._BATCHES.clear()
    yield
    batch_store._BATCHES.clear()


@pytest.fixture
def editor():
    return User(user_id="editor1", name="Editor", permission_level=PermissionLevel.editor)


@pytest.fixture
def batch():
    return batch_store.create_batch("batch1", "editor1", "unverified")


@pytest.fixture
def pipeline_kwargs(page_repo, batch):
    return dict(
        batch_id=batch.batch_id,
        file_id_str="file123",
        filename="doc.pdf",
        text="Full document text",
        content_parts=[{"type": "text", "text": "Full document text"}],
        ingestion_context=None,
        page_repo=page_repo,
    )


def _candidates(batch_id: str = "batch1"):
    return list(batch_store.get_batch(batch_id).candidates.values())


@pytest.mark.asyncio
async def test_pipeline_proposes_create_for_new_candidate(pipeline_kwargs):
    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Security Policy", "description": "MFA and access guidelines"}
        ])),
        patch("app.llm.pipeline.generate_page_content", AsyncMock(return_value={
            "content": "## Security Policy\nAll access requires MFA."
        })),
    ):
        await run_ingestion_pipeline(**pipeline_kwargs)

    candidates = _candidates()
    assert len(candidates) == 1
    c = candidates[0]
    assert c.action == "create"
    assert c.title == "Security Policy"
    assert c.status == "pending"
    assert c.content == "## Security Policy\nAll access requires MFA."


@pytest.mark.asyncio
async def test_pipeline_skips_candidates_without_title(pipeline_kwargs):
    with patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
        {"title": "", "description": "No title here"},
        {"title": "  ", "description": "Also blank"},
    ])):
        await run_ingestion_pipeline(**pipeline_kwargs)

    assert _candidates() == []


@pytest.mark.asyncio
async def test_pipeline_no_candidates_extracted(pipeline_kwargs):
    with patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[])):
        await run_ingestion_pipeline(**pipeline_kwargs)

    assert _candidates() == []


@pytest.mark.asyncio
async def test_pipeline_proposes_merge_with_new_info(editor, page_repo, pipeline_kwargs):
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
        patch("app.llm.pipeline.find_similar_pages_for_dedup", AsyncMock(return_value=[
            {"page_id": existing.page_id, "title": "Security Policy",
             "description": "Existing", "content": "Old content"},
        ])),
    ):
        await run_ingestion_pipeline(**pipeline_kwargs)

    candidates = _candidates()
    assert len(candidates) == 1
    c = candidates[0]
    assert c.action == "merge"
    assert c.status == "pending"
    assert c.matched_page_id == existing.page_id
    assert c.content == "Old content\n\nNew MFA requirement added."
    assert c.matched_page_content == "Old content"


@pytest.mark.asyncio
async def test_pipeline_proposes_link_with_no_new_info(editor, page_repo, pipeline_kwargs):
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
        patch("app.llm.pipeline.find_similar_pages_for_dedup", AsyncMock(return_value=[
            {"page_id": existing.page_id, "title": "Security Policy",
             "description": "Existing", "content": "Complete content"},
        ])),
    ):
        await run_ingestion_pipeline(**pipeline_kwargs)

    candidates = _candidates()
    assert len(candidates) == 1
    c = candidates[0]
    assert c.action == "link"
    assert c.matched_page_id == existing.page_id


@pytest.mark.asyncio
async def test_pipeline_ignores_low_confidence_duplicate(editor, page_repo, pipeline_kwargs):
    existing = await create_page(
        PageCreate(title="Security Policy", description="Existing", content="Content"),
        editor,
        page_repo,
    )

    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Security Policy Draft", "description": "Maybe related"}
        ])),
        patch("app.llm.pipeline.judge_duplicate", AsyncMock(return_value={
            "is_duplicate": True,
            "matched_page_id": existing.page_id,
            "confidence": "low",  # low confidence → treat as new page
        })),
        patch("app.llm.pipeline.generate_page_content", AsyncMock(return_value={
            "content": "New page content."
        })),
        patch("app.llm.pipeline.find_similar_pages_for_dedup", AsyncMock(return_value=[
            {"page_id": existing.page_id, "title": "Security Policy",
             "description": "Existing", "content": "Content"},
        ])),
    ):
        await run_ingestion_pipeline(**pipeline_kwargs)

    candidates = _candidates()
    assert len(candidates) == 1
    assert candidates[0].action == "create"


@pytest.mark.asyncio
async def test_pipeline_handles_multiple_candidates(pipeline_kwargs):
    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Topic A", "description": "First topic"},
            {"title": "Topic B", "description": "Second topic"},
        ])),
        patch("app.llm.pipeline.generate_page_content", AsyncMock(return_value={"content": "Content"})),
    ):
        await run_ingestion_pipeline(**pipeline_kwargs)

    candidates = _candidates()
    assert len(candidates) == 2
    titles = {c.title for c in candidates}
    assert titles == {"Topic A", "Topic B"}
    assert all(c.action == "create" for c in candidates)


@pytest.mark.asyncio
async def test_pipeline_dedup_ignores_same_batch_candidates(page_repo, pipeline_kwargs):
    """Nothing is written until approval, so two same-batch candidates can never match each other."""
    with (
        patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[
            {"title": "Security Policy", "description": "First mention"},
            {"title": "Security Policy", "description": "Second mention, same doc"},
        ])),
        patch("app.llm.pipeline.generate_page_content", AsyncMock(return_value={"content": "Content"})),
    ):
        await run_ingestion_pipeline(**pipeline_kwargs)

    candidates = _candidates()
    assert len(candidates) == 2
    assert all(c.action == "create" for c in candidates)


@pytest.mark.asyncio
async def test_pipeline_passes_ingestion_context_through_to_extraction(page_repo, batch):
    with patch("app.llm.pipeline.extract_topic_candidates", AsyncMock(return_value=[])) as mock_extract:
        await run_ingestion_pipeline(
            batch_id=batch.batch_id,
            file_id_str="file123",
            filename="doc.pdf",
            text="text",
            content_parts=[],
            ingestion_context="Focus on security sections only",
            page_repo=page_repo,
        )

    mock_extract.assert_awaited_once_with("text", "doc.pdf", [], "Focus on security sections only")
