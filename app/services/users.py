"""User CRUD service."""

import logging
from typing import Optional

from app.models.user import User, UserCreate, UserUpdate
from app.storage.base import UserRepository

logger = logging.getLogger("pinkas.users")


async def create_user(data: UserCreate, repo: UserRepository) -> User:
    user = User(
        name=data.name,
        permission_level=data.permission_level,
        workflow_id=data.workflow_id,
    )
    await repo.create(user)
    return user


async def get_user(user_id: str, repo: UserRepository) -> Optional[User]:
    return await repo.get(user_id)


async def list_users(repo: UserRepository) -> list[User]:
    return await repo.list_all()


async def update_user(user_id: str, data: UserUpdate, repo: UserRepository) -> Optional[User]:
    update_fields: dict = {}
    if data.name is not None:
        update_fields["name"] = data.name
    if data.permission_level is not None:
        update_fields["permission_level"] = data.permission_level.value
    if data.workflow_id is not None:
        update_fields["workflow_id"] = data.workflow_id

    if update_fields:
        await repo.update_fields(user_id, update_fields)
    return await repo.get(user_id)


async def delete_user(user_id: str, repo: UserRepository) -> bool:
    return await repo.delete(user_id)


async def assign_workflow(user_id: str, workflow_id: Optional[str], repo: UserRepository) -> Optional[User]:
    await repo.update_fields(user_id, {"workflow_id": workflow_id})
    return await repo.get(user_id)
