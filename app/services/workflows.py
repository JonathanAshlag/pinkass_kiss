"""Workflow CRUD service."""

import logging
from datetime import datetime
from typing import Optional

from app.db import get_db
from app.models.workflow import Workflow, WorkflowCreate, WorkflowUpdate
from app.models.page import HistoryEntry

logger = logging.getLogger("pinkas.workflows")


async def create_workflow(data: WorkflowCreate, user_id: str) -> Workflow:
    """Create a new workflow."""
    db = get_db()
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
    await db.workflows.insert_one(wf.model_dump(mode="json"))
    return wf


async def get_workflow(workflow_id: str) -> Optional[Workflow]:
    """Get a workflow by ID."""
    db = get_db()
    doc = await db.workflows.find_one({"workflow_id": workflow_id})
    if doc:
        doc.pop("_id", None)
        return Workflow(**doc)
    return None


async def list_workflows() -> list[Workflow]:
    """List all workflows."""
    db = get_db()
    workflows = []
    cursor = db.workflows.find()
    async for doc in cursor:
        doc.pop("_id", None)
        workflows.append(Workflow(**doc))
    return workflows


async def update_workflow(workflow_id: str, data: WorkflowUpdate, user_id: str) -> Optional[Workflow]:
    """Update a workflow."""
    db = get_db()
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

    await db.workflows.update_one(
        {"workflow_id": workflow_id},
        {
            "$set": update_fields,
            "$push": {"history": history_entry.model_dump(mode="json")},
        }
    )
    return await get_workflow(workflow_id)


async def delete_workflow(workflow_id: str, user_id: str) -> bool:
    """Delete a workflow."""
    db = get_db()
    history_entry = HistoryEntry(
        user_id=user_id,
        action="delete",
    )
    await db.workflows.update_one(
        {"workflow_id": workflow_id},
        {"$push": {"history": history_entry.model_dump(mode="json")}}
    )
    result = await db.workflows.delete_one({"workflow_id": workflow_id})
    return result.deleted_count > 0


async def get_workflow_history(workflow_id: str) -> list[dict]:
    """Get change log for a workflow."""
    wf = await get_workflow(workflow_id)
    if not wf:
        return []
    return [h.model_dump(mode="json") for h in wf.history]
