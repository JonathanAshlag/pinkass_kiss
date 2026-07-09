"""User CRUD service."""

import logging
from typing import Optional

from app.db import get_db
from app.models.user import User, UserCreate, UserUpdate

logger = logging.getLogger("pinkas.users")


async def create_user(data: UserCreate) -> User:
    """Create a new user."""
    db = get_db()
    user = User(
        name=data.name,
        permission_level=data.permission_level,
        workflow_id=data.workflow_id,
        page_permissions=data.page_permissions,
    )
    await db.users.insert_one(user.model_dump(mode="json"))
    return user


async def get_user(user_id: str) -> Optional[User]:
    """Get a user by ID."""
    db = get_db()
    doc = await db.users.find_one({"user_id": user_id})
    if doc:
        doc.pop("_id", None)
        return User(**doc)
    return None


async def list_users() -> list[User]:
    """List all users."""
    db = get_db()
    users = []
    cursor = db.users.find()
    async for doc in cursor:
        doc.pop("_id", None)
        users.append(User(**doc))
    return users


async def update_user(user_id: str, data: UserUpdate) -> Optional[User]:
    """Update a user."""
    db = get_db()
    update_fields: dict = {}
    if data.name is not None:
        update_fields["name"] = data.name
    if data.permission_level is not None:
        update_fields["permission_level"] = data.permission_level.value
    if data.workflow_id is not None:
        update_fields["workflow_id"] = data.workflow_id
    if data.page_permissions is not None:
        update_fields["page_permissions"] = data.page_permissions

    if update_fields:
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": update_fields}
        )
    return await get_user(user_id)


async def delete_user(user_id: str) -> bool:
    """Delete a user."""
    db = get_db()
    result = await db.users.delete_one({"user_id": user_id})
    return result.deleted_count > 0


async def assign_workflow(user_id: str, workflow_id: Optional[str]) -> Optional[User]:
    """Assign or unassign a workflow to/from a user."""
    db = get_db()
    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {"workflow_id": workflow_id}}
    )
    return await get_user(user_id)
