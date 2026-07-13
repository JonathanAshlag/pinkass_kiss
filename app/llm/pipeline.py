"""Structured LLM ingestion pipeline: extract → dedup → create/merge."""

import logging
from datetime import datetime, timezone

from app.db import get_db
from app.llm.client import (
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
from app.services.mutations import apply_page_mutation
from app.services.pages import find_pages, get_page, compute_content_hash

logger = logging.getLogger("pinkas.pipeline")


async def run_ingestion_pipeline(
    text: str,
    filename: str,
    file_id_str: str,
    user: User,
    initial_trust_tier: str,
) -> list[dict]:
    """Run the 3-phase ingestion pipeline for a single document.

    Phase 1: Extract topic candidates (title + description).
    Phase 2: Dedup check per candidate (search + LLM judge).
    Phase 3A: Generate full content and create new page.
    Phase 3B: Merge new info into existing page (or just link source).
    """
    db = get_db()
    file_ref = Reference(type=ReferenceType.file, file_id=file_id_str)
    results = []

    # Phase 1
    candidates = await extract_topic_candidates(text, filename)
    logger.info(f"Extracted {len(candidates)} topic candidates from {filename}")

    for candidate in candidates:
        title = candidate.get("title", "").strip()
        description = candidate.get("description", "").strip()
        if not title:
            continue

        # Phase 2: search all non-deleted pages (no permission filter — ingestion is system-level)
        search_results = await find_pages(
            f"{title} {description}",
            statuses=[s for s in PageStatus if s != PageStatus.deleted],
            limit=5,
            projection={"page_id": 1, "title": 1, "description": 1, "content": 1, "_id": 0},
        )

        matched_page: Page | None = None
        if search_results:
            verdict = await judge_duplicate(candidate, search_results)
            if verdict.get("is_duplicate") and verdict.get("confidence") in ("high", "medium"):
                matched_page = await get_page(verdict["matched_page_id"])

        if matched_page:
            # Phase 3B — existing page found
            merge = await merge_content(
                matched_page.title,
                matched_page.content,
                description,
                filename,
                text,
            )

            updated_refs = list(matched_page.references) + [file_ref]
            refs_json = [r.model_dump(mode="json") for r in updated_refs]

            if merge.get("has_new_info") and merge.get("merged_content"):
                mutation_result = await apply_page_mutation(
                    RequestType.edit,
                    user,
                    data=PageUpdate(content=merge["merged_content"], references=updated_refs),
                    page_id=matched_page.page_id,
                )
                # Always record the new source reference regardless of workflow routing
                if mutation_result["status"] == "pending_approval":
                    await db.pages.update_one(
                        {"page_id": matched_page.page_id},
                        {"$set": {"references": refs_json}},
                    )
                action_status = mutation_result["status"]
            else:
                # No new info — just record the new source reference
                await db.pages.update_one(
                    {"page_id": matched_page.page_id},
                    {"$set": {"references": refs_json}},
                )
                action_status = "linked"

            results.append({
                "page_id": matched_page.page_id,
                "title": matched_page.title,
                "status": action_status,
                "action": "merged" if merge.get("has_new_info") else "linked",
            })
            continue

        # Phase 3A — new topic
        content_result = await generate_page_content(title, description, filename, text)
        page_content = content_result.get("content", text)

        page_data = PageCreate(
            title=title,
            description=description,
            content=page_content,
            references=[file_ref],
        )
        mutation_result = await apply_page_mutation(
            RequestType.create,
            user,
            data=page_data,
        )
        action_status = mutation_result["status"]

        # Admin-verified trust tier promotion only applies to directly-published pages
        if (
            action_status == "published"
            and user.permission_level == PermissionLevel.admin
            and initial_trust_tier == TrustTier.verified.value
        ):
            page_id = (mutation_result.get("page") or {}).get("page_id")
            if page_id:
                await db.pages.update_one(
                    {"page_id": page_id},
                    {"$set": {
                        "trust_tier": TrustTier.verified.value,
                        "verified_content_hash": compute_content_hash(page_content),
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                        "verified_by": user.user_id,
                    }},
                )

        results.append({
            "page_id": (mutation_result.get("page") or {}).get("page_id", ""),
            "title": title,
            "status": action_status,
            "action": "created",
        })

    return results
