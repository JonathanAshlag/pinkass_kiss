# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pinkas (פנקס כיס) is a self-hosted, offline-capable organizational knowledge wiki with hierarchical pages, approval workflows, trust-tier verification, LLM-powered Q&A, and document ingestion. It runs as a FastAPI backend + Streamlit frontend, backed by MongoDB (always required for GridFS file storage) and optionally PostgreSQL for structured data. Q&A and document processing require a local OpenAI-compatible LLM server (vLLM, Ollama, LM Studio, etc.).

## Prerequisites

- **Python** 3.11+
- **MongoDB** 6.0+ (7.x recommended) — always required for file storage (GridFS)
- **PostgreSQL** 14+ (optional — default backend is MongoDB; see [Storage Backend](#storage-backend) below)
- A local **OpenAI-compatible LLM server** (vLLM, Ollama, LM Studio, etc.) for Q&A and document processing features

## Installation

### Standard install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Offline / air-gapped install

On a machine with internet, pre-download all wheels:

```bash
pip download -r requirements.txt -d ./wheels
```

Transfer the `wheels/` directory to the target machine, then:

```bash
pip install --no-index --find-links=./wheels -r requirements.txt
```

## Configuration

Environment variables are loaded from `.env` (see `app/config.py` for defaults). Key settings:

- `DB_BACKEND`: `mongodb` (default) or `postgres` — controls where pages, users, workflows are stored
- `OPENAI_BASE_URL` / `OPENAI_MODEL` — point to a local LLM server (Ollama, vLLM, LM Studio, etc.)
- `MONGO_URI`, `MONGO_DB` — MongoDB is **always required** for file storage (GridFS), even when using Postgres for structured data

## Storage Backend

MongoDB is **always required** for file uploads (GridFS). The `DB_BACKEND` env var controls where structured data (pages, users, workflows) is stored:

- **MongoDB** (default): all data in MongoDB
- **Postgres**: structured data in Postgres, files still in MongoDB GridFS

See `.claude/rules/postgres-migrations.md` for migration management and reset procedures.

## Commands

```bash
# Install & run
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080    # API (port 8080)
streamlit run streamlit_app/app.py --server.port 8501  # UI (port 8501)

# Tests (no external services needed — uses mongomock + SQLite)
pytest tests/ -v
pytest tests/integration/test_workflows.py::test_no_workflow_auto_publish -v

# Docker
docker-compose up -d
```

## Architecture

**Two-process deployment:** FastAPI API (port 8080) + Streamlit UI (port 8501). The UI calls the API via `PINKAS_API_URL`.

**Central mutation seam:** All page create/edit/delete operations flow through `app/services/mutations.py:apply_page_mutation()`. This function decides whether to publish directly or route through a workflow based on the user's `workflow_id`. Trust-tier promotion (verified) and reference persistence also happen here.

**LLM integration:**
- `app/llm/client.py` — OpenAI-compatible client setup and JSON response parsing
- `app/llm/retrieval.py` — Q&A with tool-calling loop (LLM calls a `retrieve` tool to search pages)
- `app/llm/pipeline.py` — 3-phase document ingestion orchestrator (extract → dedup → create/merge)
- `app/llm/ingestion.py` — Individual LLM calls for each pipeline phase
- `app/routers/produce.py` — HTTP endpoints for document ingestion
- `app/IP/prompts/` — Prompt templates for ingestion and retrieval workflows

**Permission model:** Classification-based. Pages carry a `classification` list of `ClassificationTriangle` objects. Access is resolved via `app/services/permissions.py` using classification matching.

**Trust tiers:** Pages carry `unverified` → `source_checked` (reserved) → `verified`. Verification is pinned to a content hash; content changes set `trust_is_stale`. The retrieval layer sorts by trust tier then inbound link count.

**Scheduler (app/scheduler/jobs.py):** Three daily APScheduler jobs:
- `check_expired_pages` — creates review requests for pages past `next_approval_date`
- `check_verification_drift` — flags verified pages whose content hash has drifted
- `update_inbound_link_counts` — recomputes graph centrality metric

## Testing

Tests use `pytest-asyncio` and parametrize across both backends: MongoDB (via mongomock-motor) and Postgres (via SQLite in-memory). No external services needed. LLM-dependent code paths are not covered.
