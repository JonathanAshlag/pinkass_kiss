from __future__ import annotations

from typing import Optional

from app.models.page import HistoryEntry
from app.models.workflow import Workflow
from app.storage.base import WorkflowRepository


class MongoWorkflowRepository(WorkflowRepository):
    def __init__(self, db) -> None:
        self._db = db

    async def get(self, workflow_id: str) -> Optional[Workflow]:
        doc = await self._db.workflows.find_one({"workflow_id": workflow_id})
        if doc:
            doc.pop("_id", None)
            return Workflow(**doc)
        return None

    async def create(self, wf: Workflow) -> None:
        await self._db.workflows.insert_one(wf.model_dump(mode="json"))

    async def list_all(self) -> list[Workflow]:
        wfs = []
        async for doc in self._db.workflows.find():
            doc.pop("_id", None)
            wfs.append(Workflow(**doc))
        return wfs

    async def update_fields(self, workflow_id: str, fields: dict) -> None:
        await self._db.workflows.update_one({"workflow_id": workflow_id}, {"$set": fields})

    async def update_with_history(self, workflow_id: str, fields: dict, entry: HistoryEntry) -> None:
        await self._db.workflows.update_one(
            {"workflow_id": workflow_id},
            {"$set": fields, "$push": {"history": entry.model_dump(mode="json")}},
        )

    async def append_history(self, workflow_id: str, entry: HistoryEntry) -> None:
        await self._db.workflows.update_one(
            {"workflow_id": workflow_id},
            {"$push": {"history": entry.model_dump(mode="json")}},
        )

    async def delete_with_history(self, workflow_id: str, entry: HistoryEntry) -> bool:
        await self._db.workflows.update_one(
            {"workflow_id": workflow_id},
            {"$push": {"history": entry.model_dump(mode="json")}},
        )
        result = await self._db.workflows.delete_one({"workflow_id": workflow_id})
        return result.deleted_count > 0
