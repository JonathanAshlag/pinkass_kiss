"""Generic LLM plumbing: client factory and JSON response helpers."""

import json
import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger("pinkas.llm")

_client = AsyncOpenAI(
    base_url=settings.openai_base_url,
    api_key=settings.openai_api_key,
    timeout=600.0,
)


def get_client() -> AsyncOpenAI:
    return _client


def _parse_json_response(raw: str) -> str:
    """Strip a leading <think>...</think> reasoning block and markdown code fences from an LLM JSON response."""
    raw = raw.strip()
    if raw.startswith("<think>"):
        end = raw.find("</think>")
        if end != -1:
            raw = raw[end + len("</think>"):].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
    return raw.strip()


async def _call_llm_json(messages: list[dict], name: str = "llm") -> object:
    """Call the LLM and parse a JSON response. Raises on any error (bad response, timeout,
    unparseable JSON) — callers must not fabricate placeholder content when the LLM is
    unavailable or misbehaving; the ingestion pipeline relies on this to abort a batch
    rather than propose a bogus candidate."""
    try:
        response = await get_client().chat.completions.create(
            model=settings.openai_model,
            messages=messages,
        )
        raw = _parse_json_response(response.choices[0].message.content or "")
        return json.loads(raw)
    except Exception as e:
        logger.error(f"LLM {name} error: {e}")
        raise
