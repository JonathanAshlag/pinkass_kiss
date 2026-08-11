"""Approval and request endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.container import PageRepo, RequestRepo, SourceFileRepo, WorkflowRepo
from app.models.request import Decision
from app.models.user import User
from app.routers.deps import get_current_user
from app.services.requests import (
    get_user_requests, get_user_approvals, decide_request,
)

router = APIRouter(tags=["approvals"])


async def _with_page_title(req, page_repo) -> dict:
    data = req.model_dump(mode="json")
    page = await page_repo.get(req.page_id)
    data["page_title"] = page.title if page else None
    return data


@router.get("/me/requests")
async def my_requests(
    user: User = Depends(get_current_user),
    req_repo: RequestRepo = None,
    page_repo: PageRepo = None,
):
    requests = await get_user_requests(user.user_id, req_repo)
    return [await _with_page_title(r, page_repo) for r in requests]


@router.get("/me/approvals")
async def my_approvals(
    user: User = Depends(get_current_user),
    req_repo: RequestRepo = None,
    wf_repo: WorkflowRepo = None,
    page_repo: PageRepo = None,
):
    requests = await get_user_approvals(user.user_id, req_repo, wf_repo)
    return [await _with_page_title(r, page_repo) for r in requests]


@router.post("/approvals/{request_id}/decide")
async def decide(
    request_id: str,
    data: Decision,
    user: User = Depends(get_current_user),
    req_repo: RequestRepo = None,
    page_repo: PageRepo = None,
    wf_repo: WorkflowRepo = None,
    source_file_repo: SourceFileRepo = None,
):
    if data.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Decision must be 'approve' or 'reject'")

    result = await decide_request(
        request_id, user.user_id, data.decision,
        req_repo=req_repo, page_repo=page_repo, wf_repo=wf_repo,
        comment=data.comment, source_file_repo=source_file_repo,
    )
    if not result:
        raise HTTPException(
            status_code=403,
            detail="Cannot decide: request not found, already decided, or not your turn",
        )
    return result.model_dump(mode="json")
