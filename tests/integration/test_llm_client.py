"""Unit tests for app/llm/client.py response parsing — pure function coverage, no LLM calls needed."""

from app.llm.client import _parse_json_response


def test_parse_json_response_plain():
    assert _parse_json_response('[{"title": "A"}]') == '[{"title": "A"}]'


def test_parse_json_response_strips_code_fence():
    raw = '```json\n[{"title": "A"}]\n```'
    assert _parse_json_response(raw) == '[{"title": "A"}]'


def test_parse_json_response_strips_think_block():
    raw = '<think>\nreasoning about the answer\n</think>\n\n[{"title": "A"}]'
    assert _parse_json_response(raw) == '[{"title": "A"}]'


def test_parse_json_response_strips_think_block_then_code_fence():
    raw = '<think>\nreasoning\n</think>\n\n```json\n[{"title": "A"}]\n```'
    assert _parse_json_response(raw) == '[{"title": "A"}]'
