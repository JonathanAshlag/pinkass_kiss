"""Approval request service."""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.db import get_db
from app.models.page import HistoryEntry, PageStatus, TrustTier
from app.services.pages import compute_content_hash
from app.models.request import (
    ApprovalRequest, RequestStatus, RequestType, RequestHistoryEntry,
    EditPayload, ReviewPayload, PageMutationPayload,
)
from app.models.user import User
from app.services.workflows import get_workflow

logger = logging.getLogger("pinkas.requests")


async def create_request(
    req_type: RequestType,
    page_id: str,
    user: User,
    proposed_content: Optional[PageMutationPayload] = None,
) -> ApprovalRequest:
    """Create a new approval request routed through the user's workflow."""
    db = get_db()
    req = ApprovalRequest(
        type=req_type,
        page_id=page_id,
        requested_by=user.user_id,
        proposed_content=proposed_content,
        workflow_id=user.workflow_id,
        current_step=0,
        status=RequestStatus.pending,
    )
    req.history.append(RequestHistoryEntry(
        user_id=user.user_id,
        decision="submitted",
        comment=f"Request type: {req_type.value}",
    ))
    await db.requests.insert_one(req.model_dump(mode="json"))

    if req_type == RequestType.create or req_type == RequestType.review:
        await db.pages.update_one(
            {"page_id": page_id},
            {"$set": {"status": PageStatus.pending_approval.value}}
        )

    return req


async def get_request(request_id: str) -> Optional[ApprovalRequest]:
    """Get a request by ID."""
    db = get_db()
    doc = await db.requests.find_one({"request_id": request_id})
    if doc:
        doc.pop("_id", None)
        return ApprovalRequest(**doc)
    return None


async def get_user_requests(user_id: str) -> list[ApprovalRequest]:
    """Get all requests created by a user."""
    db = get_db()
    requests = []
    cursor = db.requests.find({"requested_by": user_id})
    async for doc in cursor:
        doc.pop("_id", None)
        requests.append(ApprovalRequest(**doc))
    return requests


async def get_user_approvals(user_id: str) -> list[ApprovalRequest]:
    """Get requests waiting for a user's decision + past decisions."""
    db = get_db()
    requests = []
    cursor = db.requests.find()
    async for doc in cursor:
        doc.pop("_id", None)
        req = ApprovalRequest(**doc)
        wf = await get_workflow(req.workflow_id)
        if not wf:
            continue
        # Pending and this user is the current approver
        if req.status == RequestStatus.pending:
            if req.current_step < len(wf.steps) and wf.steps[req.current_step] == user_id:
                requests.append(req)
        # Past decisions by this user
        for h in req.history:
            if h.user_id == user_id and h.decision in ("approve", "reject"):
                if req not in requests:
                    requests.append(req)
                break
    return requests


async def decide_request(
    request_id: str,
    user_id: str,
    decision: str,
    comment: Optional[str] = None,
) -> Optional[ApprovalRequest]:
    """Process an approval/rejection decision."""
    db = get_db()
    req = await get_request(request_id)
    if not req or req.status != RequestStatus.pending:
        return None

    wf = await get_workflow(req.workflow_id)
    if not wf:
        return None

    if req.current_step >= len(wf.steps) or wf.steps[req.current_step] != user_id:
        return None

    history_entry = RequestHistoryEntry(
        user_id=user_id,
        decision=decision,
        comment=comment,
    )

    if decision == "reject":
        await db.requests.update_one(
            {"request_id": request_id},
            {
                "$set": {"status": RequestStatus.rejected.value},
                "$push": {"history": history_entry.model_dump(mode="json")},
            }
        )
        await db.pages.update_one(
            {"page_id": req.page_id},
            {
                "$set": {"status": PageStatus.rejected.value},
                "$push": {"history": HistoryEntry(
                    user_id=user_id,
                    action="reject",
                    comment=comment,
                ).model_dump(mode="json")},
            }
        )
    elif decision == "approve":
        next_step = req.current_step + 1
        if next_step >= len(wf.steps):
            # Final approval
            await db.requests.update_one(
                {"request_id": request_id},
                {
                    "$set": {"status": RequestStatus.approved.value, "current_step": next_step},
                    "$push": {"history": history_entry.model_dump(mode="json")},
                }
            )
            await _apply_approved_request(req, user_id, comment)
        else:
            # Move to next step
            await db.requests.update_one(
                {"request_id": request_id},
                {
                    "$set": {"current_step": next_step},
                    "$push": {"history": history_entry.model_dump(mode="json")},
                }
            )

    return await get_request(request_id)


async def _apply_approved_request(req: ApprovalRequest, approver_id: str, comment: Optional[str]) -> None:
    """Apply changes after final approval."""
    db = get_db()
    page_history = HistoryEntry(
        user_id=approver_id,
        action="approve",
        comment=comment,
    )

    if req.type in (RequestType.create, RequestType.review):
        update: dict = {"status": PageStatus.published.value, "updated_at": datetime.now(timezone.utc).isoformat()}
        if req.proposed_content:
            pc = req.proposed_content
            if hasattr(pc, "title") and pc.title:
                update["title"] = pc.title
            if hasattr(pc, "content") and pc.content:
                update["content"] = pc.content
            if req.type == RequestType.review and isinstance(pc, ReviewPayload) and pc.trust_tier == TrustTier.verified.value:
                page_doc = await db.pages.find_one({"page_id": req.page_id})
                final_content = update.get("content") or (page_doc.get("content", "") if page_doc else "")
                update["trust_tier"] = TrustTier.verified.value
                update["verified_content_hash"] = compute_content_hash(final_content)
                update["verified_at"] = datetime.now(timezone.utc).isoformat()
                update["verified_by"] = approver_id
        await db.pages.update_one(
            {"page_id": req.page_id},
            {
                "$set": update,
                "$push": {"history": page_history.model_dump(mode="json")},
            }
        )
    elif req.type == RequestType.edit:
        update = {"status": PageStatus.published.value, "updated_at": datetime.now(timezone.utc).isoformat()}
        if req.proposed_content and isinstance(req.proposed_content, EditPayload):
            ep = req.proposed_content
            for key in ("title", "description", "content", "parent_id", "next_approval_date"):
                val = getattr(ep, key, None)
                if val is not None:
                    update[key] = val
            if ep.references is not None:
                update["references"] = ep.references
        await db.pages.update_one(
            {"page_id": req.page_id},
            {
                "$set": update,
                "$push": {"history": page_history.model_dump(mode="json")},
            }
        )
    elif req.type == RequestType.delete:
        await db.pages.update_one(
            {"page_id": req.page_id},
            {
                "$set": {"status": PageStatus.deleted.value, "updated_at": datetime.now(timezone.utc).isoformat()},
                "$push": {"history": page_history.model_dump(mode="json")},
            }
        )
