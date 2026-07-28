"""Approval request service."""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.page import HistoryEntry, PageStatus, TrustTier
from app.models.request import (
    ApprovalRequest, EditPayload, RequestHistoryEntry, RequestStatus,
    RequestType, ReviewPayload,
)
from app.models.user import User
from app.storage.base import PageRepository, RequestRepository, SourceFileRepository, WorkflowRepository
from app.services.pages import compute_content_hash
from app.services.source_files import cleanup_source_file_for_page

logger = logging.getLogger("pinkas.requests")


async def create_request(
    req_type: RequestType,
    page_id: str,
    user: User,
    req_repo: RequestRepository,
    page_repo: PageRepository,
    proposed_content=None,
) -> ApprovalRequest:
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
    await req_repo.create(req)

    if req_type in (RequestType.create, RequestType.review):
        await page_repo.update_fields(page_id, {"status": PageStatus.pending_approval.value})

    return req


async def get_request(request_id: str, repo: RequestRepository) -> Optional[ApprovalRequest]:
    return await repo.get(request_id)


async def get_user_requests(user_id: str, repo: RequestRepository) -> list[ApprovalRequest]:
    return await repo.list_for_user(user_id)


async def get_user_approvals(
    user_id: str,
    req_repo: RequestRepository,
    wf_repo: WorkflowRepository,
) -> list[ApprovalRequest]:
    all_reqs = await req_repo.list_all()
    results: list[ApprovalRequest] = []
    seen: set[str] = set()

    for req in all_reqs:
        wf = await wf_repo.get(req.workflow_id)
        if not wf:
            continue
        # Pending and this user is the current approver
        if req.status == RequestStatus.pending:
            if req.current_step < len(wf.steps) and wf.steps[req.current_step] == user_id:
                if req.request_id not in seen:
                    results.append(req)
                    seen.add(req.request_id)
        # Past decisions by this user
        for h in req.history:
            if h.user_id == user_id and h.decision in ("approve", "reject"):
                if req.request_id not in seen:
                    results.append(req)
                    seen.add(req.request_id)
                break

    return results


async def decide_request(
    request_id: str,
    user_id: str,
    decision: str,
    req_repo: RequestRepository,
    page_repo: PageRepository,
    wf_repo: WorkflowRepository,
    comment: Optional[str] = None,
    source_file_repo: Optional[SourceFileRepository] = None,
) -> Optional[ApprovalRequest]:
    req = await req_repo.get(request_id)
    if not req or req.status != RequestStatus.pending:
        return None

    wf = await wf_repo.get(req.workflow_id)
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
        await req_repo.update_with_history(
            request_id,
            {"status": RequestStatus.rejected.value},
            history_entry,
        )
        await page_repo.update_with_history(
            req.page_id,
            {"status": PageStatus.rejected.value},
            HistoryEntry(user_id=user_id, action="reject", comment=comment),
        )
    elif decision == "approve":
        next_step = req.current_step + 1
        if next_step >= len(wf.steps):
            await req_repo.update_with_history(
                request_id,
                {"status": RequestStatus.approved.value, "current_step": next_step},
                history_entry,
            )
            await _apply_approved_request(req, user_id, comment, page_repo, source_file_repo)
        else:
            await req_repo.update_with_history(
                request_id,
                {"current_step": next_step},
                history_entry,
            )

    return await req_repo.get(request_id)


async def _apply_approved_request(
    req: ApprovalRequest,
    approver_id: str,
    comment: Optional[str],
    page_repo: PageRepository,
    source_file_repo: Optional[SourceFileRepository] = None,
) -> None:
    page_history = HistoryEntry(user_id=approver_id, action="approve", comment=comment)

    if req.type in (RequestType.create, RequestType.review):
        update: dict = {"status": PageStatus.published.value, "updated_at": datetime.now(timezone.utc).isoformat()}
        if req.proposed_content:
            pc = req.proposed_content
            if hasattr(pc, "title") and pc.title:
                update["title"] = pc.title
            if hasattr(pc, "content") and pc.content:
                update["content"] = pc.content
            if req.type == RequestType.review and isinstance(pc, ReviewPayload) and pc.trust_tier == TrustTier.verified.value:
                page = await page_repo.get(req.page_id)
                final_content = update.get("content") or (page.content if page else "")
                update["trust_tier"] = TrustTier.verified.value
                update["verified_content_hash"] = compute_content_hash(final_content)
                update["verified_at"] = datetime.now(timezone.utc).isoformat()
                update["verified_by"] = approver_id
        await page_repo.update_with_history(req.page_id, update, page_history)

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
        await page_repo.update_with_history(req.page_id, update, page_history)

    elif req.type == RequestType.delete:
        await page_repo.update_with_history(
            req.page_id,
            {"status": PageStatus.deleted.value, "updated_at": datetime.now(timezone.utc).isoformat()},
            page_history,
        )
        if source_file_repo is not None:
            await cleanup_source_file_for_page(req.page_id, source_file_repo)
