"""PostgreSQL implementation of RequestRepository."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request import (
    ApprovalRequest, PageMutationPayload, RequestHistoryEntry, RequestStatus, RequestType,
)
from app.storage.base import RequestRepository
from app.infrastructure.postgres.models import RequestORM


def _orm_to_request(row: RequestORM) -> ApprovalRequest:
    history = [RequestHistoryEntry(**h) for h in (row.history or [])]
    return ApprovalRequest(
        request_id=row.request_id,
        type=RequestType(row.type),
        page_id=row.page_id,
        requested_by=row.requested_by,
        proposed_content=row.proposed_content,
        workflow_id=row.workflow_id,
        current_step=row.current_step,
        status=RequestStatus(row.status),
        history=history,
        created_at=row.created_at,
    )


class PostgresRequestRepository(RequestRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, request_id: str) -> Optional[ApprovalRequest]:
        result = await self._s.execute(
            select(RequestORM).where(RequestORM.request_id == request_id)
        )
        row = result.scalar_one_or_none()
        return _orm_to_request(row) if row else None

    async def create(self, req: ApprovalRequest) -> None:
        proposed = None
        if req.proposed_content is not None:
            proposed = req.proposed_content.model_dump(mode="json")
        self._s.add(RequestORM(
            request_id=req.request_id,
            type=req.type.value,
            page_id=req.page_id,
            requested_by=req.requested_by,
            proposed_content=proposed,
            workflow_id=req.workflow_id,
            current_step=req.current_step,
            status=req.status.value,
            history=[h.model_dump(mode="json") for h in req.history],
            created_at=req.created_at,
        ))
        await self._s.flush()

    async def list_for_user(self, user_id: str) -> list[ApprovalRequest]:
        result = await self._s.execute(
            select(RequestORM).where(RequestORM.requested_by == user_id)
        )
        return [_orm_to_request(row) for row in result.scalars().all()]

    async def list_all(self) -> list[ApprovalRequest]:
        result = await self._s.execute(select(RequestORM))
        return [_orm_to_request(row) for row in result.scalars().all()]

    async def update_fields(self, request_id: str, fields: dict) -> None:
        if not fields:
            return
        await self._s.execute(
            update(RequestORM).where(RequestORM.request_id == request_id).values(**fields)
        )
        await self._s.flush()

    async def update_with_history(
        self, request_id: str, fields: dict, entry: RequestHistoryEntry
    ) -> None:
        row = await self._s.get(RequestORM, request_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        row.history = list(row.history or []) + [entry.model_dump(mode="json")]
        await self._s.flush()

    async def get_pending_for_page(self, page_id: str, req_type: str) -> Optional[ApprovalRequest]:
        result = await self._s.execute(
            select(RequestORM).where(
                RequestORM.page_id == page_id,
                RequestORM.type == req_type,
                RequestORM.status == RequestStatus.pending.value,
            )
        )
        row = result.scalar_one_or_none()
        return _orm_to_request(row) if row else None
