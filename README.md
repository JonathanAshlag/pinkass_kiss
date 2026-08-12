# פנקס כיס (Pinkas) — Organizational Knowledge Wiki

A self-hosted, offline-capable organizational knowledge wiki with hierarchical pages, approval workflows, trust-tier verification, LLM-powered Q&A, and document ingestion.

## Prerequisites

- **Python** 3.11+
- **MongoDB** 6.0+ (7.x recommended) — always required for file storage (GridFS)
- **PostgreSQL** 14+ (optional — see [Storage Backend](#storage-backend))
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

Copy `.env.example` to `.env` and adjust:

```bash
cp .env.example .env
```

| Variable | Description | Default |
|----------|-------------|---------|
| `MONGO_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MONGO_DB` | Database name | `pinkas` |
| `DB_BACKEND` | Storage backend: `mongodb` or `postgres` | `mongodb` |
| `POSTGRES_URI` | PostgreSQL connection string (when `DB_BACKEND=postgres`) | `postgresql+asyncpg://localhost/pinkas` |
| `OPENAI_BASE_URL` | LLM API base URL (OpenAI-compatible) | `http://localhost:8000/v1` |
| `OPENAI_API_KEY` | API key for the LLM server | `not-needed` |
| `OPENAI_MODEL` | Model name to use | `local-model` |
| `SCHEDULER_HOUR` | Hour (0-23) for daily expiry check | `3` |
| `SCHEDULER_MINUTE` | Minute (0-59) for daily expiry check | `0` |
| `CLASSIFICATION_API_URL` | External classification API endpoint (optional; feature disabled if empty) | *(empty)* |
| `API_HOST` | FastAPI bind address | `0.0.0.0` |
| `API_PORT` | FastAPI port | `8080` |
| `PINKAS_API_URL` | API URL for the Streamlit GUI | `http://localhost:8080` |
| `ES_AUDIT_ENABLED` | Enable Elasticsearch audit logging | `false` |
| `ES_HOSTS` | Comma-separated Elasticsearch URLs | *(empty)* |
| `ES_API_KEY` | Elasticsearch API key (optional) | *(empty)* |
| `ES_USERNAME` | Elasticsearch basic auth username (optional) | *(empty)* |
| `ES_PASSWORD` | Elasticsearch basic auth password (optional) | *(empty)* |
| `ES_CLOUD_ID` | Elastic Cloud ID (optional) | *(empty)* |

### Pointing to a local LLM server

Set `OPENAI_BASE_URL` to your local server's endpoint:

```bash
# Ollama
OPENAI_BASE_URL=http://localhost:11434/v1

# vLLM
OPENAI_BASE_URL=http://localhost:8000/v1

# LM Studio
OPENAI_BASE_URL=http://localhost:1234/v1
```

Set `OPENAI_MODEL` to the model name your server expects.

## Storage Backend

Pinkas supports two storage backends selected at startup via `DB_BACKEND`. MongoDB is always required for file uploads (GridFS); only page/user/workflow/request data moves to Postgres.

### MongoDB (default)

```bash
DB_BACKEND=mongodb  # or omit — mongodb is the default
```

Initialize indexes and seed demo data:

```bash
python scripts/init_db.py   # creates indexes
python scripts/seed_db.py   # creates sample users, workflow, and page hierarchy
```

> ⚠️ **`scripts/seed_db.py` is destructive.** It unconditionally deletes *every* existing
> user, workflow, page, and request before inserting the demo data — not just data it
> seeded on a previous run. Only run it against a database you're fine wiping. For a real
> deployment, use `scripts/create_admin.py` instead (see [First Experiment
> Checklist](FIRST_EXPERIMENT_CHECKLIST.md)) — it provisions one admin user and touches
> nothing else.

### PostgreSQL

MongoDB must still be running alongside Postgres — file blobs (PDFs, images, etc.) are always stored in GridFS, which requires MongoDB. Only structured data (pages, users, workflows, requests) moves to Postgres.

```bash
DB_BACKEND=postgres
POSTGRES_URI=postgresql+asyncpg://user:password@localhost/pinkas
# MongoDB is still required — keep MONGO_URI and MONGO_DB set
MONGO_URI=mongodb://localhost:27017
MONGO_DB=pinkas
```

Run the Alembic migration to create Postgres tables:

```bash
alembic upgrade head
```

Create MongoDB indexes (GridFS needs this even in Postgres mode):

```bash
python scripts/init_db.py
```

Seed demo data (uses the same script — reads `DB_BACKEND` from `.env`; ⚠️ destructive,
see warning above):

```bash
python scripts/seed_db.py
```

The PostgreSQL schema normalizes page history into a `page_revisions` table and page references into a `page_refs` table (for efficient backlink queries). Classification and metadata are stored as JSONB. Full-text search uses a generated `tsvector` column with a GIN index.

### Resetting after a squashed/rewritten migration

While the schema is still in flux (pre-production, no real data to preserve), migration files under `app/infrastructure/postgres/migrations/versions/` are sometimes squashed or rewritten instead of layered with new revisions. If your local Postgres DB was already migrated to a revision that no longer exists on disk, Alembic will fail with something like:

```
alembic.util.exc.CommandError: Can't locate revision identified by '004'
```

This happens because the DB's `alembic_version` table still points at a revision ID that was deleted. Since there's no data worth keeping yet, the fix is to drop the schema (which also wipes the stale `alembic_version` row) and re-migrate from scratch:

```bash
python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import settings

async def main():
    engine = create_async_engine(settings.postgres_uri)
    async with engine.begin() as conn:
        await conn.exec_driver_sql('DROP SCHEMA IF EXISTS pinkass CASCADE')
    await engine.dispose()

asyncio.run(main())
"
python scripts/init_db.py
```

⚠️ **Destructive** — `DROP SCHEMA ... CASCADE` deletes all Postgres tables and data. Only do this when the Postgres data is disposable. Once the schema is stable in production, migrations should be added as new revisions instead of rewritten in place.

## Running

### FastAPI server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --env-file .env
```

API docs available at: `http://localhost:8080/docs`

### Streamlit GUI

```bash
streamlit run streamlit_app/app.py --server.port 8501
```

GUI available at: `http://localhost:8501`

### Docker Compose

```bash
docker-compose up -d
```

## systemd Services

### `/etc/systemd/system/pinkas-api.service`

```ini
[Unit]
Description=Pinkas API Server
After=network.target mongod.service

[Service]
Type=simple
User=pinkas
Group=pinkas
WorkingDirectory=/opt/pinkas
EnvironmentFile=/opt/pinkas/.env
ExecStart=/opt/pinkas/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/pinkas-ui.service`

```ini
[Unit]
Description=Pinkas Streamlit UI
After=network.target pinkas-api.service

[Service]
Type=simple
User=pinkas
Group=pinkas
WorkingDirectory=/opt/pinkas
EnvironmentFile=/opt/pinkas/.env
ExecStart=/opt/pinkas/.venv/bin/streamlit run streamlit_app/app.py --server.port 8501 --server.address 0.0.0.0
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable pinkas-api pinkas-ui
sudo systemctl start pinkas-api pinkas-ui
```

## Quick-Start Walkthrough

1. **Seed the database** (⚠️ destructive — wipes existing users/workflows/pages/requests
   first; see [Storage Backend](#storage-backend)):
   ```bash
   python scripts/seed_db.py
   ```

2. **Start the API and UI** (in separate terminals or via systemd)

3. **Login** to the Streamlit UI as `admin1`

4. **Create a page:** Navigate to "יצירת דף", enter a title, a short description, and content. Optionally set a future `next_approval_date`. As admin with no workflow, it publishes immediately.

5. **Test approval flow:** Login as `editor1` (has a workflow). Create a page — it will be pending approval. Login as `admin1`, go to "אישורים", approve the request.

6. **Ask a question:** Go to "שאל שאלה", type a question about the wiki content. The LLM searches and composes an answer, preferring verified pages and noting when it relies on unverified content. The response includes a `cited_pages` list of page **titles** used as context. Only pages the requesting user has access to (per classification triangles) are included as context.

7. **Produce from a file:** Go to "העלאת מסמך", upload a PDF, DOCX, XLSX, PPTX, HTML, or TXT file. The system extracts text and any embedded images (up to 20 per document) and generates wiki pages using multimodal LLM calls. Admin users can pass `initial_trust_tier=verified` via the API to self-certify the upload. The raw file is kept in GridFS and reference-counted against the pages it produced — it is purged automatically once the last generated page is deleted.

8. **Verify a page:** Submit a `review` approval request with `proposed_content.type = "review"` and `trust_tier = "verified"`. Once the workflow approves it, the page is stamped as human-verified, a SHA-256 content hash is recorded, and `verified_at`/`verified_by` are set. Any subsequent content change triggers an automatic re-verification request the next morning.

## Approval Request Payloads

Every `POST /approvals` request carries a `proposed_content` object discriminated by `type`:

| `type` | Used for | Key fields |
|--------|----------|------------|
| `create` | New page pending approval | `title`, `description`, `content` |
| `edit` | Content or metadata change | `title`, `description`, `content`, `parent_id`, `references`, `next_approval_date` |
| `delete` | Soft-delete pending approval | *(no extra fields)* |
| `review` | Periodic review or trust-tier promotion | `title`, `content`, `trust_tier` |

All mutations (create/edit/delete) go through `apply_page_mutation()` in `app/services/mutations.py`. Users with a `workflow_id` are automatically routed to the approval queue; users without one have changes applied immediately.

`apply_page_mutation` returns a typed `MutationResult` — one of three shapes, reflected in the OpenAPI schema:

| Return type | `status` | Extra fields |
|-------------|----------|--------------|
| `PublishedResult` | `"published"` | `page` — the full page object |
| `PendingResult` | `"pending_approval"` | `request_id`; `page` present for create, absent for edit/delete |
| `DeletedResult` | `"deleted"` | *(none)* |

`apply_page_mutation` also accepts an optional `trust_tier` parameter. When `trust_tier=verified` and a create or edit mutation is published directly (no workflow), the seam promotes the page's trust tier inline via `build_verification_fields()` — recording the content hash, `verified_at`, and `verified_by` — so callers never need to reach around the seam with raw DB writes. Today only the document-ingestion pipeline (`app/llm/pipeline.py`, admin self-certified uploads) passes `trust_tier` on create; no API surface exposes it on edit, since every other path to `verified` is required to go through a `review` approval request (see below).

When an edit mutation is routed through a workflow, `apply_page_mutation` immediately persists any `references` on the `PageUpdate` regardless of approval state. Source provenance is always recorded; only content changes wait for approval.

## Trust Tier System

Every page carries a `trust_tier` field that signals how thoroughly its content has been vetted:

| Tier | Set by | Meaning |
|---|---|---|
| `unverified` | Default | Agent-drafted or freshly created; no human review |
| `source_checked` | Reserved | Future: automated claim-vs-citation check |
| `verified` | Human (via approval) or admin (at upload) | A person vouched for the accuracy of the prose |

**Drift detection:** verification is pinned to a content hash (`verified_content_hash`). If a page's content changes after verification, `GET /pages/{id}` returns `trust_is_stale: true` and the nightly scheduler raises an automatic re-verification request — the tier is never silently demoted.

**Agent retrieval:** the LLM retrieval layer sorts results by trust tier (then by inbound link count), and the system prompt instructs the model to prefer verified sources and to flag when it cites unverified content.

**Graph centrality:** `inbound_link_count` is updated nightly. Hub pages (many inbound references) propagate errors widely, so they are surfaced first for human verification.

**Scheduler jobs (run daily):**
- `check_expired_pages` — review requests for pages past their `next_approval_date`
- `check_verification_drift` — re-verification requests for verified pages whose content has changed
- `update_inbound_link_counts` — refreshes inbound link counts across all published pages

## Tags

Pages carry a `tags` field drawn from a **closed, curated vocabulary** — unlike `aliases` (free text), a tag must come from a fixed list. `PageCreate`/`PageUpdate` reject any tag not in the vocabulary with HTTP 422.

**`app/IP/`** holds proprietary content that shouldn't live as hardcoded literals in the reviewed application code: the real tag taxonomy (`app/IP/tags.py::ALLOWED_TAGS`) and the LLM system prompts (`app/IP/prompts/`). The directory ships with placeholder/demo content and is committed normally (no `.gitignore`) — replace `app/IP/tags.py` with the real organizational taxonomy when ready.

**Inheritance:** a child page automatically inherits its parent's tags (unioned with its own) whenever it's created, edited, or re-parented. This is **forward-only** — editing a parent's tags later does not retroactively update pages that are already its descendants; inheritance is only applied at the moment a page itself is written.

**Search:** `GET /pages/search` accepts a repeated `tags` query param and matches a page if it carries **any** of the requested tags (OR semantics), combined with the existing fuzzy title/alias search. `GET /pages/tags` returns the full `ALLOWED_TAGS` vocabulary and backs the tag pickers in the Streamlit UI.

**Browse UI:** the main "עיון" page is a two-layer, folder-style navigator over tags rather than a flat page tree:
- **Layer 1** lists one folder per tag (with a live page count) plus a "ללא תגית" (untagged) folder.
- **Layer 2**, after entering a tag, shows only the hierarchy chain leading to pages that carry it — unrelated branches with no tagged descendant never appear. Clicking a page toggles its children open/closed inline and loads its full detail in the side panel; a back button returns to layer 1.

## Classification Triangle Access Control

Pages can carry a `classification` field — a list of `ClassificationTriangle` objects, each with an `id` and a `level` (1–4). A user may only read or edit a classified page if their own triangles (fetched from an external API) satisfy **every** triangle on the page at an equal or higher level.

**Behaviour:**
- If `CLASSIFICATION_API_URL` is not configured the feature is disabled and all pages are accessible.
- If the classification API call fails the system **fails closed**: the user is treated as having no triangles and cannot access any classified page.
- Unclassified pages (`classification: []`) are always accessible to users with the normal page-level permissions.
- **Q&A is classification-aware:** the retrieval layer fetches the user's triangles once at the start of each query and filters search results before passing them to the LLM, so classified pages the user cannot read will never appear in cited context.

**Setting classifications on a page:**

Pass `classification` in the `PageCreate` or `PageUpdate` body:

```json
{
  "classification": [
    {"id": "alpha", "level": 2},
    {"id": "beta",  "level": 3}
  ]
}
```

The classification API must expose `GET {CLASSIFICATION_API_URL}/users/{user_id}/classifications` and return:

```json
{"triangles": [{"id": "alpha", "level": 3}, {"id": "beta", "level": 4}]}
```

## Audit Logging

Pinkas can emit audit logs to Elasticsearch for every user action: page creation, editing, deletion, and Q&A queries. Logs are written asynchronously (fire-and-forget) so they never slow down API responses.

### Setup

1. Set `ES_AUDIT_ENABLED=true` and `ES_HOSTS` in `.env`:

```bash
ES_AUDIT_ENABLED=true
ES_HOSTS=http://localhost:9200
```

2. Optionally configure authentication:

```bash
# API key auth
ES_API_KEY=your-api-key

# OR basic auth
ES_USERNAME=elastic
ES_PASSWORD=changeme

# OR Elastic Cloud
ES_CLOUD_ID=my-deployment:dXMtY2VudHJhbC...
```

### Index pattern

Audit documents are indexed to `pinkas-events-YYYY.MM` (monthly rotation) — the same
index also receives agent-retrieval log entries (see [Agent API](#agent-api)); an
`event_kind` field (`audit` / `retrieval`) distinguishes the two. Pinkas doesn't create
an index template itself; apply `es_index_template.json` (repo root) once via `PUT
_index_template/pinkas-events` before traffic starts flowing — dynamic mapping works
without it, but the app runs exact-match `term` filters on fields like `event_kind` and
`mode` that need `keyword` typing to stay reliable across every monthly rotation.

Audit documents (`event_kind: audit`) contain:

| Field | Description |
|-------|-------------|
| `event_kind` | Always `audit` for these documents |
| `timestamp` | UTC ISO-8601 timestamp |
| `action` | `ask`, `create_page`, `edit_page`, `delete_page` |
| `user_context.user_id` | The acting user |
| `user_context.client_application_id` | Optional — external app making the call |
| `user_context.client_session_id` | Optional — session on the external app |
| `resource_id` | Page ID (null for queries) |
| `outcome` | `success`, `pending_approval`, `denied`, `not_found`, `error` |
| `result` | The full operation response (page data, answer + cited pages, etc.) |
| `latency_ms` | Server-side processing time in milliseconds |
| `request_path` | HTTP endpoint path |

Both failed attempts (permission denied, page not found) and successful operations are logged.

### Client headers

External applications can identify themselves by sending optional headers:

- `X-Client-Application-Id` — identifies the calling application
- `X-Client-Session-Id` — identifies the user's session on the calling application

These are included in the `user_context` of every audit log entry.

## Agent API

Besides the human-facing page/UI routes, Pinkas exposes a separate **agent-facing consumption API** (`app/routers/agent_api.py`, prefix `/agent`) for AI agent clients to search and fetch wiki content over HTTP — distinct from the Q&A endpoint, which is meant for the built-in LLM retrieval loop.

### Provisioning an agent

An admin creates an agent identity via `POST /agents` (`x-user-id` header, admin permission required):

```bash
curl -X POST http://localhost:8080/agents -H "x-user-id: admin1" \
  -H "Content-Type: application/json" -d '{"name": "my-agent"}'
```

The response includes an `api_key` — it is only ever returned once (only its hash is stored), so save it immediately. If `user_id` is omitted, a new read-only user is created and linked to the agent; pass an existing `user_id` to link to one instead. Other admin routes under `/agents` list/update agents and rotate keys (`POST /agents/{agent_id}/rotate-key`, which invalidates the old key).

### Routes

Every route below requires header `X-API-Key: <api_key>`; all except `/agent/tools` also require `X-Session-Id: <any-client-chosen-session-id>` (used to correlate an agent's calls in retrieval logs).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/agent/tools` | Returns OpenAI-style function-calling schemas for `search`/`fetch`, for tool discovery |
| `POST` | `/agent/search` | Fuzzy search over pages by `query`; short tier (title + description) |
| `POST` | `/agent/fetch` | Fetch full content (long tier) for explicit `page_ids` |
| `POST` | `/agent/scan` | Passively scan arbitrary `text` for page title/alias matches |
| `GET` | `/agent/bundles/{name}` | Fetch a curated bundle, rendered against current page content |

`/agent/fetch` returns requested ids under either `results` or `unavailable`; each `unavailable` entry carries a `reason` of `not_found` (no such page, or it's deleted) or `forbidden` (the page exists but the caller's classification doesn't grant access).

Bundles are authored by an admin via `PUT /bundles/{name}` (see `app/routers/bundles.py`) before an agent can fetch them.

Every call to `/agent/search`, `/agent/fetch`, `/agent/scan`, and `/agent/bundles/{name}` emits a `RetrievalLogEntry`. Admins can review recent search misses via `GET /agent/logs/misses?since_hours=24&limit=50` (`x-user-id` header, admin permission required).

### Example client

`scripts/agent_client_example.py` is a runnable, minimal HTTP client demonstrating all four data routes (everything except `/agent/tools`), with example queries matched against `scripts/seed_db.py`'s sample data:

```bash
python scripts/seed_db.py   # ⚠️ destructive — seed sample pages to search/fetch/scan against
BASE_URL=http://localhost:8080 API_KEY=... SESSION_ID=demo-session \
  BUNDLE_NAME=pets python scripts/agent_client_example.py
```

## Running Tests

```bash
pytest tests/ -v
```

Each test runs twice — once against MongoDB (`mongomock-motor`, no real MongoDB needed) and once against PostgreSQL (`aiosqlite` in-memory, no real Postgres needed). Scheduler tests run MongoDB-only since they test the full background-job path.

## Architecture

```
pinkas/
├── app/
│   ├── main.py                  # App entry point + lifespan (initialises connections)
│   ├── config.py                # Settings from environment (pydantic-settings)
│   ├── container.py             # Composition root — selects MongoDB or Postgres implementation
│   │                            # at startup; exports Annotated repo aliases for routers
│   ├── routers/                 # REST endpoints
│   │   ├── pages.py
│   │   ├── ask.py               # Q&A
│   │   ├── produce.py           # Document ingestion (PDF/DOCX/XLSX/PPTX/HTML/TXT)
│   │   ├── workflows.py
│   │   ├── users.py
│   │   ├── approvals.py
│   │   └── deps.py              # Auth dependencies
│   ├── services/                # Business logic (backend-agnostic)
│   │   ├── mutations.py         # Central mutation seam — routes creates/edits/deletes
│   │   │                        # through workflows; returns typed MutationResult
│   │   │                        # (PublishedResult | PendingResult | DeletedResult)
│   │   ├── pages.py
│   │   ├── classification.py    # Triangle access control + external API client
│   │   ├── workflows.py
│   │   ├── users.py
│   │   ├── requests.py          # Approval request lifecycle + decision handling
│   │   ├── source_files.py      # Source file lifecycle — reference-counted GridFS cleanup
│   │   └── permissions.py
│   ├── models/                  # Pydantic v2 domain models
│   ├── IP/                      # Proprietary/curated content, kept separate from reviewed app code
│   │   ├── tags.py              # ALLOWED_TAGS — closed tag vocabulary (placeholder/demo data)
│   │   └── prompts/             # LLM system prompts (retrieval.py, ingestion.py)
│   ├── storage/                 # Data access layer
│   │   ├── base.py              # Abstract base classes (PageRepository, UserRepository, …)
│   │   ├── mongo/               # Motor implementations
│   │   └── postgres/            # SQLAlchemy 2.0 async implementations
│   ├── infrastructure/          # Connection management and schema
│   │   ├── mongo.py             # Motor client singleton + GridFS
│   │   ├── elasticsearch.py     # Async Elasticsearch client for audit logging
│   │   └── postgres/
│   │       ├── engine.py        # SQLAlchemy engine + session factory
│   │       ├── models.py        # ORM table definitions
│   │       └── migrations/      # Alembic migrations
│   ├── llm/                     # LLM integration
│   │   ├── client.py            # OpenAI-compatible client (persistent singleton, 600 s timeout)
│   │   ├── retrieval.py         # Q&A with tool-calling loop; classification-aware search;
│   │   │                        # cited_pages returns page titles
│   │   ├── ingestion.py         # Per-phase LLM calls (extract → dedup → generate/merge);
│   │   │                        # prompt structure optimised for vLLM prefix-cache reuse
│   │   ├── extraction.py        # Document text/image extraction (PDF/DOCX/XLSX/PPTX/HTML)
│   │   └── pipeline.py          # 3-phase ingestion orchestrator; returns list[PageIngestOutcome]
│   └── scheduler/               # APScheduler daily jobs
│       └── jobs.py              # check_expired_pages, check_verification_drift,
│                                # update_inbound_link_counts
├── streamlit_app/               # Streamlit GUI (Hebrew RTL)
│   ├── app.py
│   ├── state.py                 # Session-state key constants + navigate_to()
│   ├── strings.py               # Hebrew UI strings
│   ├── helpers.py               # API client + RTL helpers
│   └── views/                   # browse.py, search.py, create_edit.py, ask_page.py, …
├── scripts/                     # Standalone operational scripts (see below)
│   ├── init_db.py                # Create MongoDB indexes / run Postgres migrations
│   ├── seed_db.py                # ⚠️ destructive — wipes existing data, seeds demo users/workflow/pages
│   ├── create_admin.py           # Create a single real admin user (no demo data)
│   ├── bulk_upload_terms.py      # Bulk-import a JSON list of terms as pages
│   └── agent_client_example.py   # Minimal example client for the agent-api
├── tests/                       # pytest suite (each test runs against both backends)
├── alembic.ini                  # Points to app/infrastructure/postgres/migrations/
├── es_index_template.json       # Optional index template for pinkas-events-* (see Audit Logging)
├── requirements.txt
├── .env.example
├── docker-compose.yml
└── README.md
```

### Storage layer design

`storage/base.py` defines abstract base classes (`PageRepository`, `UserRepository`, etc.) that all implementations must satisfy. `container.py` is the only file that reads `DB_BACKEND` — it selects the concrete implementation at startup and exposes it to routers via FastAPI's `Depends()` mechanism. All service and router code is backend-agnostic.

MongoDB always runs alongside Postgres (GridFS for file blobs). Schema design rationale:
- **Normalized:** page history (`page_revisions`) and page references (`page_refs`) — queried, filtered, or joined in SQL
- **JSONB:** classification, workflow steps, request history — only ever read back whole, never filtered in SQL
- **JSONB + GIN index:** `tags` is the one JSONB column filtered directly in SQL — pages carry a small closed-vocabulary tag list, and `ix_pages_tags_gin` supports the `?|` "any of these tags" query efficiently

## License

Internal use.
