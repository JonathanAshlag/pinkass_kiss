"""Approval request service."""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.page import HistoryEntry, Page, PageStatus, TrustTier
from app.models.request import (
    ApprovalRequest, EditPayload, RequestHistoryEntry, RequestStatus,
    RequestType, ReviewPayload,
)
from app.models.user import PermissionLevel, User
from app.models.workflow import WorkflowCreate
from app.storage.base import (
    PageRepository, RequestRepository, SourceFileRepository, UserRepository, WorkflowRepository,
)
from app.services.pages import compute_content_hash, _merge_parent_tags
from app.services.source_files import cleanup_source_file_for_page
from app.services.users import assign_workflow
from app.services.workflows import create_workflow

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


async def ensure_workflow_for_user(
    user: User,
    wf_repo: WorkflowRepository,
    user_repo: UserRepository,
) -> None:
    """Give the user a workflow to route requests through, or raise if they can't have one auto-created."""
    if user.workflow_id:
        return
    if user.permission_level != PermissionLevel.admin:
        raise PermissionError("No workflow configured for your user — ask an admin to assign one")
    wf = await create_workflow(
        WorkflowCreate(name=f"Self-approval — {user.name}", steps=[user.user_id]),
        user.user_id,
        wf_repo,
    )
    await assign_workflow(user.user_id, wf.workflow_id, user_repo)
    user.workflow_id = wf.workflow_id


async def request_page_verification(
    page: Page,
    user: User,
    page_repo: PageRepository,
    req_repo: RequestRepository,
    wf_repo: WorkflowRepository,
    user_repo: UserRepository,
) -> ApprovalRequest:
    existing = await req_repo.get_pending_for_page(page.page_id, RequestType.review.value)
    if existing:
        raise ValueError("A verification request is already pending for this page")

    await ensure_workflow_for_user(user, wf_repo, user_repo)

    return await create_request(
        req_type=RequestType.review,
        page_id=page.page_id,
        user=user,
        req_repo=req_repo,
        page_repo=page_repo,
        proposed_content=ReviewPayload(trust_tier=TrustTier.verified.value),
    )


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
            if hasattr(pc, "aliases") and pc.aliases is not None:
                update["aliases"] = pc.aliases
            # tags are intentionally not touched here: create_page() already wrote the
            # correctly-merged tags when the draft was created, and re-merging against
            # the parent's *current* tags at approval time would leak a parent-tag edit
            # made while the request was pending — the forward-only inheritance rule
            # only applies at the moment the child itself is written.
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
        page = await page_repo.get(req.page_id)
        if req.proposed_content and isinstance(req.proposed_content, EditPayload):
            ep = req.proposed_content
            for key in ("title", "description", "content", "parent_id", "next_approval_date"):
                val = getattr(ep, key, None)
                if val is not None:
                    update[key] = val
            if ep.references is not None:
                update["references"] = ep.references
            if ep.aliases is not None:
                update["aliases"] = ep.aliases
            if ep.tags is not None or ep.parent_id is not None:
                base_tags = ep.tags if ep.tags is not None else (page.tags if page else [])
                new_parent_id = update.get("parent_id", page.parent_id if page else None)
                update["tags"] = await _merge_parent_tags(base_tags, new_parent_id, page_repo)
        if page and page.trust_tier == TrustTier.verified:
            final_content = update.get("content") or page.content
            update["verified_content_hash"] = compute_content_hash(final_content)
            update["verified_at"] = datetime.now(timezone.utc).isoformat()
            update["verified_by"] = approver_id
        await page_repo.update_with_history(req.page_id, update, page_history)

    elif req.type == RequestType.delete:
        await page_repo.append_history(req.page_id, page_history)
        await page_repo.delete(req.page_id)
        if source_file_repo is not None:
            await cleanup_source_file_for_page(req.page_id, source_file_repo)
