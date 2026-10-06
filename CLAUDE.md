# pinkass_kiss — lean knowledge base (OKF-inspired)

A lean, agile knowledge base for storing files and serving the right context to agents.
Design is inspired by Google's Open Knowledge Format (OKF): a directory structure of
Markdown files, each with YAML frontmatter, supporting filtering and progressive
disclosure. Storage backend is Postgres.

The real OKF v0.2 spec (`GoogleCloudPlatform/open-knowledge-format/SPEC.md`) was read
and compared against this project's schema when building layer 2 — see "OKF spec vs.
this project" below for what was deliberately adopted, adapted, or skipped.

## Architecture: three layers

1. **Postgres DAL / storage layer** — **built.**
   Owns the virtual filesystem: nodes/files, parent-child hierarchy, generic metadata,
   timestamps, soft delete, and "manifests" (curated per-agent file lists). Exposes
   clean primitives (`get_node`, `list_children`, `create_file`, `move_node`,
   `delete_node`, `get_content`, `query_metadata`, plus manifest operations). Doesn't
   interpret frontmatter *values* (tags/status/etc. stay opaque) and doesn't import
   `kb.okf` — the two layers are fully independent.

2. **OKF document layer** — **built** (minus the explicitly-out-of-scope Attested
   Computation subspec). `src/kb/okf.py`: advisory broken-link detection
   (`find_broken_links`/`extract_links`), advisory frontmatter-shape validation
   (`validate_frontmatter`), per-claim footnote attribution
   (`extract_footnote_refs`/`find_unresolved_footnotes`), and a staleness helper
   (`is_stale`).

3. **KB service / CRUD API** — **built.** `src/kb/service.py`: a thin passthrough
   chokepoint over the DAL (plus one real piece of logic, `get_warnings`) that every
   consumer — REST API, agents, ingestion jobs — goes through instead of calling
   `kb.storage.dal` directly. `src/kb/api/app.py`: a FastAPI REST surface over it. Still
   explicitly deferred: authz, manifest boundaries beyond storage, concurrency checks,
   real validation hooks (constraint violations currently surface as a raw 500).

4. **Semantic index** — **built.** `src/kb/semantic_index/`: a *derived* pgvector index
   (`kb_chunks`) beside the canonical `files` table, kept in step by LangChain's indexing
   API. Disposable — always rebuildable from `files`. See "Semantic index" below.

## Stack

Python 3.11+ (dev env: 3.13), SQLAlchemy 2.0 (declarative style, `Mapped`/`mapped_column`), Alembic for
migrations, psycopg3 as the driver. Src-layout package `kb` under `src/kb/`.

Layout: data comes **in** (`ingest/`), lives in **storage** with a **derived index** beside it,
and goes **out** through `service` → `api/` / `retrieval/`.

```
src/kb/
  service.py          the chokepoint every consumer goes through; imports storage.dal, okf,
                      retrieval.dci (semantic_index/retrieval.semantic/storage.blobs lazily)
  okf.py              layer 2: advisory OKF checks; imports only storage.models, never dal
  storage/            layer 1: canonical data
    db.py             Base, engine, SessionLocal (reads DATABASE_URL from env)
    models.py         SQLAlchemy models: File, Manifest, ManifestMember
    dal.py            the DAL primitives (see below); does not import kb.okf
    blobs.py          raw-original storage (S3 / local ByteStore), see "Folder ingestion"
  ingest/             data coming in; imports kb.service only (+ storage.blobs)
    folder.py         plan_folder / ingest_folder
    upload.py         ingest_upload (multipart uploads -> temp dir -> ingest_folder)
    plan.py           PlannedNode + materialize (the one place a plan becomes nodes)
    processors/
      base.py         Processor protocol, ProcessedDocument, registry
      markdown.py     MarkdownProcessor
      converters.py   LoaderProcessor: PDF/DOCX/... -> markdown via LangChain loaders
  semantic_index/     layer 4, build side: derived pgvector index; never imports kb.service
    vectorstore.py    LangChain wiring: PGVectorStore, record manager, embeddings
    chunking.py       files -> chunks (FileNodeLoader, splitter, line/heading annotator)
    indexer.py        index_files / unindex_files / reindex_all
  retrieval/          data going out to agents
    dci.py            direct corpus interaction: list_paths / search_lines / read_lines,
                      render_virtual_file (see reference/paper.md)
    semantic.py       semantic_search over semantic_index, scoped by manifest
    agent_tools.py    AgentTools(manifest_id): both of the above as agent tools
  api/                REST surface (`uvicorn kb.api:app`); imports kb.service
    app.py            the FastAPI app (see below)
    schemas.py        Pydantic v2 request/response models
    static/           dev UI mounted at /ui
scripts/              ingest_folder.py, reindex.py, seed_db.py; eval/load_qasper.py
tests/                unit/integration suites; eval/ = QASPER suites + fixtures
```

- `migrations/` — Alembic: `0001_create_schema.py` (compacted from what was originally
  six incremental migrations, while there was still no real data to preserve) and
  `0002_semantic_index.py` (`vector` extension, `kb_chunks`, `upsertion_record`).
  **The DB needs the pgvector extension** — installed on the local Homebrew Postgres 17
  (pgvector 0.8.7); the test stack uses the `pgvector/pgvector:pg16` image.
- `.env` (gitignored) — `DATABASE_URL`, plus `BLOB_BUCKET`/`BLOB_PREFIX`/`AWS_*` for the
  real S3 bucket (verified round-trip); `.env.example` has the template

**Local dev environment:** conda env `kb` (Python 3.13), with the project installed via
`pip install -e ".[test]"` (pyproject.toml is the only dependency list; there is no
requirements.txt). Run tools with `conda run -n kb ...` or after `conda activate kb`.
Embeddings come from Ollama (`brew services start ollama`, model `nomic-embed-text`,
768d), which the semantic index needs for indexing and search.

To run migrations locally: `set -a && source .env && set +a && alembic upgrade head`
(a local Postgres with a `pinkas` database, trust auth, is what's been used for
verification so far — adjust `.env` if that changes). It is at `0002` (head) and its
content has been indexed (`scripts/reindex.py --all`).

To run the API locally: `set -a && source .env && set +a && uvicorn kb.api:app
--app-dir src --reload` — then see `/docs` for the interactive OpenAPI UI.

## Schema as it stands (migration 0001)

### `files` — one row per node (file OR folder), self-referencing tree

| Column | Notes |
|---|---|
| `id` | uuid PK, doubles as the OKF frontmatter `id` — no separate internal PK |
| `parent_id` | self-FK, `ON DELETE SET NULL`; adjacency-list tree (no separate `name`/slug — `title` is the only label, no sibling-uniqueness constraint) |
| `kind` | enum `file` \| `folder`. `folder` nodes may have `content = NULL` (pure container) or filled in (e.g. an explainer for what's inside) |
| `title`, `aliases[]`, `description`, `tags[]` (GIN indexed) | frontmatter fields, plain columns |
| `sources`, `verified` | JSONB arrays, unnormalized, GIN indexed. `sources[].resource` can point externally (`okf://...`) or internally (`db://files/<id>`) — internal refs are **not** enforced as real FKs, just opaque text (deliberate — matches the project's lean bias) |
| `generated` | nullable JSONB **object** (not array) — OKF's `{by, at}` provenance field, added alongside `verified`. `NULL` = no provenance recorded |
| `stale_after` | nullable `timestamptz` — an absolute instant, not a relative TTL, matching OKF's semantics exactly; `kb.okf.is_stale(node)` compares it against now |
| `status` (enum `draft`\|`stable`\|`deprecated`) | lifecycle field |
| `content` | markdown body; NOT NULL unless `kind='folder'` (`content_required_for_file` CHECK) |
| `deleted_at` | soft delete, NULL = active |
| `blob_key`, `blob_size_bytes`, `blob_mime_type`, `blob_checksum` | the retained original of a converted file (PDF, ...): content-addressed key `sha256/<hex>` in the `kb.storage.blobs` store, checksum `sha256:<hex>`. NULL for markdown nodes or when no blob store is configured |
| `created_at`, `updated_at` | `updated_at` kept current by a DB trigger (`trg_files_set_updated_at` → `set_updated_at()`), not the ORM |

### `manifests` / `manifest_members` — curated per-agent file lists

Named "manifest," not "bundle" — OKF itself uses "bundle" for the whole knowledge
directory (root-relative links are called "bundle-relative" in the spec), which would
collide with this project's unrelated concept below. (This was originally built as
"bundles" and renamed via a migration; since migrations were later compacted into one
file while there was still no real data, the single migration just creates it as
"manifest" from the start — no rename step, no leftover naming artifacts.)

A **manifest** is a named, developer-curated list of files/directories/other
manifests, meant to preclaim what parts of the wiki are relevant to a given agent
(resolved later, when data is actually exposed to that agent — that exposure logic
doesn't exist yet).

- `manifests`: `id`, `name` (globally unique), `description`, `deleted_at`, timestamps
  (same trigger pattern as `files`)
- `manifest_members`: polymorphic membership row — exactly one of `file_id` (a file
  *or* directory node) or `child_manifest_id` (nested manifest) is set (CHECK
  constraint), plus a CHECK blocking direct self-reference, and partial unique indexes
  preventing duplicate membership rows
- **Directory membership is dynamic**: including a folder means its whole subtree,
  resolved at read time — a file added under that folder *after* the manifest was
  created is automatically included, nothing needs re-syncing.
- **Cycle prevention for nested manifests** is NOT a DB constraint (can't express
  multi-hop cycles as a CHECK) — it's enforced in `dal.add_manifest_member`, which
  walks the transitive closure before inserting. Deliberate choice to avoid a DB
  trigger, consistent with the project's lean bias

### OKF spec vs. this project (decisions made after reading SPEC.md)

- **`type`** (OKF's only required frontmatter field) — **not modeled**. Skipped.
- **`resource`** (URI to an external asset a concept describes) — **not modeled**.
  Skipped; nodes here are self-contained wiki content, not external-asset descriptors.
- **`log.md`** (per-directory chronological change history) — **not modeled at all**,
  not even a derived/read-only version. Conflicts with "no version history" below.
- **Link validation, frontmatter-shape validation, and footnote resolution** — all
  **advisory-only, never block writes** (`kb.okf.find_broken_links`,
  `validate_frontmatter`, `find_unresolved_footnotes`). Matches OKF's own "consumers
  must tolerate broken links" rule and the existing `sources[].resource`-is-opaque-text
  precedent; nothing in OKF beyond the (unmodeled) `type` field is actually mandatory.
- **Attested Computation subspec** (runtime/parameters/computation/executor/attester)
  — out of scope; this is a context/wiki store, not an execution framework.

### Explicitly rejected / deferred concepts (don't reintroduce without asking)

- **No version history** — current-state-only, chosen multiple times (including
  against OKF's own `log.md` convention). No audit/version table, no
  optimistic-concurrency counter, no derived change-log view.
- **No workspace/tenancy concept** — one global file tree. "Manifest" is the only
  grouping mechanism, not a second tenancy boundary.
- **DAL/OKF separation is code-level, not schema-level** — the `files` table is not
  split into a generic-DAL table + separate OKF-owned table. OKF-specific columns live
  directly on `files`, but `dal.py` still has zero import-level dependency on `okf.py`.
- **Delete is soft, not hard** — `deleted_at`, with cascading soft-delete for folders
  (deleting a folder soft-deletes its whole subtree). `restore_node` only restores the
  single node, not descendants (deliberate — some descendants may have been
  independently deleted earlier).
- **OKF's `type` and `resource` fields** — considered and skipped, see above; don't
  add columns for these without a concrete need.
- **Auto-generated `index.md`-style directory index nodes** — built once (an
  `is_generated_index` column + `kb.okf.regenerate_index`, wired into every `kb.storage.dal`
  mutation), then removed: most uploaded markdown won't have a `description` set (and
  we deliberately never auto-generate one), so an auto-index listing that can't show
  descriptions is no better than just listing children — not worth the schema/code
  weight, and it raises the entry price for uploading already-existing files. Don't
  re-add without asking.

## DAL primitives (`src/kb/storage/dal.py`)

All functions take an explicit `Session` — no global/module-level session usage. Reads
default to excluding soft-deleted rows (`include_deleted=False`).

Nodes: `get_node`, `list_children`, `list_descendants` (recursive CTE on `parent_id`,
relied on by cascade-delete/cycle-detection), `create_file`, `update_node`, `move_node`
(raises `TreeCycleError` if the move would place a node under its own descendant),
`delete_node` (cascading soft delete), `restore_node`, `get_content`, `query_metadata`
(filter by tags/status/kind/parent_id).

Manifests: `create_manifest`, `get_manifest`, `list_manifests`, `add_manifest_member`
(raises `ManifestCycleError` on a would-be cycle), `remove_manifest_member`,
`resolve_manifest` (dynamic recursive resolution to the concrete file/folder set a
manifest currently expands to).

## OKF layer primitives (`src/kb/okf.py`)

Two calling conventions, by whether cross-node lookups are needed:

**Take `(session, node_id)`** (need to look at other rows):
- `find_broken_links(session, node_id)` — advisory only, never raises; reports
  `db://files/<uuid>` links in a node's content that don't resolve to a real,
  non-deleted node (non-internal links are out of scope, ignored).

**Take a `File` object directly, no `Session`** (everything needed is on that row):
- `validate_frontmatter(node)` — advisory only, never raises; reports shape problems
  in `sources[]`/`verified[]`/`generated` against OKF's documented shapes (missing
  `sources[].resource`, missing/malformed `by`/`at` on `verified`/`generated` entries,
  `by` not matching the `human:<id>`/`process:<id>`/`<producer>/<version>` actor
  convention). None of this is DB-enforced — purely informational.
- `extract_footnote_refs(content)` — in-body footnote reference labels (`[^label]`),
  excluding definition lines (`[^label]: ...`).
- `find_unresolved_footnotes(node)` — advisory; footnote refs in `node.content` with
  no matching `id` in `node.sources[]`.
- `is_stale(node, *, now=None)` — `True` when `stale_after` is set and has passed.

**Pure, no node at all**: `extract_links(content)` — every markdown link target in a
string, in order.

## KB service / API primitives (`src/kb/service.py`, `src/kb/api/app.py`)

`service.py` re-exports every `kb.storage.dal` node/manifest function 1:1, same
explicit-`Session`-argument style, same exceptions (`TreeCycleError`,
`ManifestCycleError`, plain `ValueError` for "no such node/manifest") — it's the
chokepoint future authz/validation hooks will land in, not a redesign of the DAL's
surface. The one new function: `get_warnings(session, node)` composes all four
`kb.okf` advisory checks into a single `list[str]` report for a node.

`api/app.py` is a thin FastAPI wrapper: no business logic, just request/response shaping
via `schemas.py` and HTTP-status mapping (`TreeCycleError`/`ManifestCycleError` → 409,
`ValueError` → 404). Routes: `POST/GET/PATCH/DELETE /files/{id}`,
`POST /files/{id}/move`, `POST /files/{id}/restore`, `GET /files/{id}/children`,
`GET /files/roots`, `GET /files` (query filters), and the `/manifests` equivalents
(`POST/GET /manifests`, `GET /manifests/{id}`, `POST/DELETE /manifests/{id}/members`,
`GET /manifests/{id}/resolve`), and `GET /ingest/extensions` / `POST /ingest` (folder upload). Single-resource file responses (`FileRead`) include a
`warnings` field from `get_warnings`; list responses (`FileSummary`) omit `content` and
`warnings` to avoid running the advisory checks on every row of a listing.

A static dev UI (`src/kb/api/static/index.html`, vanilla JS, no logic) is mounted at `/ui` for
browsing the tree and exercising the CRUD/manifest routes.

`FileUpdate` deliberately excludes `parent_id` — moving a node has to go through
`POST /files/{id}/move`, which runs `move_node`'s cycle detection; `update_node` does
not. `ManifestMemberCreate` enforces "exactly one of `file_id`/`child_manifest_id`" at
the Pydantic layer (a native 422), so `dal.add_manifest_member`'s `ValueError` for that
same shape problem is effectively unreachable through the API — the global `ValueError`
→ 404 handler stays correct (it only ever means "no such id").

## Folder ingestion (`src/kb/ingest/`, `scripts/ingest_folder.py`)

`ingest_folder(session, root, *, parent_id=None, registry=None, tags=None) ->
IngestReport` mirrors a local directory into the tree: dirs → folder nodes (title = dir
name), supported files → file nodes, each with `sources=[{"resource": "file:///..."}]`
and the given `tags`. Unsupported files go in `report.skipped`, processor errors in
`report.failed` (per-file, never fatal). Hidden entries and symlinks are ignored. A dir
with no supported files (e.g. images only) gets no node. The root always does. Never
commits; the caller owns the transaction. Re-ingest always creates a new subtree (no
dedupe/upsert, deliberate).

It's two steps. **`plan_folder(root, ...) -> FolderPlan`** walks the dir and runs the
processors without touching the DB. It returns `PlannedNode`s (the root plus one per
file, addressed by `/`-joined path) and the skipped/failed lists. Then
**`materialize(session, plan, *, parent_id, folder_fields)`** (`ingest/plan.py`) is the one place
a plan becomes nodes. Ancestors the plan doesn't list are *implied* and created as plain
folders (title = path segment, `folder_fields`) only when something below them is
created, which is why empty dirs get no node. Explicit folders are always created.
`parent_id` must be an existing folder and the plan must contain its root (`""`), else
`ValueError`. It returns `{path: File}` in creation order. Any producer that builds a
subtree should emit a plan and call `materialize` (the QASPER loader does), not loop
over `create_file` by hand.

**Extension point** (`ingest/processors/base.py`): a `Processor` has `name`, `extensions` (lowercase,
with dot) and `process(path) -> ProcessedDocument(title, content, extra)`, where
`extra` holds additional `files` columns. To add a format (PDF, docx...), write one
processor module and register it in `default_registry()`. `plan_folder` doesn't change.
Only `MarkdownProcessor` exists today: content stored verbatim, **no frontmatter
parsing** (user's choice), title = first `# ` H1 else filename stem.

CLI: `python scripts/ingest_folder.py PATH [--parent-id UUID] [--tag T ...] [--dry-run]`.

**Uploads** (`upload.py`): `ingest_upload(session, [(rel_path, bytes), ...], ...)` writes
the files into a temp dir mirroring the tree and runs `ingest_folder` over it (so
processors stay path-based). Paths must be relative, `/`-separated, with no `..`, and
all under one top-level folder, whose name becomes the root title. Anything else raises
`UploadError` (a `ValueError` subclass, mapped to 422 ahead of the generic 404 handler).
Upload sources are `upload:<rel path>`, not temp-dir file URIs. API:
`GET /ingest/extensions` and `POST /ingest` (multipart: `files[]` + a parallel `paths[]`,
optional `parent_id`, `tags[]`). Needs `python-multipart`. The dev UI has "⇪ ingest
folder" (root) and "Ingest folder here…" (on folders), using `<input webkitdirectory>`.
It filters hidden and unsupported files client-side, so those are never uploaded. No
upload size limit yet.

**Non-markdown formats** (`ingest/processors/converters.py`): `LoaderProcessor(name, extensions,
loader_factory)` runs any LangChain `BaseLoader` as a `Processor` (docs joined, title =
first H1 else stem, empty text → failed). Built-ins: `PyMuPDF4LLMLoader(mode="single")`
for `.pdf`; `DoclingLoader(export_type=MARKDOWN)` for `.pdf .docx .pptx .html .htm` when
the optional `docling` extra is installed (it then takes `.pdf`; it pulls in torch).

**Raw originals** (`src/kb/storage/blobs.py`): processors with `retain_original = True` (the
loader ones, not markdown) get their source bytes stored via `put_original` and the
`blob_*` columns set. Store from env: `BLOB_BUCKET` (+`BLOB_ENDPOINT_URL`,
`BLOB_PREFIX`) → `S3ByteStore` (boto3; LangChain has no S3 write store); else
`BLOB_LOCAL_DIR` → `LocalFileStore`; else none (originals not kept). `set_blob_store`
for tests. `plan_folder(..., blob_store=None)` stays I/O-free without a store;
`ingest_folder`/`ingest_upload` default to the env store. A blob error fails only that
file. Blobs are written before the DB commit, so a rolled-back ingest can leave
(harmless, deduped) orphans. `GET /files/{id}/raw` downloads it (404 if none).

Tests: `tests/test_ingest.py` (plan tests need no DB; materialize/walker/upload tests
do), `tests/test_blobs.py`.

## Semantic index (`src/kb/semantic_index/`, `src/kb/retrieval/semantic.py`)

Built from LangChain parts; only KB-specific glue is hand-written.

- **Store** (`semantic_index/vectorstore.py`): `langchain-postgres` `PGVectorStore` bound to `kb_chunks`
  (columns `langchain_id`, `content`, `embedding vector(EMBEDDING_DIM)`, `file_id` FK
  `ON DELETE CASCADE`, `heading`, `start_line`, `end_line`, `langchain_metadata`; HNSW
  cosine) + `SQLRecordManager` (table `upsertion_record`, namespace
  `kb_chunks/<model>`) + `init_embeddings(EMBEDDINGS_MODEL)` (default
  `ollama:nomic-embed-text`, 768d). `get_index_store()`/`set_index_store()`; tests use
  `DeterministicFakeEmbedding`. Searches run with `hnsw.iterative_scan=relaxed_order`
  so narrow `$in` filters still return k hits. `chunk_key_encoder` makes uuid chunk ids
  (the default sha1 one warns; sha256 hex doesn't fit the uuid column).
- **Chunking** (`semantic_index/chunking.py`): `FileNodeLoader` yields one Document per active node with
  content (files *and* folders). Only the markdown **body** is embedded, not frontmatter
  (so retags re-embed nothing), but `start_line`/`end_line` are lines of the
  `dci`-rendered virtual file, so `read_lines(path, offset=start_line)` returns the
  chunk. `RecursiveCharacterTextSplitter.from_language(MARKDOWN)`, 1500 chars / 200
  overlap. `heading` = heading path at the chunk start ("Methods > Data"). Embedded text
  is prefixed with `"<title> > <heading>"`; `chunk_text(doc)` strips it.
- **Sync** (`semantic_index/indexer.py`): `index_files(ids)` takes *any* touched ids — active nodes are
  re-indexed via `langchain_core.indexing.index(cleanup="incremental",
  source_id_key="file_id")` (unchanged chunks skipped, never re-embedded), deleted /
  missing / content-less ones unindexed. Never raises per file (`IndexResult.failed`).
  Each file goes to `index()` as **one batch** (`batch_size=len(chunks)`): incremental
  cleanup runs after every batch, so a file split across the default 100-chunk batches
  had its later chunks deleted and re-embedded on every run (bug hit on a 162-chunk
  page; regression test `test_reindex_unchanged_large_file_is_all_skipped`).
  `unindex_files(ids)` goes through record-manager keys (incremental cleanup can't drop
  a file absent from the batch). `reindex_all()` = full cleanup, file by file. All open
  their own session: call them **after commit**, and not inside a running event loop.
- **Index after writes** (`api/app.py`): create/patch/delete (+ descendants)/restore/ingest
  commit explicitly, then schedule `index_files` as a `BackgroundTask`; failures are only
  logged. Move/manifest routes don't reindex. `KB_AUTO_INDEX=0` turns it off (dev
  without an embeddings server). Repair: `POST /index/reindex` or `scripts/reindex.py
  --all`. `scripts/ingest_folder.py --index` and `scripts/eval/load_qasper.py --index` opt in.
- **Search** (`retrieval/semantic.py`): `service.semantic_search(session, manifest_id, query, *, k,
  tags, status)` → `SearchHit(file_id, path, title, heading, start_line, end_line,
  snippet, score)`. Scope = manifest nodes (∩ `query_metadata` tags/status) as a
  `file_id $in` filter — tags/status are never copied onto chunks. `score` is cosine
  **similarity** (higher = closer). Route: `GET /manifests/{id}/semantic?q=&k=`.

Tests: `tests/test_semantic_index.py`, `tests/test_semantic_search.py`.

## Agent tools (`src/kb/retrieval/agent_tools.py`)

`AgentTools(manifest_id, *, session_factory=SessionLocal)` adapts the DCI tools for an agent.
It sits at the same seam (`kb.service`'s `list_paths`/`search_lines`/`read_lines`) as the
REST routes. Methods `list_paths`, `search_lines`, `read_lines` return plain text. Each
call opens its own session. A bad call (unknown path, bad regex, out-of-range arg)
returns an `error: ...` string instead of raising, so the model can recover.
`semantic_search(query, k)` adds the semantic index (hits as `path:start-end [heading]
(score)` + snippet). `as_langchain(include_semantic=False)` wraps them as LangChain
tools, the 4th only on request. Their docstrings are the tool
descriptions the model reads, so tune tool wording there.

## Verification status

Layers 1 and 2 have both been verified against a live Postgres instance: tree ops,
cascade soft-delete + restore, manifest resolution (dynamic folder expansion, nested
manifests, both cycle-rejection paths), broken-link detection, frontmatter-shape
validation, footnote resolution, and `is_stale`/`stale_after` round-tripping as a real
timestamp — via throwaway scripts (`session.rollback()` in `finally`, deleted after
each run), not a committed test suite. Layer 3 was verified by running `uvicorn` against
the local Postgres and exercising the full golden path plus error-mapping cases
(self-cycle move → 409, unknown id → 404, malformed manifest-member body → 422) with
`curl`; the DB was reset to empty afterward (`alembic downgrade base && alembic upgrade
head`). There is now a committed pytest suite (`pytest -m "not llm"`, needs
`TEST_DATABASE_URL` pointing at a pgvector Postgres whose DB name ends in `_test`); it
passes in the `kb` env (86 passed, Docling test skipped). The tests use fake embeddings;
real Ollama embeddings were verified on the dev DB (6 pages → 222 chunks, a second
`reindex.py --all` skips all of them, and manifest-scoped `semantic_search` returns
ranked hits).

## QASPER evaluation

`scripts/eval/load_qasper.py` ingests QASPER papers (`paper_nodes` → a `PlannedNode` plan →
`kb.ingest.materialize`), one folder per
paper that **mirrors the paper's own outline**: sections become numbered files
(`01-introduction.md`), sections with subsections become numbered folders (their lead
text as folder content), plus `metadata.md` (id, arXiv link, counts) and `figures/` /
`tables/` (one file per caption). The paper folder's content is title + abstract +
outline, and its `description` is the abstract's first sentence. Each section keeps
its original heading as an alias and gets a `role:<role>` tag (intro / related /
method / training / setup / baselines / results / conclusion / other) from keyword
heuristics (`classify_section`) -- a label only, it never moves text. An earlier
fixed-template layout (introduction.md, methods/, experiments/...) was replaced by this
because 31% of sections matched no rule and files became grab bags. Questions/answers
are never stored in the KB. `tests/eval/test_qasper.py` is the deterministic suite (committed
20-paper fixture `tests/eval/fixtures/qasper_20.jsonl`); `tests/eval/test_qasper_llm.py` runs a
LangGraph agent (Anthropic, Ollama or any OpenAI-compatible server) bound to
`AgentTools(...).as_langchain()`, scored by Answer-F1 (`QASPER_SEMANTIC=1` indexes the
corpus once and adds the `semantic_search` tool, for comparing). Replaced the earlier HotPotQA eval
(its test file has been deleted).

## Known gotcha already hit once

Alembic + `postgresql.ENUM`: if you explicitly call `some_enum.create(op.get_bind(),
checkfirst=True)` and *also* use that same ENUM object inline in a `create_table`/
`add_column` column definition, SQLAlchemy will try to create the type a second time
(`create_table`'s own `checkfirst` defaults to `False`) and fail with `DuplicateObject`.
Fix: always construct migration-local enum objects with `create_type=False`, and do the
actual create/drop explicitly. Both `file_status` and `file_kind` (in `0001_create_schema.py`)
follow this pattern — copy it for any new enum.

## Natural next steps (not started)

- Authz, manifest boundaries beyond storage, concurrency checks, real validation hooks
  (currently a DB constraint violation from the API surfaces as a raw 500) — all
  explicitly deferred from layer 3, not designed yet.
- Run the QASPER LLM eval with `QASPER_SEMANTIC=1` vs without (Ollama embeddings are
  now set up; the agent model still needs a tool-calling LLM) to see if
  semantic search earns its place; then consider `HybridSearchConfig` (keyword + vector
  fusion in `langchain-postgres`) and an index-status route.
- Background indexing is in-process (`BackgroundTask`); a real worker would loop
  `index_files`/`reindex_all`. No upload size limit.
