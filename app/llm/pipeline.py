"""Structured LLM ingestion pipeline: extract → dedup → create/merge."""

import logging
from typing import Literal

from pydantic import BaseModel

from app.llm.ingestion import (
    extract_topic_candidates,
    generate_page_content,
    judge_duplicate,
    merge_content,
)
from app.models.page import (
    Page, PageCreate, PageUpdate, Reference, ReferenceType, TrustTier,
)
from app.models.request import RequestType
from app.models.user import User, PermissionLevel
from app.search_config import DEDUP_SIMILARITY_THRESHOLD, DEDUP_TOP_K
from app.storage.base import PageRepository, RequestRepository
from app.services.mutations import apply_page_mutation
from app.services.pages import get_page, set_page_references

logger = logging.getLogger("pinkas.pipeline")


class PageIngestOutcome(BaseModel):
    """Result of ingesting a single topic candidate from a document."""
    page_id: str
    title: str
    action: Literal["created", "merged", "linked"]
    status: str  # "published" | "pending_approval" | "linked"


async def run_ingestion_pipeline(
    text: str,
    content_parts: list[dict],
    filename: str,
    file_id_str: str,
    user: User,
    initial_trust_tier: str,
    page_repo: PageRepository,
    req_repo: RequestRepository,
) -> list[PageIngestOutcome]:
    """Run the 3-phase ingestion pipeline for a single document."""
    file_ref = Reference(type=ReferenceType.file, file_id=file_id_str)
    results: list[PageIngestOutcome] = []

    candidates = await extract_topic_candidates(text, filename, content_parts)
    logger.info(f"Extracted {len(candidates)} topic candidates from {filename}")

    for candidate in candidates:
        title = candidate.get("title", "").strip()
        description = candidate.get("description", "").strip()
        if not title:
            continue

        similar_pages = await page_repo.find_similar_for_dedup(
            title,
            description,
            threshold=DEDUP_SIMILARITY_THRESHOLD,
            limit=DEDUP_TOP_K,
            fields=["page_id", "title", "description", "content"],
        )

        matched_page: Page | None = None
        if similar_pages:
            verdict = await judge_duplicate(candidate, similar_pages)
            if verdict.get("is_duplicate") and verdict.get("confidence") in ("high", "medium"):
                matched_page = await get_page(verdict["matched_page_id"], page_repo)

        if matched_page:
            merge = await merge_content(
                matched_page.title,
                matched_page.content,
                description,
                filename,
                text,
            )
            updated_refs = list(matched_page.references) + [file_ref]

            if merge.get("has_new_info") and merge.get("merged_content"):
                mutation_result = await apply_page_mutation(
                    RequestType.edit,
                    user,
                    page_repo=page_repo,
                    req_repo=req_repo,
                    data=PageUpdate(content=merge["merged_content"], references=updated_refs),
                    page_id=matched_page.page_id,
                )
                results.append(PageIngestOutcome(
                    page_id=matched_page.page_id,
                    title=matched_page.title,
                    action="merged",
                    status=mutation_result.status,
                ))
            else:
                await set_page_references(matched_page.page_id, updated_refs, page_repo)
                results.append(PageIngestOutcome(
                    page_id=matched_page.page_id,
                    title=matched_page.title,
                    action="linked",
                    status="linked",
                ))
            continue

        content_result = await generate_page_content(title, description, filename, text, content_parts)
        page_content = content_result.get("content", text)

        page_data = PageCreate(
            title=title,
            description=description,
            content=page_content,
            references=[file_ref],
        )
        should_verify = (
            user.permission_level == PermissionLevel.admin
            and initial_trust_tier == TrustTier.verified.value
        )
        try:
            mutation_result = await apply_page_mutation(
                RequestType.create,
                user,
                page_repo=page_repo,
                req_repo=req_repo,
                data=page_data,
                trust_tier=TrustTier.verified if should_verify else None,
            )
        except ValueError:
            logger.warning(f"Skipping candidate '{title}': page_id already taken")
            continue
        results.append(PageIngestOutcome(
            page_id=mutation_result.page["page_id"] if mutation_result.page else "",
            title=title,
            action="created",
            status=mutation_result.status,
        ))

    return results
