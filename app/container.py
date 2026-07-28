"""Composition root — FastAPI dependency factories and background-task context manager.

Reads DB_BACKEND once and returns the right repository implementation. All
routers import the Annotated type aliases defined here; nothing else in the
app touches the backend-selection logic.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.infrastructure.mongo import get_db
from app.infrastructure.postgres.engine import get_session, get_session_factory
from app.storage.base import (
    PageRepository, RequestRepository, SourceFileRepository,
    UserRepository, WorkflowRepository,
)


@dataclass
class RepoSet:
    """Named bundle of repositories for background tasks (scheduler, etc.)."""
    pages: PageRepository
    requests: RequestRepository
    users: UserRepository
    workflows: WorkflowRepository


# ---------------------------------------------------------------------------
# Internal connection deps
# ---------------------------------------------------------------------------

def _mongo_db():
    if settings.db_backend == "mongodb":
        return get_db()
    return None


async def _pg_session() -> AsyncGenerator[AsyncSession | None, None]:
    if settings.db_backend == "postgres":
        async for s in get_session():
            yield s
    else:
        yield None


# ---------------------------------------------------------------------------
# Repository factory deps
# ---------------------------------------------------------------------------

def _page_repo(db=Depends(_mongo_db), session: AsyncSession | None = Depends(_pg_session)) -> PageRepository:
    if settings.db_backend == "postgres":
        from app.storage.postgres.pages import PostgresPageRepository
        return PostgresPageRepository(session)
    from app.storage.mongo.pages import MongoPageRepository
    return MongoPageRepository(db)


def _user_repo(db=Depends(_mongo_db), session: AsyncSession | None = Depends(_pg_session)) -> UserRepository:
    if settings.db_backend == "postgres":
        from app.storage.postgres.users import PostgresUserRepository
        return PostgresUserRepository(session)
    from app.storage.mongo.users import MongoUserRepository
    return MongoUserRepository(db)


def _workflow_repo(db=Depends(_mongo_db), session: AsyncSession | None = Depends(_pg_session)) -> WorkflowRepository:
    if settings.db_backend == "postgres":
        from app.storage.postgres.workflows import PostgresWorkflowRepository
        return PostgresWorkflowRepository(session)
    from app.storage.mongo.workflows import MongoWorkflowRepository
    return MongoWorkflowRepository(db)


def _request_repo(db=Depends(_mongo_db), session: AsyncSession | None = Depends(_pg_session)) -> RequestRepository:
    if settings.db_backend == "postgres":
        from app.storage.postgres.requests import PostgresRequestRepository
        return PostgresRequestRepository(session)
    from app.storage.mongo.requests import MongoRequestRepository
    return MongoRequestRepository(db)


def _source_file_repo(db=Depends(_mongo_db), session: AsyncSession | None = Depends(_pg_session)) -> SourceFileRepository:
    if settings.db_backend == "postgres":
        from app.storage.postgres.source_files import PostgresSourceFileRepository
        return PostgresSourceFileRepository(session)
    from app.storage.mongo.source_files import MongoSourceFileRepository
    return MongoSourceFileRepository(db)


# ---------------------------------------------------------------------------
# Annotated aliases — import these in routers for clean signatures
# ---------------------------------------------------------------------------

PageRepo = Annotated[PageRepository, Depends(_page_repo)]
UserRepo = Annotated[UserRepository, Depends(_user_repo)]
WorkflowRepo = Annotated[WorkflowRepository, Depends(_workflow_repo)]
RequestRepo = Annotated[RequestRepository, Depends(_request_repo)]
SourceFileRepo = Annotated[SourceFileRepository, Depends(_source_file_repo)]


# ---------------------------------------------------------------------------
# Background-task context manager (scheduler and other non-request code)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def background_repos():
    """Yield a RepoSet for background tasks that run outside FastAPI request scope."""
    if settings.db_backend == "postgres":
        from app.storage.postgres.pages import PostgresPageRepository
        from app.storage.postgres.requests import PostgresRequestRepository
        from app.storage.postgres.users import PostgresUserRepository
        from app.storage.postgres.workflows import PostgresWorkflowRepository

        factory = get_session_factory()
        async with factory() as session:
            yield RepoSet(
                pages=PostgresPageRepository(session),
                requests=PostgresRequestRepository(session),
                users=PostgresUserRepository(session),
                workflows=PostgresWorkflowRepository(session),
            )
            await session.commit()
    else:
        from app.storage.mongo.pages import MongoPageRepository
        from app.storage.mongo.requests import MongoRequestRepository
        from app.storage.mongo.users import MongoUserRepository
        from app.storage.mongo.workflows import MongoWorkflowRepository

        db = get_db()
        yield RepoSet(
            pages=MongoPageRepository(db),
            requests=MongoRequestRepository(db),
            users=MongoUserRepository(db),
            workflows=MongoWorkflowRepository(db),
        )
