# First Indoor Experiment — Connect Pinkas to Company IP

Checklist for moving this repo from the toy/demo setup to your real organizational
data. Work top to bottom; each section has the concrete commands/files involved.

## 0. Decisions to make before touching anything

- [ ] **DB backend.** Your `.env` currently sets `DB_BACKEND=postgres` — make sure
      `POSTGRES_URI` points at a real, reachable Postgres instance before you start.
      If you don't specifically need structured/SQL storage, switching to
      `DB_BACKEND=mongodb` removes this dependency entirely (Mongo is required
      either way, for GridFS).
- [ ] **Classification triangles.** Decide whether you're using the access-control
      feature at all for this experiment. It's off by default (`classification_api_url`
      empty in `app/config.py:16`) and stays off until you point it at a real service.
- [ ] **Elasticsearch auth mode.** Your `.env` is currently configured with basic
      auth (`ES_USERNAME=elastic`, a password) — confirm that's actually your real
      cluster's auth and not a leftover from earlier testing before relying on it.

## 1. Define the real tag taxonomy

File: `app/IP/tags.py` — this is the one place the repo intentionally keeps out of
reviewed application code for exactly this reason (see `README.md:308`).

```python
# app/IP/tags.py
ALLOWED_TAGS: list[str] = [
    # replace the placeholder demo list with your real, closed vocabulary
]
```

- [ ] Replace `ALLOWED_TAGS` with the real list. This has no dependency on
      `scripts/init_db.py` or the database — tags aren't stored anywhere, they're a
      plain Python list loaded into memory, so you can edit this file independently,
      before or after resetting the DB.
- [ ] Tags are a **closed vocabulary** — `PageCreate`/`PageUpdate` reject anything not
      in this list with HTTP 422 (`app/models/page.py:74-121`). Get the list right
      before anyone starts creating real pages, or edits will bounce.
- [ ] **Restart the API process after every edit to this file.** `ALLOWED_TAGS` is
      imported once at process startup (`app/models/page.py:10`,
      `app/routers/pages.py:8`) — since uvicorn isn't run with `--reload` in the
      standard setup, changes here won't take effect until you restart it.
- [ ] Removing/renaming a tag isn't retroactive-safe: existing pages keep the old tag
      value untouched, but the *next* edit to one of those pages will fail validation
      (`PageUpdate` re-validates the full submitted tag list) unless that edit also
      updates its tags.
- [ ] Remember tag inheritance is **forward-only**: child pages inherit a parent's
      tags at write time only; changing a parent's tags later does not retroactively
      touch existing descendants.
- [ ] `GET /pages/tags` serves this list to the Streamlit tag pickers and the
      folder-style "עיון" browse UI — reflects the new list as soon as the API process
      is back up, no separate UI config needed.

## 2. (Optional) Review the other `app/IP/` content

`app/IP/prompts/` (`ingestion.py`, `retrieval.py`, `agent_tools.py`) holds the LLM
system prompts — also placeholder/demo content by the same convention as tags.

- [ ] Decide if the demo prompts are good enough for the first experiment or need
      rewriting for your real domain/terminology.

## 3. Classification access control (only if you decided "yes" in step 0)

- [ ] Stand up (or point at) a service exposing
      `GET {CLASSIFICATION_API_URL}/users/{user_id}/classifications` returning:
      ```json
      {"triangles": [{"id": "alpha", "level": 3}, {"id": "beta", "level": 4}]}
      ```
- [ ] Set `CLASSIFICATION_API_URL` in `.env`.
- [ ] Know the fail-closed behavior: if that API call errors, the user is treated as
      having zero triangles and loses access to every classified page — test this
      deliberately once, don't discover it by accident.
- [ ] If you decided "no" for now: leave `CLASSIFICATION_API_URL` blank. Feature is
      fully disabled and every page is accessible — nothing else to do.

## 4. Reset schema / delete the toy run

Per `.claude/rules/postgres-migrations.md`, this app is pre-launch, so a hard reset
is the sanctioned path — no data to preserve from the toy run. MongoDB always holds
file blobs (GridFS) regardless of `DB_BACKEND`, so it gets wiped either way; the
Postgres step only applies if that's your structured-data backend. Every command
below reads config straight from `.env` via `app.config.settings` — run them from
the repo root, no need to `export`/`source` anything first.

**If `DB_BACKEND=postgres`** — copy-paste the whole block:

```bash
python -c "
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

async def main():
    client = AsyncIOMotorClient(settings.mongo_uri)
    await client.drop_database(settings.mongo_db)
    client.close()

asyncio.run(main())
"
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
python scripts/create_admin.py <your_user_id> "<your name>"
```

**If `DB_BACKEND=mongodb`** — copy-paste the whole block:

```bash
python -c "
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

async def main():
    client = AsyncIOMotorClient(settings.mongo_uri)
    await client.drop_database(settings.mongo_db)
    client.close()

asyncio.run(main())
"
python scripts/init_db.py
python scripts/create_admin.py <your_user_id> "<your name>"
```

- [ ] Replace `<your_user_id>` / `"<your name>"` in the block above with your real
      values before running — that's the identity you'll log in with (auth is the
      trusted `X-User-Id` header; see `app/routers/deps.py`).
- [ ] Do **not** run `scripts/seed_db.py` against this setup — it's demo/toy content
      (same category as the placeholder tags/prompts), not something you want mixed
      into real company IP. It's also destructive on its own terms: it unconditionally
      deletes every existing user/workflow/page/request before reseeding, so running
      it here would immediately wipe the admin user you just created.
- [ ] Confirm `MONGO_DB` in `.env` is the real database name you want, not the
      leftover toy value (`pinkass_kiss`).

## 5. Connect to your real Elasticsearch

Config lives in `app/config.py:20-25` / `.env`.

- [ ] Set `ES_HOSTS` to your real cluster endpoint(s) (comma-separated if multiple).
- [ ] Pick exactly **one** auth mode (they're mutually exclusive per `README.md:362-370`):
      - API key: `ES_API_KEY`
      - basic auth: `ES_USERNAME` + `ES_PASSWORD`
      - Elastic Cloud: `ES_CLOUD_ID`
- [ ] Rotate/replace whatever password is currently sitting in `.env` — it looks
      like a leftover from the toy run and shouldn't be reused for the real cluster.
- [ ] Set `ES_AUDIT_ENABLED=true`.
- [ ] Pinkas writes both audit and agent-retrieval events to `pinkas-events-YYYY.MM`
      (monthly rotation, `event_kind` field distinguishes the two) but does **not**
      create an index template or ILM policy itself. Dynamic mapping will work without
      one, but the app runs exact-match `term` filters on fields like `event_kind` and
      `mode` (`app/routers/agent_logs.py`) that need `keyword` typing to be reliable
      across every monthly rotation — apply `es_index_template.json` once, before
      traffic starts flowing:
      ```bash
      curl -X PUT "$ES_HOSTS/_index_template/pinkas-events" \
        -H "Content-Type: application/json" \
        --data-binary @es_index_template.json
      ```
      If your org enforces ILM, set that up on the ES side too, same timing.
- [ ] `.env` is already gitignored (`.gitignore` — `.env`, `.env.*`, keeps
      `.env.example`) — don't override that.

## 6. Deploy the Kibana agent-usage dashboard

`dashboards/pinkas-ops.json` is a self-contained agent-usage dashboard (per-agent
interaction volume, miss rate, latency, plus a recent-activity table) — every panel
is inline ES|QL, no saved data views, no library visualizations. See the
[Dashboards](README.md#dashboards) section in the README for full details.

- [ ] Confirm the index template step in section 5 above is done — the dashboard's
      per-agent breakdowns rely on `event_kind`/`mode` staying reliably typed across
      monthly index rotations, same as the rest of the app.
- [ ] Point it at a Kibana **9.4+** instance connected to the same Elasticsearch
      cluster from section 5 (inline ES|QL dashboard panels aren't available on
      older Kibana versions).
- [ ] Deploy:
      ```bash
      KIBANA_URL=https://your-kibana.example.com \
      KIBANA_API_KEY=your-api-key \
      INDEX_PREFIX=pinkas-events \
      ./deploy.sh
      ```
      (`INDEX_PREFIX` only needs to change if your real cluster uses a different
      index name than `pinkas-events-*`.)
- [ ] `deploy.sh` upserts dashboard ID `pinkas-ops` and exits non-zero if the
      deploy fails or if the round-tripped dashboard turns out to contain any
      external saved-object reference — no manual verification needed beyond
      checking the script's exit code.
- [ ] Git is the source of truth for this dashboard: editing it in the Kibana UI is
      fine for exploration, but the next `./deploy.sh` run overwrites those changes
      with whatever is committed in `dashboards/pinkas-ops.json`.
- [ ] Panels are agent-scoped (`event_kind: retrieval`) — they'll be empty until
      real agents exist and start making search/fetch/scan/bundle calls, same
      prerequisite as any other agent-facing feature in this checklist.

## 7. Bring it all up and verify end-to-end

```bash
# with mongo, postgres (if used), elasticsearch, and your LLM server all reachable
python scripts/init_db.py
uvicorn app.main:app --host 0.0.0.0 --port 8080 &
streamlit run streamlit_app/app.py --server.port 8501
```

- [ ] Create one real page using a real tag; confirm `GET /pages/tags` returns your
      real taxonomy (not the animal-themed placeholder list).
- [ ] Confirm an audit doc lands in `pinkas-events-<current-YYYY.MM>` (filter on
      `event_kind: audit`) in your real ES cluster after that page create.
- [ ] If classification is enabled: create one classified page and verify a user
      without the matching triangle is denied, and one with it is allowed.
- [ ] Confirm `OPENAI_BASE_URL`/`OPENAI_MODEL` in `.env` still point at a reachable
      local LLM server if you're testing ingestion or Q&A in this same pass.
