"""PostgreSQL async engine and session factory."""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(uri: str) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(uri, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def close_engine() -> None:
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def get_session() -> AsyncGenerator[AsyncSession | None, None]:
    if _session_factory is None:
        yield None
        return
    async with _session_factory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    return _session_factory
