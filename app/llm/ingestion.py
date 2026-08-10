"""LLM calls for the document ingestion pipeline (Phases 1-3)."""

import logging

from app.llm.client import _call_llm_json
from app.IP.prompts.ingestion import (
    EXTRACT_TOPICS_SYSTEM,
    GENERATE_CONTENT_SYSTEM,
    JUDGE_DUPLICATE_SYSTEM,
    MERGE_CONTENT_SYSTEM,
    build_extract_topics_user,
    build_generate_content_user,
    build_judge_duplicate_user,
    build_merge_content_user,
)

logger = logging.getLogger("pinkas.llm")

def _build_multimodal_content(pre: str, content_parts: list[dict] | None, post: str = "") -> str | list[dict]:
    """Build user message content with the document as a separate block for KV-cache reuse.

    Structure: [pre_instruction, document_block, ...images..., post_instruction]
    Keeping the document as a stable prefix lets vLLM's automatic prefix caching reuse
    it across all candidates extracted from the same source document.
    """
    if not content_parts:
        return pre + post
    doc_text = "\n\n".join(p.get("text", "") for p in content_parts if p.get("type") == "text")
    parts: list[dict] = [{"type": "text", "text": pre}, {"type": "text", "text": doc_text}]
    parts.extend(p for p in content_parts if p.get("type") == "image_url")
    if post:
        parts.append({"type": "text", "text": post})
    return parts


async def extract_topic_candidates(text: str, filename: str, content_parts: list[dict] = None) -> list[dict]:
    """Phase 1: Extract topic titles and descriptions from a document.

    Returns list of {"title": str, "description": str}
    """
    pre, post = build_extract_topics_user(filename)
    user_content = _build_multimodal_content(pre, content_parts, post)

    result = await _call_llm_json(
        messages=[
            {"role": "system", "content": EXTRACT_TOPICS_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        default=[{"title": filename, "description": f"Content from {filename}"}],
        name="extract_topic_candidates",
    )
    candidates = result if isinstance(result, list) else [result]
    return candidates


async def judge_duplicate(candidate: dict, search_results: list[dict]) -> dict:
    """Phase 2: Determine whether a candidate topic matches an existing page.

    Returns {"is_duplicate": bool, "matched_page_id": str|None, "confidence": "high"|"medium"|"low"}
    """
    return await _call_llm_json(
        messages=[
            {"role": "system", "content": JUDGE_DUPLICATE_SYSTEM},
            {"role": "user", "content": build_judge_duplicate_user(candidate, search_results)},
        ],
        default={"is_duplicate": False, "matched_page_id": None, "confidence": "low"},
        name="judge_duplicate",
    )


async def generate_page_content(title: str, description: str, filename: str, text: str, content_parts: list[dict] = None) -> dict:
    """Phase 3A: Generate full wiki page content for a confirmed-new topic.

    Returns {"content": str}
    """
    pre, post = build_generate_content_user(title, description, filename)
    user_content = _build_multimodal_content(pre, content_parts, post)

    return await _call_llm_json(
        messages=[
            {"role": "system", "content": GENERATE_CONTENT_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        default={"content": text},
        name="generate_page_content",
    )


async def merge_content(existing_title: str, existing_content: str, candidate_description: str, filename: str, text: str) -> dict:
    """Phase 3B: Check if a source document adds new information to an existing page.

    Returns {"has_new_info": bool, "merged_content": str|None, "summary_of_additions": str|None}
    """
    return await _call_llm_json(
        messages=[
            {"role": "system", "content": MERGE_CONTENT_SYSTEM},
            {"role": "user", "content": build_merge_content_user(existing_title, existing_content, candidate_description, filename, text)},
        ],
        default={"has_new_info": False, "merged_content": None, "summary_of_additions": None},
        name="merge_content",
    )
