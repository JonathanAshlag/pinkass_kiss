"""Workflow management endpoints (admin only)."""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from app.models.user import User
from app.models.workflow import WorkflowCreate, WorkflowUpdate
from app.routers.deps import require_admin
from app.services.workflows import (
    create_workflow, get_workflow, list_workflows,
    update_workflow, delete_workflow, get_workflow_history,
)
from app.services.users import assign_workflow

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("")
async def create_workflow_endpoint(data: WorkflowCreate, user: User = Depends(require_admin)):
    """Create a new workflow."""
    wf = await create_workflow(data, user.user_id)
    return wf.model_dump(mode="json")


@router.get("")
async def list_workflows_endpoint(user: User = Depends(require_admin)):
    """List all workflows."""
    wfs = await list_workflows()
    return [wf.model_dump(mode="json") for wf in wfs]


@router.get("/{workflow_id}")
async def get_workflow_endpoint(workflow_id: str, user: User = Depends(require_admin)):
    """Get a workflow by ID."""
    wf = await get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf.model_dump(mode="json")


@router.put("/{workflow_id}")
async def update_workflow_endpoint(
    workflow_id: str, data: WorkflowUpdate, user: User = Depends(require_admin)
):
    """Update a workflow."""
    wf = await update_workflow(workflow_id, data, user.user_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf.model_dump(mode="json")


@router.delete("/{workflow_id}")
async def delete_workflow_endpoint(workflow_id: str, user: User = Depends(require_admin)):
    """Delete a workflow."""
    success = await delete_workflow(workflow_id, user.user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"status": "deleted"}


@router.get("/{workflow_id}/history")
async def workflow_history(workflow_id: str, user: User = Depends(require_admin)):
    """Get workflow change history."""
    history = await get_workflow_history(workflow_id)
    if not history:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return history
