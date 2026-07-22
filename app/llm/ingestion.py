"""LLM calls for the document ingestion pipeline (Phases 1-3)."""

import logging

from app.llm.client import _call_llm_json

logger = logging.getLogger("pinkas.llm")


def _build_multimodal_content(prompt: str, content_parts: list[dict] | None) -> str | list[dict]:
    """Build a message content value with interleaved text/images in document order.

    If content_parts has no images, returns a plain string (prompt + text).
    Otherwise returns a multimodal array: prompt text, then document parts in order.
    """
    if not content_parts:
        return prompt
    has_images = any(p.get("type") == "image_url" for p in content_parts)
    if not has_images:
        return prompt
    parts: list[dict] = [{"type": "text", "text": prompt}]
    parts.extend(content_parts)
    return parts


async def extract_topic_candidates(text: str, filename: str, content_parts: list[dict] = None) -> list[dict]:
    """Phase 1: Extract topic titles and descriptions from a document.

    Returns list of {"title": str, "description": str}
    """
    prompt_text = (
        f"Document: {filename}\n\n"
        "For each distinct topic in the document, return:\n"
        "- title: concise wiki-style title\n"
        "- description: one sentence defining this topic (used for search and dedup)\n\n"
        "Return a JSON array only. No other text.\n\n"
        "Document content follows:\n"
    )
    user_content = _build_multimodal_content(prompt_text, content_parts)

    result = await _call_llm_json(
        messages=[
            {
                "role": "system",
                "content": (
                    'You are a knowledge base curator for an organizational wiki called "Pinkas" (פנקס כיס). '
                    "Identify the distinct wiki-worthy topics in a source document."
                ),
            },
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
    pages_text = "\n".join(
        f"- [{r['page_id']}] {r['title']}: {r.get('description', '')}\n  Content: {r.get('content', '')}"
        for r in search_results
    )
    return await _call_llm_json(
        messages=[
            {"role": "system", "content": "You are a knowledge deduplication specialist."},
            {
                "role": "user",
                "content": (
                    f"Candidate:\n"
                    f"  Title: {candidate['title']}\n"
                    f"  Description: {candidate.get('description', '')}\n\n"
                    f"Existing pages:\n{pages_text}\n\n"
                    "Is the candidate the same concept as any existing page?\n"
                    'Return JSON only: {"is_duplicate": bool, "matched_page_id": "id or null", "confidence": "high|medium|low"}'
                ),
            },
        ],
        default={"is_duplicate": False, "matched_page_id": None, "confidence": "low"},
        name="judge_duplicate",
    )


async def generate_page_content(title: str, description: str, filename: str, text: str, content_parts: list[dict] = None) -> dict:
    """Phase 3A: Generate full wiki page content for a confirmed-new topic.

    Returns {"content": str}
    """
    prompt_text = (
        f'Write a complete wiki page for the topic "{title}" ({description}).\n'
        f"Source document: {filename}\n\n"
        "Base your content only on what the document says about this topic.\n"
        'Return JSON only: {"content": "full markdown content"}\n\n'
        "Document content follows:\n"
    )
    user_content = _build_multimodal_content(prompt_text, content_parts)

    return await _call_llm_json(
        messages=[
            {
                "role": "system",
                "content": "You are a wiki editor writing a new page for an organizational knowledge base.",
            },
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
            {
                "role": "system",
                "content": (
                    "You are a wiki editor. Determine if a source document adds new information "
                    "to an existing wiki page, and if so produce updated content."
                ),
            },
            {
                "role": "user",
                "content": (
                    f'Existing page "{existing_title}":\n{existing_content}\n\n'
                    f'New source "{filename}" covers this topic as:\n'
                    f"  Description: {candidate_description}\n\n"
                    f"Document text:\n{text}\n\n"
                    "Does the document add meaningful information not already in the existing page?\n"
                    'Return JSON only: {"has_new_info": bool, "merged_content": "full updated markdown or null", "summary_of_additions": "brief or null"}'
                ),
            },
        ],
        default={"has_new_info": False, "merged_content": None, "summary_of_additions": None},
        name="merge_content",
    )
