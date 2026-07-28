"""PostgreSQL implementation of UserRepository."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import PermissionLevel, User
from app.storage.base import UserRepository
from app.infrastructure.postgres.models import UserORM


def _orm_to_user(row: UserORM) -> User:
    return User(
        user_id=row.user_id,
        name=row.name,
        permission_level=PermissionLevel(row.permission_level),
        workflow_id=row.workflow_id,
    )


class PostgresUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: str) -> Optional[User]:
        result = await self._s.execute(select(UserORM).where(UserORM.user_id == user_id))
        row = result.scalar_one_or_none()
        return _orm_to_user(row) if row else None

    async def create(self, user: User) -> None:
        self._s.add(UserORM(
            user_id=user.user_id,
            name=user.name,
            permission_level=user.permission_level.value,
            workflow_id=user.workflow_id,
        ))
        await self._s.flush()

    async def list_all(self) -> list[User]:
        result = await self._s.execute(select(UserORM))
        return [_orm_to_user(row) for row in result.scalars().all()]

    async def update_fields(self, user_id: str, fields: dict) -> None:
        if not fields:
            return
        await self._s.execute(
            update(UserORM).where(UserORM.user_id == user_id).values(**fields)
        )
        await self._s.flush()

    async def delete(self, user_id: str) -> bool:
        result = await self._s.execute(delete(UserORM).where(UserORM.user_id == user_id))
        await self._s.flush()
        return result.rowcount > 0
