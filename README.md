# פנקס כיס (Pinkas) — Organizational Knowledge Wiki

A self-hosted, offline-capable organizational knowledge wiki with hierarchical pages, approval workflows, trust-tier verification, LLM-powered Q&A, and document ingestion.

## Prerequisites

- **Python** 3.11+
- **MongoDB** 6.0+ (7.x recommended)
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
| `OPENAI_BASE_URL` | LLM API base URL (OpenAI-compatible) | `http://localhost:8000/v1` |
| `OPENAI_API_KEY` | API key for the LLM server | `not-needed` |
| `OPENAI_MODEL` | Model name to use | `local-model` |
| `SCHEDULER_HOUR` | Hour (0-23) for daily expiry check | `3` |
| `SCHEDULER_MINUTE` | Minute (0-59) for daily expiry check | `0` |
| `CLASSIFICATION_API_URL` | External classification API endpoint (optional; feature disabled if empty) | *(empty)* |
| `API_HOST` | FastAPI bind address | `0.0.0.0` |
| `API_PORT` | FastAPI port | `8080` |
| `PINKAS_API_URL` | API URL for the Streamlit GUI | `http://localhost:8080` |

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

## Database Setup

### Create indexes

```bash
python init_db.py
```

This creates:
- Text index on `pages.title` + `pages.content` (full-text search)
- Index on `pages.parent_id` (tree queries)
- Index on `pages.next_approval_date` (expiry job)
- Index on `pages.status`
- Index on `pages.trust_tier` (verification queries)
- Compound index on `pages.(trust_tier, inbound_link_count)` (ranked retrieval)
- Unique indexes on `page_id`, `user_id`, `workflow_id`, `request_id`
- Index on `requests.status` and `requests.requested_by`

### Seed demo data

```bash
python seed_db.py
```

Creates sample users, a workflow, and a page hierarchy (Animals → Dog/Cat/Sheep).

## Running

### FastAPI server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
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

1. **Seed the database:**
   ```bash
   python seed_db.py
   ```

2. **Start the API and UI** (in separate terminals or via systemd)

3. **Login** to the Streamlit UI as `admin1`

4. **Create a page:** Navigate to "יצירת דף", enter a title, a short description, and content. Optionally set a future `next_approval_date`. As admin with no workflow, it publishes immediately.

5. **Test approval flow:** Login as `editor1` (has a workflow). Create a page — it will be pending approval. Login as `admin1`, go to "אישורים", approve the request.

6. **Ask a question:** Go to "שאל שאלה", type a question about the wiki content. The LLM searches and composes an answer, preferring verified pages and noting when it relies on unverified content.

7. **Produce from a file:** Go to "העלאת מסמך", upload a PDF, DOCX, XLSX, PPTX, HTML, or TXT file. The system extracts text and any embedded images (up to 20 per document) and generates wiki pages using multimodal LLM calls. Admin users can pass `initial_trust_tier=verified` via the API to self-certify the upload.

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

`apply_page_mutation` accepts an optional `trust_tier` parameter. When `trust_tier=verified` and the create mutation is published directly (no workflow), the seam promotes the page's trust tier inline — recording the content hash, `verified_at`, and `verified_by` — so the pipeline never needs to reach around the seam with raw DB writes.

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

## Classification Triangle Access Control

Pages can carry a `classification` field — a list of `ClassificationTriangle` objects, each with an `id` and a `level` (1–4). A user may only read or edit a classified page if their own triangles (fetched from an external API) satisfy **every** triangle on the page at an equal or higher level.

**Behaviour:**
- If `CLASSIFICATION_API_URL` is not configured the feature is disabled and all pages are accessible.
- If the classification API call fails the system **fails closed**: the user is treated as having no triangles and cannot access any classified page.
- Unclassified pages (`classification: []`) are always accessible to users with the normal page-level permissions.

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

## Running Tests

```bash
pytest tests/ -v
```

Tests use `mongomock-motor` — no running MongoDB instance required.

## Architecture

```
pinkas/
├── app/                    # FastAPI backend
│   ├── main.py            # App entry point + lifespan
│   ├── config.py          # Settings from environment
│   ├── routers/           # REST endpoints
│   │   ├── pages.py       # Page CRUD
│   │   ├── ask.py         # Q&A endpoint
│   │   ├── produce.py     # Document ingestion (PDF/DOCX/XLSX/PPTX/HTML/TXT; multimodal extraction)
│   │   ├── workflows.py   # Workflow management
│   │   ├── users.py       # User management
│   │   ├── approvals.py   # Approval decisions
│   │   └── deps.py        # Auth dependencies
│   ├── services/          # Business logic
│   │   ├── mutations.py      # Workflow-routing dispatch (apply_page_mutation); absorbs trust-tier promotion and reference persistence
│   │   ├── pages.py          # Page CRUD; find_pages → list[Page], find_page_docs(projection) → list[dict], set_page_references
│   │   ├── classification.py # Classification triangle access control (external API + user_satisfies_classification)
│   │   ├── workflows.py
│   │   ├── users.py
│   │   ├── requests.py       # Approval request lifecycle + decision handling
│   │   └── permissions.py
│   ├── models/            # Pydantic v2 models
│   ├── db/                # MongoDB + GridFS
│   ├── llm/               # LLM integration
│   │   ├── client.py      # Generic plumbing (_get_client, _call_llm_json)
│   │   ├── retrieval.py   # Q&A with tool-calling retrieval
│   │   ├── ingestion.py   # Per-phase LLM calls (extract, dedup, generate, merge); multimodal image parts forwarded where supported
│   │   └── pipeline.py    # 3-phase ingestion orchestration
│   └── scheduler/         # APScheduler daily jobs
├── streamlit_app/         # Streamlit GUI (Hebrew RTL)
│   ├── app.py             # Main app
│   ├── strings.py         # Hebrew UI strings
│   ├── helpers.py         # API client + RTL helper
│   └── pages/             # GUI pages
├── tests/                 # pytest suite
├── init_db.py             # Create MongoDB indexes
├── seed_db.py             # Seed demo data
├── requirements.txt
├── .env.example
├── docker-compose.yml
└── README.md
```

## License

Internal use.
