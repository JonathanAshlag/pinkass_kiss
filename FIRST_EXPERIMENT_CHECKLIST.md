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

- [ ] Replace `ALLOWED_TAGS` with the real list.
- [ ] Tags are a **closed vocabulary** — `PageCreate`/`PageUpdate` reject anything not
      in this list with HTTP 422 (`app/models/page.py:74-121`). Get the list right
      before anyone starts creating real pages, or edits will bounce.
- [ ] Remember tag inheritance is **forward-only**: child pages inherit a parent's
      tags at write time only; changing a parent's tags later does not retroactively
      touch existing descendants.
- [ ] `GET /pages/tags` serves this list to the Streamlit tag pickers and the
      folder-style "עיון" browse UI — no separate UI config needed once you change
      the Python list.

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
is the sanctioned path — no data to preserve from the toy run.

**If staying on Postgres:**

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
python init_db.py
```

**If switching to / staying on MongoDB:** there's no destructive step required by
the app itself. If you want a fully clean slate (drop the toy collections too):

```bash
# only if you want to wipe existing toy documents, not just re-ensure indexes
mongosh "$MONGO_URI" --eval "db.getSiblingDB('$MONGO_DB').dropDatabase()"
python init_db.py
```

- [ ] Do **not** run `seed_db.py` against this setup — it's demo/toy content
      (same category as the placeholder tags/prompts), not something you want mixed
      into real company IP.
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
- [ ] Pinkas writes to `pinkas-audit-YYYY.MM` (monthly rotation) but does **not**
      create an index template or ILM policy itself — if your org enforces ILM,
      set that up on the ES side before traffic starts flowing.
- [ ] `.env` is already gitignored (`.gitignore` — `.env`, `.env.*`, keeps
      `.env.example`) — don't override that.

## 6. Bring it all up and verify end-to-end

```bash
# with mongo, postgres (if used), elasticsearch, and your LLM server all reachable
python init_db.py
uvicorn app.main:app --host 0.0.0.0 --port 8080 &
streamlit run streamlit_app/app.py --server.port 8501
```

- [ ] Create one real page using a real tag; confirm `GET /pages/tags` returns your
      real taxonomy (not the animal-themed placeholder list).
- [ ] Confirm an audit doc lands in `pinkas-audit-<current-YYYY.MM>` in your real ES
      cluster after that page create.
- [ ] If classification is enabled: create one classified page and verify a user
      without the matching triangle is denied, and one with it is allowed.
- [ ] Confirm `OPENAI_BASE_URL`/`OPENAI_MODEL` in `.env` still point at a reachable
      local LLM server if you're testing ingestion or Q&A in this same pass.
