# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pinkas (פנקס כיס) is a self-hosted organizational knowledge wiki with hierarchical pages, approval workflows, trust-tier verification, LLM-powered Q&A, and document ingestion. It runs as a FastAPI backend + Streamlit frontend, backed by MongoDB and a local OpenAI-compatible LLM server.

## Commands

```bash
# Install
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Run API server
uvicorn app.main:app --host 0.0.0.0 --port 8080

# Run Streamlit GUI
streamlit run streamlit_app/app.py --server.port 8501

# Database setup (indexes + seed data)
python init_db.py
python seed_db.py

# Run all tests (uses mongomock — no MongoDB needed)
pytest tests/ -v

# Run a single test file or test
pytest tests/test_workflows.py -v
pytest tests/test_workflows.py::test_no_workflow_auto_publish -v

# Docker
docker-compose up -d
```

## Architecture

**Two-process deployment:** FastAPI API (port 8080) + Streamlit UI (port 8501). The UI calls the API via `PINKAS_API_URL`.

**Central mutation seam:** All page create/edit/delete operations flow through `app/services/mutations.py:apply_page_mutation()`. This function decides whether to publish directly or route through a workflow based on the user's `workflow_id`. Trust-tier promotion (verified) and reference persistence also happen here.

**LLM integration (app/llm/):**
- `client.py` — OpenAI-compatible client setup and generic `_call_llm_json` helper
- `retrieval.py` — Q&A with tool-calling loop (the LLM calls a `retrieve` tool to search pages)
- `pipeline.py` — 3-phase document ingestion orchestrator (extract → dedup → create/merge)
- `ingestion.py` — Individual LLM calls for each pipeline phase

**Permission model:** Hierarchical. Users have `page_permissions` (list of page IDs) granting access to those pages and all descendants. Admins see everything. Resolved in `app/services/permissions.py`.

**Trust tiers:** Pages carry `unverified` → `source_checked` (reserved) → `verified`. Verification is pinned to a content hash; content changes set `trust_is_stale`. The retrieval layer sorts by trust tier then inbound link count.

**Scheduler (app/scheduler/jobs.py):** Three daily APScheduler jobs:
- `check_expired_pages` — creates review requests for pages past `next_approval_date`
- `check_verification_drift` — flags verified pages whose content hash has drifted
- `update_inbound_link_counts` — recomputes graph centrality metric

## Testing

Tests use `pytest-asyncio` with `asyncio_mode = auto`. The `conftest.py` fixture replaces MongoDB with `mongomock-motor` (in-memory), so all tests run without external services. LLM-dependent code paths are not covered by the existing test suite.

## Configuration

Environment variables loaded via pydantic-settings from `.env` (see `app/config.py`). Key vars: `MONGO_URI`, `MONGO_DB`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `SCHEDULER_HOUR`, `SCHEDULER_MINUTE`.
