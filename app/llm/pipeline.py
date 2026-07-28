"""Structured LLM ingestion pipeline: extract → dedup → create/merge."""

import logging

from app.llm.ingestion import (
    extract_topic_candidates,
    generate_page_content,
    judge_duplicate,
    merge_content,
)
from app.models.page import (
    Page, PageCreate, PageUpdate, PageStatus, Reference, ReferenceType, TrustTier,
)
from app.models.request import RequestType
from app.models.user import User, PermissionLevel
from app.storage.base import PageRepository, RequestRepository
from app.services.mutations import apply_page_mutation
from app.services.pages import find_page_docs, get_page, set_page_references

logger = logging.getLogger("pinkas.pipeline")


async def run_ingestion_pipeline(
    text: str,
    content_parts: list[dict],
    filename: str,
    file_id_str: str,
    user: User,
    initial_trust_tier: str,
    page_repo: PageRepository,
    req_repo: RequestRepository,
) -> list[dict]:
    """Run the 3-phase ingestion pipeline for a single document."""
    file_ref = Reference(type=ReferenceType.file, file_id=file_id_str)
    results = []

    candidates = await extract_topic_candidates(text, filename, content_parts)
    logger.info(f"Extracted {len(candidates)} topic candidates from {filename}")

    for candidate in candidates:
        title = candidate.get("title", "").strip()
        description = candidate.get("description", "").strip()
        if not title:
            continue

        search_results = await find_page_docs(
            f"{title} {description}",
            fields=["page_id", "title", "description", "content"],
            user=user,
            repo=page_repo,
            statuses=[s for s in PageStatus if s != PageStatus.deleted],
            limit=5,
        )

        matched_page: Page | None = None
        if search_results:
            verdict = await judge_duplicate(candidate, search_results)
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
                action_status = mutation_result["status"]
            else:
                await set_page_references(matched_page.page_id, updated_refs, page_repo)
                action_status = "linked"

            results.append({
                "page_id": matched_page.page_id,
                "title": matched_page.title,
                "status": action_status,
                "action": "merged" if merge.get("has_new_info") else "linked",
            })
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
        mutation_result = await apply_page_mutation(
            RequestType.create,
            user,
            page_repo=page_repo,
            req_repo=req_repo,
            data=page_data,
            trust_tier=TrustTier.verified if should_verify else None,
        )
        action_status = mutation_result["status"]

        results.append({
            "page_id": (mutation_result.get("page") or {}).get("page_id", ""),
            "title": title,
            "status": action_status,
            "action": "created",
        })

    return results
