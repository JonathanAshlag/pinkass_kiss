"""LLM client using OpenAI-compatible API with tool calling."""

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings
from app.db import get_db
from app.models.page import PageStatus
from app.models.user import User
from app.services.permissions import get_visible_page_ids

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


async def _retrieve_pages(query: str, user: User) -> list[dict]:
    """Search pages with permission filtering for the tool call."""
    db = get_db()
    visible_ids = await get_visible_page_ids(user)

    projection = {"page_id": 1, "title": 1, "content": 1, "trust_tier": 1, "inbound_link_count": 1, "_id": 0}
    try:
        cursor = db.pages.find(
            {
                "$text": {"$search": query},
                "status": PageStatus.published.value,
                "page_id": {"$in": list(visible_ids)},
            },
            projection,
        ).limit(10)
    except Exception:
        cursor = db.pages.find(
            {
                "status": PageStatus.published.value,
                "page_id": {"$in": list(visible_ids)},
            },
            projection,
        ).limit(10)

    results = []
    async for doc in cursor:
        results.append({
            "page_id": doc["page_id"],
            "title": doc["title"],
            "content": doc.get("content", "")[:500],
            "trust_tier": doc.get("trust_tier", "unverified"),
            "inbound_link_count": doc.get("inbound_link_count", 0),
        })

    tier_rank = {"verified": 3, "source_checked": 2, "unverified": 1}
    results.sort(key=lambda p: (tier_rank.get(p["trust_tier"], 0), p["inbound_link_count"]), reverse=True)
    return results


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
                    results = await _retrieve_pages(args["query"], user)
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


async def produce_pages(text: str, filename: str) -> list[dict]:
    """Use LLM to split extracted text into pages.

    Returns list of {"title": str, "content": str, "parent_title": str|None}
    """
    client = _get_client()

    prompt = f"""You are processing a document for an organizational knowledge wiki.
The document is named: {filename}

Extract distinct entities/topics from the following text and create separate wiki pages for each.
For each page provide:
- title: a clear title for the entity/topic
- content: the full content in Markdown format
- parent_title: suggested parent page title for hierarchy (null if it should be a root page)

Return a JSON array of objects with these fields. Only return valid JSON, no other text.

Document text:
{text[:8000]}"""

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or "[]"
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
        pages = json.loads(content)
        if not isinstance(pages, list):
            pages = [pages]
        return pages
    except Exception as e:
        logger.error(f"LLM produce error: {e}")
        return [{"title": filename, "content": text[:5000], "parent_title": None}]
