"""Page endpoints."""

import time

from fastapi import APIRouter, Depends, HTTPException, Query

from app.container import PageRepo, RequestRepo, SourceFileRepo
from app.IP.tags import ALLOWED_TAGS
from app.models.audit import AuditAction, AuditLogEntry, AuditOutcome, UserContext
from app.models.page import Page, PageCreate, PageUpdate, PageStatus
from app.models.request import RequestType
from app.models.user import User
from app.routers.deps import get_current_user, require_editor, get_user_context
from app.services.event_log import emit_audit_log
from app.services.mutations import apply_page_mutation, MutationResult
from app.services.pages import (
    get_page, search_pages, fuzzy_search_pages, get_page_tree, get_page_history, is_trust_stale,
)
from app.services.permissions import can_view_page
from app.services.source_files import cleanup_source_file_for_page

router = APIRouter(prefix="/pages", tags=["pages"])


def _mutation_outcome(status: str) -> AuditOutcome:
    if status == "pending_approval":
        return AuditOutcome.pending_approval
    return AuditOutcome.success


@router.post("", response_model=MutationResult)
async def create_page_endpoint(
    data: PageCreate,
    user: User = Depends(require_editor),
    user_context: UserContext = Depends(get_user_context),
    page_repo: PageRepo = None,
    req_repo: RequestRepo = None,
):
    t0 = time.perf_counter()
    try:
        result = await apply_page_mutation(RequestType.create, user, page_repo, req_repo, data=data)
    except ValueError as e:
        emit_audit_log(AuditLogEntry(
            action=AuditAction.create_page,
            user_context=user_context,
            resource_id=data.title,
            outcome=AuditOutcome.denied,
            latency_ms=(time.perf_counter() - t0) * 1000,
            request_path="/pages",
        ))
        raise HTTPException(status_code=409, detail=str(e))
    emit_audit_log(AuditLogEntry(
        action=AuditAction.create_page,
        user_context=user_context,
        resource_id=result.page.get("page_id") if result.page else None,
        outcome=_mutation_outcome(result.status),
        result=result.model_dump(mode="json"),
        latency_ms=(time.perf_counter() - t0) * 1000,
        request_path="/pages",
    ))
    return result


@router.get("/tree")
async def get_tree(user: User = Depends(get_current_user), page_repo: PageRepo = None):
    return await get_page_tree(user, page_repo)


@router.get("/search")
async def search(
    query: str = Query(...),
    tags: list[str] | None = Query(None),
    user: User = Depends(get_current_user),
    page_repo: PageRepo = None,
):
    pages = await fuzzy_search_pages(query, user, page_repo, tags=tags)
    return [p.model_dump(mode="json") for p in pages]


@router.get("/tags")
async def list_allowed_tags(user: User = Depends(get_current_user)):
    return {"tags": ALLOWED_TAGS}


@router.get("/{page_id}")
async def get_page_endpoint(
    page_id: str,
    user: User = Depends(get_current_user),
    page_repo: PageRepo = None,
):
    if not await can_view_page(user, page_id, page_repo):
        raise HTTPException(status_code=403, detail="No permission to view this page")
    page = await get_page(page_id, page_repo)
    if not page or page.status == PageStatus.deleted:
        raise HTTPException(status_code=404, detail="Page not found")
    result = page.model_dump(mode="json")
    result["trust_is_stale"] = is_trust_stale(page)
    return result


@router.get("/{page_id}/history")
async def get_history(
    page_id: str,
    user: User = Depends(get_current_user),
    page_repo: PageRepo = None,
):
    if not await can_view_page(user, page_id, page_repo):
        raise HTTPException(status_code=403, detail="No permission to view this page")
    return await get_page_history(page_id, page_repo)


@router.put("/{page_id}", response_model=MutationResult)
async def update_page_endpoint(
    page_id: str,
    data: PageUpdate,
    user: User = Depends(require_editor),
    user_context: UserContext = Depends(get_user_context),
    page_repo: PageRepo = None,
    req_repo: RequestRepo = None,
):
    t0 = time.perf_counter()
    if not await can_view_page(user, page_id, page_repo):
        emit_audit_log(AuditLogEntry(
            action=AuditAction.edit_page,
            user_context=user_context,
            resource_id=page_id,
            outcome=AuditOutcome.denied,
            latency_ms=(time.perf_counter() - t0) * 1000,
            request_path=f"/pages/{page_id}",
            ))
        raise HTTPException(status_code=403, detail="No permission to view this page")
    page = await get_page(page_id, page_repo)
    if not page:
        emit_audit_log(AuditLogEntry(
            action=AuditAction.edit_page,
            user_context=user_context,
            resource_id=page_id,
            outcome=AuditOutcome.not_found,
            latency_ms=(time.perf_counter() - t0) * 1000,
            request_path=f"/pages/{page_id}",
            ))
        raise HTTPException(status_code=404, detail="Page not found")
    result = await apply_page_mutation(RequestType.edit, user, page_repo, req_repo, data=data, page_id=page_id)
    emit_audit_log(AuditLogEntry(
        action=AuditAction.edit_page,
        user_context=user_context,
        resource_id=page_id,
        outcome=_mutation_outcome(result.status),
        result=result.model_dump(mode="json"),
        latency_ms=(time.perf_counter() - t0) * 1000,
        request_path=f"/pages/{page_id}",
    ))
    return result


@router.delete("/{page_id}", response_model=MutationResult)
async def delete_page_endpoint(
    page_id: str,
    user: User = Depends(require_editor),
    user_context: UserContext = Depends(get_user_context),
    page_repo: PageRepo = None,
    req_repo: RequestRepo = None,
    source_file_repo: SourceFileRepo = None,
):
    t0 = time.perf_counter()
    if not await can_view_page(user, page_id, page_repo):
        emit_audit_log(AuditLogEntry(
            action=AuditAction.delete_page,
            user_context=user_context,
            resource_id=page_id,
            outcome=AuditOutcome.denied,
            latency_ms=(time.perf_counter() - t0) * 1000,
            request_path=f"/pages/{page_id}",
            ))
        raise HTTPException(status_code=403, detail="No permission to view this page")
    page = await get_page(page_id, page_repo)
    if not page or page.status == PageStatus.deleted:
        emit_audit_log(AuditLogEntry(
            action=AuditAction.delete_page,
            user_context=user_context,
            resource_id=page_id,
            outcome=AuditOutcome.not_found,
            latency_ms=(time.perf_counter() - t0) * 1000,
            request_path=f"/pages/{page_id}",
            ))
        raise HTTPException(status_code=404, detail="Page not found")
    result = await apply_page_mutation(RequestType.delete, user, page_repo, req_repo, page_id=page_id)
    if result.status == "deleted":
        await cleanup_source_file_for_page(page_id, source_file_repo)
    emit_audit_log(AuditLogEntry(
        action=AuditAction.delete_page,
        user_context=user_context,
        resource_id=page_id,
        outcome=_mutation_outcome(result.status),
        result=result.model_dump(mode="json"),
        latency_ms=(time.perf_counter() - t0) * 1000,
        request_path=f"/pages/{page_id}",
    ))
    return result
