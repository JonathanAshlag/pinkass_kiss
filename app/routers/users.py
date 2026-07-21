"""User management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel

from app.models.user import User, UserCreate, UserUpdate
from app.routers.deps import require_admin, get_current_user
from app.services.users import (
    create_user, get_user, list_users, update_user, delete_user, assign_workflow,
)

router = APIRouter(prefix="/users", tags=["users"])


class WorkflowAssign(BaseModel):
    workflow_id: Optional[str] = None


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """Get the current user's info (any permission level)."""
    return user.model_dump(mode="json")


@router.post("")
async def create_user_endpoint(data: UserCreate, admin: User = Depends(require_admin)):
    """Create a new user (admin only)."""
    user = await create_user(data)
    return user.model_dump(mode="json")


@router.get("")
async def list_users_endpoint(admin: User = Depends(require_admin)):
    """List all users (admin only)."""
    users = await list_users()
    return [u.model_dump(mode="json") for u in users]


@router.get("/{user_id}")
async def get_user_endpoint(user_id: str, admin: User = Depends(require_admin)):
    """Get a user by ID (admin only)."""
    user = await get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.model_dump(mode="json")


@router.put("/{user_id}")
async def update_user_endpoint(user_id: str, data: UserUpdate, admin: User = Depends(require_admin)):
    """Update a user (admin only)."""
    user = await update_user(user_id, data)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.model_dump(mode="json")


@router.delete("/{user_id}")
async def delete_user_endpoint(user_id: str, admin: User = Depends(require_admin)):
    """Delete a user (admin only)."""
    success = await delete_user(user_id)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "deleted"}


@router.put("/{user_id}/workflow")
async def assign_workflow_endpoint(
    user_id: str, data: WorkflowAssign, admin: User = Depends(require_admin)
):
    """Assign or unassign a workflow to a user (admin only)."""
    user = await assign_workflow(user_id, data.workflow_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.model_dump(mode="json")


