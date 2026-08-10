"""Tool definitions for external LLM agents consuming Pinkas."""

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search",
        "description": (
            "Look up the meaning of a company-specific term, acronym, or entity in Pinkas, "
            "the organizational knowledge wiki. Returns short summaries (title + description) "
            "ranked by relevance. Does not contain procedures, runbooks, or how-to guidance — "
            "use this only to resolve what something IS, not how to DO something. "
            "Always call search before fetch: you cannot judge which tier of detail you need "
            "for a page you have not seen yet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Term, acronym, or entity to look up"}
            },
            "required": ["query"],
        },
    },
}

FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch",
        "description": (
            "Retrieve the full descriptive content for one or more Pinkas page ids previously "
            "returned by search. Returns title + full markdown content per id. "
            "Pinkas contains descriptive/reference knowledge (what things are), not procedures, "
            "runbooks, or how-to guidance."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "page_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit page ids from a prior search result",
                }
            },
            "required": ["page_ids"],
        },
    },
}
