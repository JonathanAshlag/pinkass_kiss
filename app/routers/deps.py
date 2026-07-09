"""Shared dependencies for route handlers."""

from fastapi import Header, HTTPException

from app.models.user import User, PermissionLevel
from app.services.permissions import get_user_by_id


async def get_current_user(x_user_id: str = Header(...)) -> User:
    """Extract and validate the current user from request headers."""
    user = await get_user_by_id(x_user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def require_editor(x_user_id: str = Header(...)) -> User:
    """Require at least editor permission level."""
    user = await get_current_user(x_user_id)
    if user.permission_level == PermissionLevel.read_only:
        raise HTTPException(status_code=403, detail="Editor or admin permission required")
    return user


async def require_admin(x_user_id: str = Header(...)) -> User:
    """Require admin permission level."""
    user = await get_current_user(x_user_id)
    if user.permission_level != PermissionLevel.admin:
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user
