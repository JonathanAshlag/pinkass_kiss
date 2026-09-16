"""Structured LLM ingestion pipeline: extract → dedup → propose create/merge/link candidates.

Generates proposals only — nothing is written to the page DB here. Each
candidate is handed to the in-memory batch store (`app/llm/batch_store.py`)
for the user to review; the actual `apply_page_mutation` call happens later,
at approval time, in `app/routers/produce.py`.
"""

import logging
import uuid

from app.llm.batch_store import Candidate, add_candidate, register_file, set_progress
from app.llm.ingestion import (
    extract_topic_candidates,
    generate_page_content,
    judge_duplicate,
    merge_content,
)
from app.storage.base import PageRepository
from app.services.pages import find_similar_pages_for_dedup, get_page

logger = logging.getLogger("pinkas.pipeline")


async def run_ingestion_pipeline(
    batch_id: str,
    file_id_str: str,
    filename: str,
    text: str,
    content_parts: list[dict],
    ingestion_context: str | None,
    page_repo: PageRepository,
) -> None:
    """Run the 3-phase ingestion pipeline for a single document, proposing candidates."""
    register_file(batch_id, file_id_str)

    candidates = await extract_topic_candidates(text, filename, content_parts, ingestion_context)
    logger.info(f"Extracted {len(candidates)} topic candidates from {filename}")
    set_progress(batch_id, f"Extracted {len(candidates)} candidates from {filename}")

    total = len(candidates)
    for i, candidate in enumerate(candidates, start=1):
        title = candidate.get("title", "").strip()
        description = candidate.get("description", "").strip()
        if not title:
            continue

        set_progress(batch_id, f"Analyzing candidate {i}/{total}: {title}")

        similar_pages = await find_similar_pages_for_dedup(title, page_repo)

        matched_page = None
        if similar_pages:
            verdict = await judge_duplicate(candidate, similar_pages, ingestion_context)
            if verdict.get("is_duplicate") and verdict.get("confidence") in ("high", "medium"):
                matched_page = await get_page(verdict["matched_page_id"], page_repo)

        if matched_page:
            set_progress(
                batch_id,
                f"Candidate {i}/{total} '{title}' matches existing page "
                f"'{matched_page.title}' — checking for new information...",
            )
            merge = await merge_content(
                matched_page.title,
                matched_page.content,
                description,
                filename,
                text,
                ingestion_context,
            )

            if merge.get("has_new_info") and merge.get("merged_content"):
                set_progress(
                    batch_id,
                    f"Candidate {i}/{total}: preparing an update to '{matched_page.title}'",
                )
                add_candidate(batch_id, Candidate(
                    candidate_id=str(uuid.uuid4()),
                    file_id=file_id_str,
                    filename=filename,
                    action="merge",
                    title=matched_page.title,
                    description=matched_page.description,
                    content=merge["merged_content"],
                    aliases=list(matched_page.aliases),
                    matched_page_id=matched_page.page_id,
                    matched_page_title=matched_page.title,
                    matched_page_description=matched_page.description,
                    matched_page_content=matched_page.content,
                    matched_page_aliases=list(matched_page.aliases),
                ))
            else:
                set_progress(
                    batch_id,
                    f"Candidate {i}/{total}: '{matched_page.title}' already covers this "
                    "— proposing a reference link",
                )
                add_candidate(batch_id, Candidate(
                    candidate_id=str(uuid.uuid4()),
                    file_id=file_id_str,
                    filename=filename,
                    action="link",
                    title=matched_page.title,
                    description=matched_page.description,
                    content="",
                    matched_page_id=matched_page.page_id,
                    matched_page_title=matched_page.title,
                    matched_page_description=matched_page.description,
                    matched_page_content=matched_page.content,
                    matched_page_aliases=list(matched_page.aliases),
                ))
            continue

        set_progress(
            batch_id,
            f"Candidate {i}/{total} '{title}': no match found — drafting a new page...",
        )
        content_result = await generate_page_content(
            title, description, filename, text, content_parts, ingestion_context,
        )
        page_content = content_result.get("content", text)
        set_progress(batch_id, f"Candidate {i}/{total}: drafted new page '{title}'")

        add_candidate(batch_id, Candidate(
            candidate_id=str(uuid.uuid4()),
            file_id=file_id_str,
            filename=filename,
            action="create",
            title=title,
            description=description,
            content=page_content,
        ))
