"""Workflow CRUD service."""

import logging
from typing import Optional

from app.models.page import HistoryEntry
from app.models.workflow import Workflow, WorkflowCreate, WorkflowUpdate
from app.storage.base import WorkflowRepository

logger = logging.getLogger("pinkas.workflows")


async def create_workflow(data: WorkflowCreate, user_id: str, repo: WorkflowRepository) -> Workflow:
    wf = Workflow(
        name=data.name,
        description=data.description,
        steps=data.steps,
    )
    wf.history.append(HistoryEntry(
        user_id=user_id,
        action="create",
        snapshot=f"Created workflow: {data.name}",
    ))
    await repo.create(wf)
    return wf


async def get_workflow(workflow_id: str, repo: WorkflowRepository) -> Optional[Workflow]:
    return await repo.get(workflow_id)


async def list_workflows(repo: WorkflowRepository) -> list[Workflow]:
    return await repo.list_all()


async def update_workflow(
    workflow_id: str, data: WorkflowUpdate, user_id: str, repo: WorkflowRepository
) -> Optional[Workflow]:
    update_fields: dict = {}
    if data.name is not None:
        update_fields["name"] = data.name
    if data.description is not None:
        update_fields["description"] = data.description
    if data.steps is not None:
        update_fields["steps"] = data.steps

    history_entry = HistoryEntry(
        user_id=user_id,
        action="edit",
        diff=str(update_fields),
    )
    await repo.update_with_history(workflow_id, update_fields, history_entry)
    return await repo.get(workflow_id)


async def delete_workflow(workflow_id: str, user_id: str, repo: WorkflowRepository) -> bool:
    history_entry = HistoryEntry(user_id=user_id, action="delete")
    return await repo.delete_with_history(workflow_id, history_entry)


async def get_workflow_history(workflow_id: str, repo: WorkflowRepository) -> list[dict]:
    wf = await repo.get(workflow_id)
    if not wf:
        return []
    return [h.model_dump(mode="json") for h in wf.history]
