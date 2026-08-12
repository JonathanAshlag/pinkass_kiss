"""Initialize the database for Pinkas.

Run this script once before starting the application:
    python scripts/init_db.py

Respects DB_BACKEND env var (mongodb / postgres).
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings


async def init_mongo() -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    await db.pages.create_index(
        [("title", "text"), ("aliases", "text")],
        name="pages_text_search",
    )
    await db.pages.create_index("parent_id", name="pages_parent_id")
    await db.pages.create_index("next_approval_date", name="pages_approval_date")
    await db.pages.create_index("page_id", unique=True, name="pages_page_id")
    await db.pages.create_index("status", name="pages_status")
    await db.pages.create_index("trust_tier", name="pages_trust_tier")
    await db.pages.create_index("tags", name="pages_tags")
    await db.pages.create_index(
        [("trust_tier", 1), ("inbound_link_count", -1)],
        name="pages_trust_link_rank",
    )
    await db.users.create_index("user_id", unique=True, name="users_user_id")
    await db.workflows.create_index("workflow_id", unique=True, name="workflows_workflow_id")
    await db.requests.create_index("request_id", unique=True, name="requests_request_id")
    await db.requests.create_index("status", name="requests_status")
    await db.requests.create_index("requested_by", name="requests_requested_by")
    await db.source_files.create_index("file_id", unique=True, name="source_files_file_id")
    await db.agents.create_index("agent_id", unique=True, name="agents_agent_id")
    await db.agents.create_index("api_key_hash", unique=True, name="agents_api_key_hash")
    await db.bundles.create_index("name", unique=True, name="bundles_name")

    print("✓ All MongoDB indexes created successfully")
    print(f"  Database: {settings.mongo_db}")
    print(f"  URI: {settings.mongo_uri}")
    client.close()


def init_postgres() -> None:
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", settings.postgres_uri)
    command.upgrade(alembic_cfg, "head")
    print("✓ PostgreSQL schema migrated to head")
    print(f"  URI: {settings.postgres_uri}")


async def main() -> None:
    print(f"Backend: {settings.db_backend}")
    if settings.db_backend == "postgres":
        # Run in a thread so Alembic's own asyncio.run() doesn't conflict
        # with the outer event loop started by asyncio.run(main()).
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, init_postgres)
    else:
        await init_mongo()


if __name__ == "__main__":
    asyncio.run(main())
