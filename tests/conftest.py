"""Test configuration with mongomock-motor."""

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from app.db import mongo as mongo_module
from app.config import settings


@pytest_asyncio.fixture(autouse=True)
async def mock_db(monkeypatch):
    """Replace the MongoDB client with mongomock for all tests."""
    client = AsyncMongoMockClient()
    monkeypatch.setattr(mongo_module, "_client", client)
    monkeypatch.setattr(settings, "mongo_db", "pinkas_test")

    # Patch get_db to use the mock client
    def mock_get_db():
        return client["pinkas_test"]

    monkeypatch.setattr(mongo_module, "get_db", mock_get_db)
    yield client["pinkas_test"]

    # Clean up
    client.close()
