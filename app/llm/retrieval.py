"""LLM-powered Q&A with knowledge-base retrieval."""

import json
import logging

from app.config import settings
from app.llm.client import _get_client
from app.models.user import User
from app.prompts.retrieval import QA_SYSTEM_PROMPT, RETRIEVE_TOOL
from app.storage.base import PageRepository
from app.services.pages import find_page_docs_fuzzy

logger = logging.getLogger("pinkas.llm")

MAX_TOOL_ITERATIONS = 5


async def ask_with_retrieval(
    messages: list[dict],
    user: User,
    page_repo: PageRepository,
) -> dict:
    """Answer a question using LLM with retrieve tool."""
    client = _get_client()

    system_msg = {"role": "system", "content": QA_SYSTEM_PROMPT}
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

        if choice.finish_reason == "tool_calls" or choice.message.tool_calls:
            conversation.append(choice.message.model_dump())
            for tool_call in choice.message.tool_calls:
                if tool_call.function.name == "retrieve":
                    args = json.loads(tool_call.function.arguments)
                    results = await find_page_docs_fuzzy(
                        args["query"],
                        fields=["page_id", "title", "content", "trust_tier", "inbound_link_count"],
                        user=user,
                        repo=page_repo,
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
