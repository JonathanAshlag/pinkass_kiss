"""Workflow management endpoints (admin only)."""

from fastapi import APIRouter, Depends, HTTPException

from app.container import UserRepo, WorkflowRepo
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
async def create_workflow_endpoint(
    data: WorkflowCreate,
    user: User = Depends(require_admin),
    wf_repo: WorkflowRepo = None,
):
    wf = await create_workflow(data, user.user_id, wf_repo)
    return wf.model_dump(mode="json")


@router.get("")
async def list_workflows_endpoint(
    user: User = Depends(require_admin),
    wf_repo: WorkflowRepo = None,
):
    wfs = await list_workflows(wf_repo)
    return [wf.model_dump(mode="json") for wf in wfs]


@router.get("/{workflow_id}")
async def get_workflow_endpoint(
    workflow_id: str,
    user: User = Depends(require_admin),
    wf_repo: WorkflowRepo = None,
):
    wf = await get_workflow(workflow_id, wf_repo)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf.model_dump(mode="json")


@router.put("/{workflow_id}")
async def update_workflow_endpoint(
    workflow_id: str,
    data: WorkflowUpdate,
    user: User = Depends(require_admin),
    wf_repo: WorkflowRepo = None,
):
    wf = await update_workflow(workflow_id, data, user.user_id, wf_repo)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf.model_dump(mode="json")


@router.delete("/{workflow_id}")
async def delete_workflow_endpoint(
    workflow_id: str,
    user: User = Depends(require_admin),
    wf_repo: WorkflowRepo = None,
):
    success = await delete_workflow(workflow_id, user.user_id, wf_repo)
    if not success:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"status": "deleted"}


@router.get("/{workflow_id}/history")
async def workflow_history(
    workflow_id: str,
    user: User = Depends(require_admin),
    wf_repo: WorkflowRepo = None,
):
    history = await get_workflow_history(workflow_id, wf_repo)
    if not history and not await get_workflow(workflow_id, wf_repo):
        raise HTTPException(status_code=404, detail="Workflow not found")
    return history
