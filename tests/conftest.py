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
sys.path.insert(0, str(ROOT / "scripts" / "eval"))  # for `import load_qasper`

load_dotenv(ROOT / ".env")  # TEST_DATABASE_URL, ANTHROPIC_API_KEY, E2E_LLM_* live here
TEST_URL = os.environ.get("TEST_DATABASE_URL")
# kb.storage.db builds its engine at import time; create_engine doesn't connect, so a
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
    from kb.storage.db import engine

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE kb_chunks, upsertion_record, manifest_members, manifests, files, folders CASCADE"))


# --------------------------------------------------------------------------
# QASPER fixture data, loaded into the KB once per test module (and removed afterwards)
# --------------------------------------------------------------------------

FIXTURE = Path(__file__).parent / "eval" / "fixtures" / "qasper_20.jsonl"
ROOT_TITLE = "QASPER"


@pytest.fixture(scope="session")
def papers():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def _delete_qasper() -> None:
    """Remove the QASPER corpus and its manifests (leaves everything else alone), so the
    shared test DB isn't polluted for modules that assume they own the KB (reindex_all,
    root-level path assertions, ...)."""
    from kb.storage.db import engine

    subtree = (
        "WITH RECURSIVE t AS (SELECT id FROM folders WHERE title = :t AND parent_id IS NULL"
        " UNION ALL SELECT f.id FROM folders f JOIN t ON f.parent_id = t.id)"
    )
    with engine.begin() as conn:
        # members first: manifests are matched by name, then every row that points at the subtree
        conn.execute(text("DELETE FROM manifest_members WHERE manifest_id IN (SELECT id FROM manifests WHERE name LIKE 'qasper-%')"))
        conn.execute(text("DELETE FROM manifests WHERE name LIKE 'qasper-%'"))
        conn.execute(text(f"{subtree} DELETE FROM files WHERE parent_id IN (SELECT id FROM t)"), {"t": ROOT_TITLE})
        conn.execute(text(f"{subtree} DELETE FROM folders WHERE id IN (SELECT id FROM t)"), {"t": ROOT_TITLE})


@pytest.fixture(scope="module")
def ids(migrated_db, papers):
    import load_qasper

    _delete_qasper()  # a previous aborted run may have left it behind (the loader refuses to overwrite)
    loaded = load_qasper.load_into_kb(papers)  # {paper id: paper folder node id}
    yield loaded
    _delete_qasper()


@pytest.fixture(scope="module")
def manifests(ids, papers):
    """{paper id: manifest id}, each holding exactly that paper's folder (so its whole
    subtree). Plus "corpus": a manifest over the whole QASPER folder."""
    from kb import service
    from kb.storage.db import SessionLocal

    out = {}
    with SessionLocal() as session:
        for paper in papers:
            m = service.create_manifest(session, f"qasper-p-{paper['id']}")
            service.add_manifest_member(session, m.id, node_id=uuid.UUID(ids[paper["id"]]))
            out[paper["id"]] = m.id
        corpus = service.create_manifest(session, "qasper-corpus")
        root = next(n for n in service.list_children(session, None) if n.title == ROOT_TITLE)
        service.add_manifest_member(session, corpus.id, node_id=root.id)
        out["corpus"] = corpus.id
        session.commit()
    return out


@pytest.fixture
def session(ids):
    from kb.storage.db import SessionLocal

    with SessionLocal() as s:
        yield s


@pytest.fixture
def blob_store(monkeypatch):
    """A `BlobStore` over an in-process fake S3 (moto), bucket "bkt", prefix "kb/".
    Fake credentials, so the real AWS_* from .env are never used."""
    import boto3
    from moto import mock_aws

    from kb.storage.blobs import BlobStore

    for var, value in (("AWS_ACCESS_KEY_ID", "x"), ("AWS_SECRET_ACCESS_KEY", "x"), ("AWS_DEFAULT_REGION", "us-east-1")):
        monkeypatch.setenv(var, value)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="bkt")
        yield BlobStore("bkt", prefix="kb/", client=client)
