"""Prompts for the document ingestion pipeline (Phases 1-3)."""

EXTRACT_TOPICS_SYSTEM = (
    'You are a knowledge base curator for an organizational wiki called "Pinkas" (פנקס כיס). '
    "Identify the distinct wiki-worthy topics in a source document."
)


def build_extract_topics_user(filename: str) -> str:
    return (
        f"Document: {filename}\n\n"
        "For each distinct topic in the document, return:\n"
        "- title: concise wiki-style title\n"
        "- description: one sentence defining this topic (used for search and dedup)\n\n"
        "Return a JSON array only. No other text.\n\n"
        "Document content follows:\n"
    )


JUDGE_DUPLICATE_SYSTEM = "You are a knowledge deduplication specialist."


def build_judge_duplicate_user(candidate: dict, search_results: list[dict]) -> str:
    pages_text = "\n".join(
        f"- [{r['page_id']}] {r['title']}: {r.get('description', '')}\n  Content: {r.get('content', '')}"
        for r in search_results
    )
    return (
        f"Candidate:\n"
        f"  Title: {candidate['title']}\n"
        f"  Description: {candidate.get('description', '')}\n\n"
        f"Existing pages:\n{pages_text}\n\n"
        "Is the candidate the same concept as any existing page?\n"
        'Return JSON only: {"is_duplicate": bool, "matched_page_id": "id or null", "confidence": "high|medium|low"}'
    )


GENERATE_CONTENT_SYSTEM = "You are a wiki editor writing a new page for an organizational knowledge base."


def build_generate_content_user(title: str, description: str, filename: str) -> str:
    return (
        f'Write a complete wiki page for the topic "{title}" ({description}).\n'
        f"Source document: {filename}\n\n"
        "Base your content only on what the document says about this topic.\n"
        'Return JSON only: {"content": "full markdown content"}\n\n'
        "Document content follows:\n"
    )


MERGE_CONTENT_SYSTEM = (
    "You are a wiki editor. Determine if a source document adds new information "
    "to an existing wiki page, and if so produce updated content."
)


def build_merge_content_user(
    existing_title: str,
    existing_content: str,
    candidate_description: str,
    filename: str,
    text: str,
) -> str:
    return (
        f'Existing page "{existing_title}":\n{existing_content}\n\n'
        f'New source "{filename}" covers this topic as:\n'
        f"  Description: {candidate_description}\n\n"
        f"Document text:\n{text}\n\n"
        "Does the document add meaningful information not already in the existing page?\n"
        'Return JSON only: {"has_new_info": bool, "merged_content": "full updated markdown or null", "summary_of_additions": "brief or null"}'
    )
