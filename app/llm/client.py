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
    """Strip markdown code fences from an LLM JSON response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
    return raw.strip()


async def _call_llm_json(messages: list[dict], default: object, name: str = "llm") -> object:
    """Call the LLM and parse a JSON response. Returns `default` on any error."""
    try:
        response = await get_client().chat.completions.create(
            model=settings.openai_model,
            messages=messages,
        )
        raw = _parse_json_response(response.choices[0].message.content or "")
        return json.loads(raw)
    except Exception as e:
        logger.error(f"LLM {name} error: {e}")
        return default
