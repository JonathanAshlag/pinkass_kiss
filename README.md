# pinkass_kiss

A lean, agile knowledge base for storing files and serving the right context to agents.
It is inspired by Google's [Open Knowledge Format (OKF)](https://github.com/GoogleCloudPlatform/open-knowledge-format):
a tree of Markdown files with YAML-style frontmatter, supporting filtering and progressive
disclosure. Storage is Postgres.

## Features

- **Virtual filesystem in Postgres**: folders and files, parent/child hierarchy, tags, status,
  sources, soft delete (no version history, by design).
- **Permissions**: folder kinds (`skeleton`, `manual`, `auto_updated`) and per-node agent locks.
- **Manifests**: curated per-agent lists of files, folders and nested manifests.
- **OKF checks**: advisory broken-link, frontmatter-shape and footnote validation, plus staleness.
- **Ingestion**: mirror a local folder or an upload into the tree. Markdown is stored verbatim; PDF,
  DOCX and others are converted to Markdown, and the original is kept in S3 or a local blob store.
- **Retrieval for agents**: direct corpus interaction (`list_paths`, `search_lines`, `read_lines`)
  and optional semantic search over a derived pgvector index.
- **REST API** (FastAPI) with a small dev UI at `/ui`.
- **QASPER evaluation** of agents answering questions over the KB.

## Architecture

```
ingest/  ──►  storage (Postgres)  ──►  service  ──►  api/ (REST)
                   │                      │
                   └─► semantic_index     └─► retrieval/ (agent tools)
```

| Layer | Location | Role |
|---|---|---|
| Storage (DAL) | `src/kb/storage/` | Folders, files, manifests; policy-unaware primitives |
| OKF documents | `src/kb/okf.py` | Advisory validation and the "virtual file" agents see |
| Service | `src/kb/service.py` | The chokepoint every consumer goes through; enforces `kb.policy` |
| Semantic index | `src/kb/semantic_index/` | Derived, rebuildable pgvector index of file chunks |
| Retrieval | `src/kb/retrieval/` | DCI tools, semantic search, `AgentTools` |
| API | `src/kb/api/` | FastAPI app over the service |

See [CLAUDE.md](CLAUDE.md) for the full design notes, schema and decisions.

## Requirements

- Python 3.11+
- PostgreSQL with the [pgvector](https://github.com/pgvector/pgvector) extension
- [Ollama](https://ollama.com) with `nomic-embed-text` for embeddings (or another LangChain
  embeddings provider via `EMBEDDINGS_MODEL`)

## Setup

```bash
# 1. Install (a conda env named `kb` is what the project uses)
pip install -e ".[test]"

# 2. Configure
cp .env.example .env        # set DATABASE_URL at minimum

# 3. Migrate
set -a && source .env && set +a
alembic upgrade head

# 4. Embeddings (needed for indexing and semantic search)
brew services start ollama && ollama pull nomic-embed-text
```

Set `KB_AUTO_INDEX=0` to run without an embeddings server.

## Usage

Run the API (interactive docs at `/docs`, dev UI at `/ui`):

```bash
uvicorn kb.api:app --app-dir src --reload
```

Ingest a local folder, and (re)build the semantic index:

```bash
python scripts/ingest_folder.py PATH [--parent-id UUID] [--tag T ...] [--dry-run] [--index]
python scripts/reindex.py --all
```

Give an agent the KB tools, scoped to a manifest:

```python
from kb.retrieval.agent_tools import AgentTools

tools = AgentTools(manifest_id).as_langchain(include_semantic=True)
```

## Tests

Database tests need a pgvector Postgres whose database name ends in `_test` (tests truncate its
tables). A ready-made one is in `docker/stack.test.yml`.

```bash
docker compose -f docker/stack.test.yml up -d
export TEST_DATABASE_URL=postgresql+psycopg://kb:kbtest@localhost:5433/kb_test
pytest -m "not llm"
```

Without `TEST_DATABASE_URL`, DB tests are skipped silently. The QASPER LLM evaluation
(`tests/eval/test_qasper_llm.py`) needs an LLM provider; see `.env.example`.

## Project layout

```
src/kb/        the package (storage, okf, service, ingest, semantic_index, retrieval, api, evaluation)
migrations/    Alembic migrations
scripts/       ingest_folder.py, reindex.py, seed_db.py, eval/load_qasper.py
tests/         unit/integration suites; eval/ holds the QASPER suites and fixtures
reference/     background paper on direct corpus interaction
```
