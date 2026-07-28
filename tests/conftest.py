"""Test configuration — runs each test against both MongoDB and Postgres (SQLite in-memory)."""

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.infrastructure import mongo as mongo_module
from app.config import settings


@pytest_asyncio.fixture(autouse=True)
async def mock_db(monkeypatch):
    """Replace the MongoDB client with mongomock for all tests."""
    client = AsyncMongoMockClient()
    monkeypatch.setattr(mongo_module, "_client", client)
    monkeypatch.setattr(settings, "mongo_db", "pinkas_test")
    monkeypatch.setattr(settings, "db_backend", "mongodb")

    def mock_get_db():
        return client["pinkas_test"]

    monkeypatch.setattr(mongo_module, "get_db", mock_get_db)
    yield client["pinkas_test"]

    client.close()


@pytest_asyncio.fixture
async def pg_session():
    """SQLite in-memory async session — stand-in for the Postgres session in tests."""
    from app.infrastructure.postgres.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(params=["mongodb", "postgres"])
def backend(request):
    return request.param


@pytest.fixture
def page_repo(backend, mock_db, pg_session):
    if backend == "postgres":
        from app.storage.postgres.pages import PostgresPageRepository
        return PostgresPageRepository(pg_session, dialect="sqlite")
    from app.storage.mongo.pages import MongoPageRepository
    return MongoPageRepository(mock_db)


@pytest.fixture
def user_repo(backend, mock_db, pg_session):
    if backend == "postgres":
        from app.storage.postgres.users import PostgresUserRepository
        return PostgresUserRepository(pg_session)
    from app.storage.mongo.users import MongoUserRepository
    return MongoUserRepository(mock_db)


@pytest.fixture
def wf_repo(backend, mock_db, pg_session):
    if backend == "postgres":
        from app.storage.postgres.workflows import PostgresWorkflowRepository
        return PostgresWorkflowRepository(pg_session)
    from app.storage.mongo.workflows import MongoWorkflowRepository
    return MongoWorkflowRepository(mock_db)


@pytest.fixture
def req_repo(backend, mock_db, pg_session):
    if backend == "postgres":
        from app.storage.postgres.requests import PostgresRequestRepository
        return PostgresRequestRepository(pg_session)
    from app.storage.mongo.requests import MongoRequestRepository
    return MongoRequestRepository(mock_db)
