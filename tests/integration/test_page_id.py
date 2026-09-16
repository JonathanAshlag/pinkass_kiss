"""Tests for the Slack-style page_id generator (app/services/page_id.py)."""

import pytest

from app.services.page_id import PageIdCollisionError, generate_page_id, normalize_title_for_id


def test_normalize_ascii_title():
    assert normalize_title_for_id("Security Policy") == "security-policy"


def test_normalize_collapses_punctuation_and_whitespace():
    assert normalize_title_for_id("  Hello,   World!!  ") == "hello-world"


def test_normalize_keeps_hebrew_script_as_is():
    assert normalize_title_for_id("תקנון חופשה") == "תקנון-חופשה"


def test_normalize_strips_niqqud_diacritics():
    # "שָׁלוֹם" with niqqud should normalize the same as the bare letters "שלום".
    assert normalize_title_for_id("שָׁלוֹם") == normalize_title_for_id("שלום")


def test_normalize_falls_back_to_page_for_symbol_only_title():
    assert normalize_title_for_id("!!!") == "page"
    assert normalize_title_for_id("🎉🎉") == "page"


@pytest.mark.asyncio
async def test_generate_page_id_shape():
    async def never_exists(_candidate: str) -> bool:
        return False

    page_id = await generate_page_id("Security Policy", never_exists)
    prefix, _, suffix = page_id.rpartition("-")
    assert prefix == "security-policy"
    assert len(suffix) == 4


@pytest.mark.asyncio
async def test_generate_page_id_retries_on_collision():
    calls = {"n": 0}

    async def exists_twice_then_free(_candidate: str) -> bool:
        calls["n"] += 1
        return calls["n"] <= 2

    page_id = await generate_page_id("Docs", exists_twice_then_free)
    assert page_id.startswith("docs-")
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_generate_page_id_raises_after_exhausting_attempts():
    async def always_exists(_candidate: str) -> bool:
        return True

    with pytest.raises(PageIdCollisionError):
        await generate_page_id("Docs", always_exists)
