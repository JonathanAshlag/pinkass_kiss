"""
Test DB wiring. Tests run against the Postgres named by TEST_DATABASE_URL (e.g. the
swarm service in docker/stack.test.yml) and are skipped when it's unset.

`DATABASE_URL` is always overridden with the test URL *before* `kb` is imported, so a
dev database can never be touched by accident.
"""

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import make_url, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))  # for `import load_hotpotqa`

load_dotenv(ROOT / ".env")  # TEST_DATABASE_URL, ANTHROPIC_API_KEY, HOTPOTQA_LLM_* live here
TEST_URL = os.environ.get("TEST_DATABASE_URL")
# kb.db builds its engine at import time; create_engine doesn't connect, so a
# placeholder is fine when the tests are going to be skipped anyway.
os.environ["DATABASE_URL"] = TEST_URL or "postgresql+psycopg://skipped/skipped_test"


@pytest.fixture(scope="session")
def migrated_db():
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL not set (see docker/stack.test.yml)")
    dbname = make_url(TEST_URL).database or ""
    if not dbname.endswith("_test"):
        pytest.exit(f"refusing to run: test database '{dbname}' must end in '_test'")

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")

    _truncate()
    yield
    _truncate()


def _truncate() -> None:
    from kb.db import engine

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE manifest_members, manifests, files CASCADE"))


# --------------------------------------------------------------------------
# HotPotQA fixture data, loaded into the KB once per test session
# --------------------------------------------------------------------------

FIXTURE = Path(__file__).parent / "fixtures" / "hotpotqa_100.jsonl"
ROOT_TITLE = "HotPotQA"


@pytest.fixture(scope="session")
def examples():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="session")
def ids(migrated_db, examples):
    import load_hotpotqa

    return load_hotpotqa.load_into_kb(examples)  # {paragraph title: node id}


@pytest.fixture(scope="session")
def manifests(ids, examples):
    """{question id: manifest id}, each holding exactly that question's paragraphs.
    Plus "corpus": a manifest over the whole HotPotQA folder."""
    from kb import service
    from kb.db import SessionLocal

    out = {}
    with SessionLocal() as session:
        for ex in examples:
            m = service.create_manifest(session, f"hotpotqa-q-{ex['id']}")
            for src in ex["sources"]:
                service.add_manifest_member(session, m.id, file_id=uuid.UUID(ids[src["title"]]))
            out[ex["id"]] = m.id
        corpus = service.create_manifest(session, "hotpotqa-corpus")
        root = next(n for n in service.list_children(session, None) if n.title == ROOT_TITLE)
        service.add_manifest_member(session, corpus.id, file_id=root.id)
        out["corpus"] = corpus.id
        session.commit()
    return out


@pytest.fixture
def session(ids):
    from kb.db import SessionLocal

    with SessionLocal() as s:
        yield s
