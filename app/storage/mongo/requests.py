from __future__ import annotations

from typing import Optional

from app.models.request import ApprovalRequest, RequestHistoryEntry, RequestStatus
from app.storage.base import RequestRepository


class MongoRequestRepository(RequestRepository):
    def __init__(self, db) -> None:
        self._db = db

    async def get(self, request_id: str) -> Optional[ApprovalRequest]:
        doc = await self._db.requests.find_one({"request_id": request_id})
        if doc:
            doc.pop("_id", None)
            return ApprovalRequest(**doc)
        return None

    async def create(self, req: ApprovalRequest) -> None:
        await self._db.requests.insert_one(req.model_dump(mode="json"))

    async def list_for_user(self, user_id: str) -> list[ApprovalRequest]:
        reqs = []
        async for doc in self._db.requests.find({"requested_by": user_id}):
            doc.pop("_id", None)
            reqs.append(ApprovalRequest(**doc))
        return reqs

    async def list_all(self) -> list[ApprovalRequest]:
        reqs = []
        async for doc in self._db.requests.find():
            doc.pop("_id", None)
            reqs.append(ApprovalRequest(**doc))
        return reqs

    async def update_fields(self, request_id: str, fields: dict) -> None:
        await self._db.requests.update_one({"request_id": request_id}, {"$set": fields})

    async def update_with_history(
        self, request_id: str, fields: dict, entry: RequestHistoryEntry
    ) -> None:
        await self._db.requests.update_one(
            {"request_id": request_id},
            {"$set": fields, "$push": {"history": entry.model_dump(mode="json")}},
        )

    async def get_pending_for_page(self, page_id: str, req_type: str) -> Optional[ApprovalRequest]:
        doc = await self._db.requests.find_one({
            "page_id": page_id,
            "type": req_type,
            "status": RequestStatus.pending.value,
        })
        if doc:
            doc.pop("_id", None)
            return ApprovalRequest(**doc)
        return None
