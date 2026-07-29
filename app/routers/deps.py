"""Shared dependencies for route handlers."""

from typing import Optional

from fastapi import Header, HTTPException

from app.container import UserRepo
from app.models.audit import UserContext
from app.models.user import User, PermissionLevel
from app.services.permissions import get_user_by_id


async def get_current_user(x_user_id: str = Header(...), user_repo: UserRepo = None) -> User:
    user = await get_user_by_id(x_user_id, user_repo)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def require_editor(x_user_id: str = Header(...), user_repo: UserRepo = None) -> User:
    user = await get_current_user(x_user_id, user_repo)
    if user.permission_level == PermissionLevel.read_only:
        raise HTTPException(status_code=403, detail="Editor or admin permission required")
    return user


async def require_admin(x_user_id: str = Header(...), user_repo: UserRepo = None) -> User:
    user = await get_current_user(x_user_id, user_repo)
    if user.permission_level != PermissionLevel.admin:
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user


async def get_user_context(
    x_user_id: str = Header(...),
    x_client_application_id: Optional[str] = Header(None),
    x_client_session_id: Optional[str] = Header(None),
) -> UserContext:
    return UserContext(
        user_id=x_user_id,
        client_application_id=x_client_application_id,
        client_session_id=x_client_session_id,
    )
