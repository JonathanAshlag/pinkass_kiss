"""Prompts for the document ingestion pipeline (Phases 1-3)."""

EXTRACT_TOPICS_SYSTEM = (
    'You are a knowledge base curator for an organizational wiki called "Pinkas" (פנקס כיס). '
    "Identify the distinct wiki-worthy topics in a source document."
)


def build_extract_topics_user(filename: str) -> tuple[str, str]:
    pre = f"Document: {filename}\n\nDocument content:\n"
    post = (
        "\n\nFor each distinct topic in the document, return:\n"
        "- title: concise wiki-style title\n"
        "- description: one sentence defining this topic (used for search and dedup)\n\n"
        "Return a JSON array only. No other text."
    )
    return pre, post


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


def build_generate_content_user(title: str, description: str, filename: str) -> tuple[str, str]:
    pre = f"Source document: {filename}\n\nDocument content:\n"
    post = (
        f'\n\nWrite a complete wiki page for the topic "{title}" ({description}).\n'
        "Base your content only on what the document says about this topic.\n"
        'Return JSON only: {"content": "full markdown content"}'
    )
    return pre, post


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
        f'Source document "{filename}":\n{text}\n\n'
        f'Existing page "{existing_title}":\n{existing_content}\n\n'
        f"New source covers this topic as: {candidate_description}\n\n"
        "Does the document add meaningful information not already in the existing page?\n"
        'Return JSON only: {"has_new_info": bool, "merged_content": "full updated markdown or null", "summary_of_additions": "brief or null"}'
    )
