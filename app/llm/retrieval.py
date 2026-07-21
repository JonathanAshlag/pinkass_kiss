"""LLM-powered Q&A with knowledge-base retrieval."""

import json
import logging

from app.config import settings
from app.llm.client import _get_client
from app.models.user import User
from app.services.pages import find_page_docs

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
                    results = await find_page_docs(
                        args["query"],
                        projection={"page_id": 1, "title": 1, "content": 1, "trust_tier": 1, "inbound_link_count": 1, "_id": 0},
                        user=user,
                        ranked=True,
                        limit=10,
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
