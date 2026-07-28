"""PostgreSQL implementation of WorkflowRepository."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import HistoryEntry
from app.models.workflow import Workflow
from app.storage.base import WorkflowRepository
from app.infrastructure.postgres.models import WorkflowORM


def _orm_to_workflow(row: WorkflowORM) -> Workflow:
    from app.models.page import HistoryEntry as HE
    history = [HE(**h) for h in (row.history or [])]
    return Workflow(
        workflow_id=row.workflow_id,
        name=row.name,
        description=row.description,
        steps=list(row.steps or []),
        history=history,
    )


class PostgresWorkflowRepository(WorkflowRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, workflow_id: str) -> Optional[Workflow]:
        result = await self._s.execute(
            select(WorkflowORM).where(WorkflowORM.workflow_id == workflow_id)
        )
        row = result.scalar_one_or_none()
        return _orm_to_workflow(row) if row else None

    async def create(self, wf: Workflow) -> None:
        self._s.add(WorkflowORM(
            workflow_id=wf.workflow_id,
            name=wf.name,
            description=wf.description,
            steps=list(wf.steps),
            history=[h.model_dump(mode="json") for h in wf.history],
        ))
        await self._s.flush()

    async def list_all(self) -> list[Workflow]:
        result = await self._s.execute(select(WorkflowORM))
        return [_orm_to_workflow(row) for row in result.scalars().all()]

    async def update_fields(self, workflow_id: str, fields: dict) -> None:
        if not fields:
            return
        await self._s.execute(
            update(WorkflowORM).where(WorkflowORM.workflow_id == workflow_id).values(**fields)
        )
        await self._s.flush()

    async def update_with_history(self, workflow_id: str, fields: dict, entry: HistoryEntry) -> None:
        row = await self._s.get(WorkflowORM, workflow_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        row.history = list(row.history or []) + [entry.model_dump(mode="json")]
        await self._s.flush()

    async def append_history(self, workflow_id: str, entry: HistoryEntry) -> None:
        row = await self._s.get(WorkflowORM, workflow_id)
        if row is None:
            return
        row.history = list(row.history or []) + [entry.model_dump(mode="json")]
        await self._s.flush()

    async def delete_with_history(self, workflow_id: str, entry: HistoryEntry) -> bool:
        await self.append_history(workflow_id, entry)
        result = await self._s.execute(
            delete(WorkflowORM).where(WorkflowORM.workflow_id == workflow_id)
        )
        await self._s.flush()
        return result.rowcount > 0
