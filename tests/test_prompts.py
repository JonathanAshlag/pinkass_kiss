"""Unit tests for app/prompts/ — pure function coverage, no LLM calls needed."""

import pytest

from app.prompts.ingestion import (
    EXTRACT_TOPICS_SYSTEM,
    GENERATE_CONTENT_SYSTEM,
    JUDGE_DUPLICATE_SYSTEM,
    MERGE_CONTENT_SYSTEM,
    build_extract_topics_user,
    build_generate_content_user,
    build_judge_duplicate_user,
    build_merge_content_user,
)
from app.prompts.retrieval import QA_SYSTEM_PROMPT, RETRIEVE_TOOL


# ---------------------------------------------------------------------------
# System prompt constants
# ---------------------------------------------------------------------------

def test_system_prompts_are_nonempty_strings():
    for name, val in [
        ("EXTRACT_TOPICS_SYSTEM", EXTRACT_TOPICS_SYSTEM),
        ("JUDGE_DUPLICATE_SYSTEM", JUDGE_DUPLICATE_SYSTEM),
        ("GENERATE_CONTENT_SYSTEM", GENERATE_CONTENT_SYSTEM),
        ("MERGE_CONTENT_SYSTEM", MERGE_CONTENT_SYSTEM),
        ("QA_SYSTEM_PROMPT", QA_SYSTEM_PROMPT),
    ]:
        assert isinstance(val, str) and val.strip(), f"{name} must be a non-empty string"


# ---------------------------------------------------------------------------
# RETRIEVE_TOOL schema
# ---------------------------------------------------------------------------

def test_retrieve_tool_schema():
    assert RETRIEVE_TOOL["type"] == "function"
    fn = RETRIEVE_TOOL["function"]
    assert fn["name"] == "retrieve"
    params = fn["parameters"]
    assert "query" in params["properties"]
    assert "query" in params["required"]


# ---------------------------------------------------------------------------
# build_extract_topics_user
# ---------------------------------------------------------------------------

def test_extract_topics_user_contains_filename():
    result = build_extract_topics_user("report.pdf")
    assert "report.pdf" in result


def test_extract_topics_user_instructs_json_array():
    result = build_extract_topics_user("doc.txt")
    assert "JSON array" in result


def test_extract_topics_user_requests_title_and_description():
    result = build_extract_topics_user("doc.txt")
    assert "title" in result
    assert "description" in result


# ---------------------------------------------------------------------------
# build_judge_duplicate_user
# ---------------------------------------------------------------------------

CANDIDATE = {"title": "Onboarding Process", "description": "How new hires are onboarded."}

SEARCH_RESULTS = [
    {
        "page_id": "p1",
        "title": "Employee Onboarding",
        "description": "Steps for bringing new employees on board.",
        "content": "First week schedule...",
    },
    {
        "page_id": "p2",
        "title": "IT Setup",
        "description": "Laptop and accounts setup.",
        "content": "Request laptop via IT portal.",
    },
]


def test_judge_duplicate_user_contains_candidate_title():
    result = build_judge_duplicate_user(CANDIDATE, SEARCH_RESULTS)
    assert "Onboarding Process" in result


def test_judge_duplicate_user_contains_candidate_description():
    result = build_judge_duplicate_user(CANDIDATE, SEARCH_RESULTS)
    assert "How new hires are onboarded." in result


def test_judge_duplicate_user_contains_all_page_ids():
    result = build_judge_duplicate_user(CANDIDATE, SEARCH_RESULTS)
    assert "p1" in result
    assert "p2" in result


def test_judge_duplicate_user_contains_existing_page_titles():
    result = build_judge_duplicate_user(CANDIDATE, SEARCH_RESULTS)
    assert "Employee Onboarding" in result
    assert "IT Setup" in result


def test_judge_duplicate_user_empty_search_results():
    result = build_judge_duplicate_user(CANDIDATE, [])
    assert "Onboarding Process" in result
    assert "Existing pages:" in result


def test_judge_duplicate_user_candidate_missing_description():
    candidate_no_desc = {"title": "Budget Planning"}
    result = build_judge_duplicate_user(candidate_no_desc, SEARCH_RESULTS)
    assert "Budget Planning" in result


def test_judge_duplicate_user_instructs_json_response():
    result = build_judge_duplicate_user(CANDIDATE, SEARCH_RESULTS)
    assert "is_duplicate" in result
    assert "matched_page_id" in result
    assert "confidence" in result


# ---------------------------------------------------------------------------
# build_generate_content_user
# ---------------------------------------------------------------------------

def test_generate_content_user_contains_title():
    result = build_generate_content_user("Security Policy", "Company security rules", "handbook.pdf")
    assert "Security Policy" in result


def test_generate_content_user_contains_description():
    result = build_generate_content_user("Security Policy", "Company security rules", "handbook.pdf")
    assert "Company security rules" in result


def test_generate_content_user_contains_filename():
    result = build_generate_content_user("Security Policy", "Company security rules", "handbook.pdf")
    assert "handbook.pdf" in result


def test_generate_content_user_instructs_json_response():
    result = build_generate_content_user("Title", "Desc", "file.pdf")
    assert '"content"' in result


# ---------------------------------------------------------------------------
# build_merge_content_user
# ---------------------------------------------------------------------------

def test_merge_content_user_contains_existing_title():
    result = build_merge_content_user(
        existing_title="Leave Policy",
        existing_content="Employees get 20 days off.",
        candidate_description="Updated leave rules.",
        filename="hr_update.pdf",
        text="New leave rules effective 2026.",
    )
    assert "Leave Policy" in result


def test_merge_content_user_contains_existing_content():
    result = build_merge_content_user(
        existing_title="Leave Policy",
        existing_content="Employees get 20 days off.",
        candidate_description="Updated leave rules.",
        filename="hr_update.pdf",
        text="New leave rules effective 2026.",
    )
    assert "Employees get 20 days off." in result


def test_merge_content_user_contains_filename():
    result = build_merge_content_user(
        existing_title="Leave Policy",
        existing_content="Old content.",
        candidate_description="New info.",
        filename="hr_update.pdf",
        text="New text.",
    )
    assert "hr_update.pdf" in result


def test_merge_content_user_contains_new_document_text():
    result = build_merge_content_user(
        existing_title="Leave Policy",
        existing_content="Old content.",
        candidate_description="New info.",
        filename="hr_update.pdf",
        text="New leave rules effective 2026.",
    )
    assert "New leave rules effective 2026." in result


def test_merge_content_user_instructs_json_response():
    result = build_merge_content_user("T", "C", "D", "f.pdf", "text")
    assert "has_new_info" in result
    assert "merged_content" in result
    assert "summary_of_additions" in result
