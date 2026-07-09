"""Initialize MongoDB indexes for Pinkas.

Run this script once before starting the application:
    python init_db.py
"""

import asyncio
import os

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "pinkas")


async def create_indexes():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB]

    # Pages: text index for search
    await db.pages.create_index(
        [("title", "text"), ("content", "text")],
        name="pages_text_search",
    )
    # Pages: parent_id for tree queries
    await db.pages.create_index("parent_id", name="pages_parent_id")
    # Pages: next_approval_date for expiry job
    await db.pages.create_index("next_approval_date", name="pages_approval_date")
    # Pages: page_id unique
    await db.pages.create_index("page_id", unique=True, name="pages_page_id")
    # Pages: status for filtering
    await db.pages.create_index("status", name="pages_status")
    # Pages: trust tier for verification queries
    await db.pages.create_index("trust_tier", name="pages_trust_tier")
    # Pages: trust tier + inbound link count for ranked retrieval
    await db.pages.create_index(
        [("trust_tier", 1), ("inbound_link_count", -1)],
        name="pages_trust_link_rank",
    )

    # Users: user_id unique
    await db.users.create_index("user_id", unique=True, name="users_user_id")

    # Workflows: workflow_id unique
    await db.workflows.create_index("workflow_id", unique=True, name="workflows_workflow_id")

    # Requests: request_id unique
    await db.requests.create_index("request_id", unique=True, name="requests_request_id")
    # Requests: status for filtering pending
    await db.requests.create_index("status", name="requests_status")
    # Requests: requested_by for user's requests
    await db.requests.create_index("requested_by", name="requests_requested_by")

    # Source files: file_id
    await db.source_files.create_index("file_id", unique=True, name="source_files_file_id")

    print("✓ All indexes created successfully")
    print(f"  Database: {MONGO_DB}")
    print(f"  URI: {MONGO_URI}")

    client.close()


if __name__ == "__main__":
    asyncio.run(create_indexes())
