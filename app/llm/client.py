"""LLM client using OpenAI-compatible API with tool calling."""

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings
from app.models.user import User
from app.services.pages import find_pages

logger = logging.getLogger("pinkas.llm")

RETRIEVE_TOOL = {
    "type": "function",
    "function": {
        "name": "retrieve",
        "description": "Search the knowledge base for pages matching a query. Returns page titles, IDs, and content snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to find relevant pages"
                }
            },
            "required": ["query"]
        }
    }
}

MAX_TOOL_ITERATIONS = 5


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )


async def ask_with_retrieval(
    messages: list[dict],
    user: User,
) -> dict:
    """Answer a question using LLM with retrieve tool.

    Returns {"answer": str, "cited_pages": list[str]}
    """
    client = _get_client()

    system_msg = {
        "role": "system",
        "content": (
            "You are a knowledge assistant for an organizational wiki called Pinkas (פנקס כיס). "
            "Use the retrieve tool to search for relevant pages before answering. "
            "Always cite the page_ids you used in your answer. "
            "Each retrieved page includes a trust_tier: 'verified' means a human vouched for its accuracy, "
            "'source_checked' means claims were checked against citations, 'unverified' means agent-drafted. "
            "When pages conflict, prefer verified > source_checked > unverified. "
            "When citing an unverified page, note its trust level (e.g., 'according to an unverified page...'). "
            "Answer in the same language as the question."
        )
    }

    conversation = [system_msg] + messages
    cited_pages: list[str] = []

    for _ in range(MAX_TOOL_ITERATIONS):
        try:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=conversation,
                tools=[RETRIEVE_TOOL],
                tool_choice="auto",
            )
        except Exception as e:
            logger.error(f"LLM API error: {e}")
            return {"answer": f"Error communicating with LLM: {e}", "cited_pages": []}

        choice = response.choices[0]

        if choice.finish_reason == "tool_calls" or (choice.message.tool_calls):
            conversation.append(choice.message.model_dump())
            for tool_call in choice.message.tool_calls:
                if tool_call.function.name == "retrieve":
                    args = json.loads(tool_call.function.arguments)
                    results = await find_pages(
                        args["query"],
                        user=user,
                        ranked=True,
                        limit=10,
                        projection={"page_id": 1, "title": 1, "content": 1, "trust_tier": 1, "inbound_link_count": 1, "_id": 0},
                    )
                    for r in results:
                        r["content"] = r.get("content", "")[:500]
                    for r in results:
                        if r["page_id"] not in cited_pages:
                            cited_pages.append(r["page_id"])
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(results, ensure_ascii=False),
                    })
        else:
            answer = choice.message.content or ""
            return {"answer": answer, "cited_pages": cited_pages}

    return {"answer": "Maximum iterations reached.", "cited_pages": cited_pages}


def _parse_json_response(raw: str) -> str:
    """Strip markdown code fences from an LLM JSON response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
    return raw.strip()


async def _call_llm_json(messages: list[dict], default: object, name: str = "llm") -> object:
    """Call the LLM and parse a JSON response. Returns `default` on any error."""
    client = _get_client()
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
        )
        raw = _parse_json_response(response.choices[0].message.content or "")
        return json.loads(raw)
    except Exception as e:
        logger.error(f"LLM {name} error: {e}")
        return default


async def extract_topic_candidates(text: str, filename: str) -> list[dict]:
    """Phase 1: Extract topic titles and descriptions from a document.

    Returns list of {"title": str, "description": str}
    """
    result = await _call_llm_json(
        messages=[
            {
                "role": "system",
                "content": (
                    'You are a knowledge base curator for an organizational wiki called "Pinkas" (פנקס כיס). '
                    "Identify the distinct wiki-worthy topics in a source document."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Document: {filename}\n\n"
                    "For each distinct topic in the document, return:\n"
                    "- title: concise wiki-style title\n"
                    "- description: one sentence defining this topic (used for search and dedup)\n\n"
                    "Return a JSON array only. No other text.\n\n"
                    f"Document text:\n{text}"
                ),
            },
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


async def generate_page_content(title: str, description: str, filename: str, text: str) -> dict:
    """Phase 3A: Generate full wiki page content for a confirmed-new topic.

    Returns {"content": str}
    """
    return await _call_llm_json(
        messages=[
            {
                "role": "system",
                "content": "You are a wiki editor writing a new page for an organizational knowledge base.",
            },
            {
                "role": "user",
                "content": (
                    f'Write a complete wiki page for the topic "{title}" ({description}).\n'
                    f"Source document: {filename}\n\n"
                    "Base your content only on what the document says about this topic.\n"
                    'Return JSON only: {"content": "full markdown content"}\n\n'
                    f"Document text:\n{text}"
                ),
            },
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
