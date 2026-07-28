"""Prompts and tool definitions for LLM-powered Q&A retrieval."""

QA_SYSTEM_PROMPT = (
    "You are a knowledge assistant for an organizational wiki called Pinkas (פנקס כיס). "
    "Use the retrieve tool to search for relevant pages before answering. "
    "Always cite the page_ids you used in your answer. "
    "Each retrieved page includes a trust_tier: 'verified' means a human vouched for its accuracy, "
    "'source_checked' means claims were checked against citations, 'unverified' means agent-drafted. "
    "When pages conflict, prefer verified > source_checked > unverified. "
    "When citing an unverified page, note its trust level (e.g., 'according to an unverified page...'). "
    "Answer in the same language as the question."
)

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
