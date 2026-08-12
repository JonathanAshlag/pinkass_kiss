"""Minimal example client for the Pinkas agent-api (app/routers/agent_api.py).

Shows how an agent would call the 4 data routes over HTTP (everything except
GET /agent/tools, which just returns tool schemas). Every call needs:

  X-API-Key    - obtained once via `POST /agents` as an admin
                 (see app/routers/agents.py; e.g.
                 `curl -X POST $BASE_URL/agents -H "x-user-id: admin1" \
                    -H "Content-Type: application/json" -d '{"name": "demo-agent"}'`)
  X-Session-Id - any client-chosen string identifying this agent session

`GET /agent/bundles/{name}` additionally needs a bundle that already exists
(created via `PUT /bundles/{name}` as an admin, see app/routers/bundles.py), e.g.
against the `scripts/seed_db.py` sample data:

  curl -X PUT $BASE_URL/bundles/pets -H "x-user-id: admin1" \
    -H "Content-Type: application/json" \
    -d '{"entries": [{"page_id": "dog", "content_form": "full_info"}, \
                      {"page_id": "cat", "content_form": "description"}]}'

The demo queries below are written to match `scripts/seed_db.py`'s sample pages
(run `python scripts/seed_db.py` first to have real data to search/fetch/scan against).
Configure via env vars and run:

  BASE_URL=http://localhost:8080 API_KEY=... SESSION_ID=demo-session \
    BUNDLE_NAME=pets python scripts/agent_client_example.py
"""

import json
import os
import sys

import requests
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from openai import OpenAI

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
API_KEY = os.environ.get("API_KEY", "ake_noooGRAMsgzS7UobOF6WnPqEi31QYLyVE_3z-BE")
SESSION_ID = os.environ.get("SESSION_ID", "demo-session")
BUNDLE_NAME = os.environ.get("BUNDLE_NAME", "כלבים")

# Local OpenAI-compatible LLM server (vLLM, Ollama, LM Studio, ...) — same defaults as app/config.py.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "not-needed")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "local-model")

if not API_KEY:
    sys.exit(
        "API_KEY is required. Provision one first via POST /agents as an admin "
        "(see module docstring), then set the API_KEY env var."
    )

HEADERS = {"X-API-Key": API_KEY, "X-Session-Id": SESSION_ID}


@tool
def search(query: str) -> str:
    """POST /agent/search - fuzzy search over pages (short tier: title + description). Returns plain text for agent context.

    Args:
        query: Term, acronym, or entity to look up.
    """
    resp = requests.post(f"{BASE_URL}/agent/search", json={"query": query}, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if data.get("miss"):
        lines = [data.get("message") or "No matches found."]
        lines += [
            f'  near miss: {c["title"]} (page_id: {c["page_id"]}) score={c["score"]:.2f}'
            for c in data.get("nearest_candidates", [])
        ]
        return "\n".join(lines)
    return "\n".join(
        f'{r["title"]} (page_id: {r["page_id"]}) — {r["description"]} [{r["trust_tier"]}]'
        for r in data.get("results", [])
    ) or "No results."


@tool
def fetch(page_ids: list[str]) -> str:
    """POST /agent/fetch - fetch full content (long tier) for explicit page ids. Returns plain text for agent context.

    Args:
        page_ids: Explicit page ids from a prior search result.
    """
    resp = requests.post(f"{BASE_URL}/agent/fetch", json={"page_ids": page_ids}, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    lines = [
        f'## {r["title"]} (page_id: {r["page_id"]}) [{r["trust_tier"]}]\n{r["content"]}'
        for r in data.get("results", [])
    ]
    lines += [f'page_id: {u["page_id"]} unavailable ({u["reason"]})' for u in data.get("unavailable", [])]
    return "\n\n".join(lines) or "No pages fetched."


# `@tool` turns search/fetch into StructuredTool instances: call via .invoke({...}),
# and their OpenAI-compatible schema (equivalent to GET /agent/tools) is available at
# `search.tool_call_schema` / bind them directly with `chat_model.bind_tools([search, fetch])`.
TOOLS = [search, fetch]
OPENAI_TOOLS = [convert_to_openai_tool(t) for t in TOOLS]
TOOL_FUNCTIONS = {t.name: t for t in TOOLS}
MAX_TOOL_ITERATIONS = 5


def ask_llm(question: str) -> str:
    """Drive a local OpenAI-compatible LLM through a tool-calling loop using search/fetch."""
    client = OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
    conversation = [
        {
            "role": "system",
            "content": (
                "You are an agent with access to Pinkas, an organizational knowledge wiki, "
                "via the search and fetch tools. Always call search to look up a term before "
                "answering; never rely on prior knowledge."
            ),
        },
        {"role": "user", "content": question},
    ]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=conversation,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
        )
        message = response.choices[0].message
        conversation.append(message.model_dump())

        if not message.tool_calls:
            return message.content or ""

        for tool_call in message.tool_calls:
            fn = TOOL_FUNCTIONS.get(tool_call.function.name)
            args = json.loads(tool_call.function.arguments or "{}")
            result = fn.invoke(args) if fn else f"unknown tool: {tool_call.function.name}"
            conversation.append(
                {"role": "tool", "tool_call_id": tool_call.id, "content": result}
            )

    return "Maximum tool iterations reached."


def scan(text: str) -> str:
    """POST /agent/scan - passively scan arbitrary text for title/alias matches. Returns plain text for agent context."""
    resp = requests.post(f"{BASE_URL}/agent/scan", json={"text": text}, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    return "\n".join(
        f'{m["title"]} (page_id: {m["page_id"]}) — {m["description"]} [{m["trust_tier"]}]'
        for m in data.get("matches", [])
    ) or "No matches."


def get_bundle(name: str) -> str:
    """GET /agent/bundles/{name} - fetch a curated bundle, rendered against current page content. Returns plain text for agent context."""
    resp = requests.get(f"{BASE_URL}/agent/bundles/{name}", headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    return f'# {data["bundle_name"]}\n\n{data["rendered_text"]}'


if __name__ == "__main__":
    print(f"== search ==")
    print(search.invoke({"query": "ho"}))  # matches the "dogs" page title in scripts/seed_db.py

    print(f"\n== fetch (page_id=dogs) ==")
    print(fetch.invoke({"page_ids": ["dos"]}))

    print(f"\n== scan ==")
    # "גרמן שפרד" / "חתול" are page titles, "Felis catus" is the cat page's alias.
    print(scan("מישהו שאל על גרמן שפרד ועל חתול (Felis catus) בתור חיות מחמד."))

    print(f"\n== get_bundle (name={BUNDLE_NAME}) ==")
    print(get_bundle(BUNDLE_NAME))

    print(f"\n== llm tool-calling (term=כלב) ==")
    print(ask_llm('מה זה "כלב"? תשתמש בכלי החיפוש שיש לך כדי לבדוק לפני שאתה עונה.'))
