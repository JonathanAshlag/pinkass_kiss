"""Page endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.container import PageRepo, RequestRepo, SourceFileRepo
from app.models.page import Page, PageCreate, PageUpdate, PageStatus
from app.models.request import RequestType
from app.models.user import User
from app.routers.deps import get_current_user, require_editor
from app.services.mutations import apply_page_mutation
from app.services.pages import (
    get_page, search_pages, get_page_tree, get_page_history, is_trust_stale,
)
from app.services.permissions import can_view_page
from app.services.source_files import cleanup_source_file_for_page

router = APIRouter(prefix="/pages", tags=["pages"])


@router.post("", response_model=dict)
async def create_page_endpoint(
    data: PageCreate,
    user: User = Depends(require_editor),
    page_repo: PageRepo = None,
    req_repo: RequestRepo = None,
):
    return await apply_page_mutation(RequestType.create, user, page_repo, req_repo, data=data)


@router.get("/tree")
async def get_tree(user: User = Depends(get_current_user), page_repo: PageRepo = None):
    return await get_page_tree(user, page_repo)


@router.get("/search")
async def search(
    query: str = Query(...),
    user: User = Depends(get_current_user),
    page_repo: PageRepo = None,
):
    pages = await search_pages(query, user, page_repo)
    return [p.model_dump(mode="json") for p in pages]


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


@router.put("/{page_id}", response_model=dict)
async def update_page_endpoint(
    page_id: str,
    data: PageUpdate,
    user: User = Depends(require_editor),
    page_repo: PageRepo = None,
    req_repo: RequestRepo = None,
):
    if not await can_view_page(user, page_id, page_repo):
        raise HTTPException(status_code=403, detail="No permission to view this page")
    page = await get_page(page_id, page_repo)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return await apply_page_mutation(RequestType.edit, user, page_repo, req_repo, data=data, page_id=page_id)


@router.delete("/{page_id}", response_model=dict)
async def delete_page_endpoint(
    page_id: str,
    user: User = Depends(require_editor),
    page_repo: PageRepo = None,
    req_repo: RequestRepo = None,
    source_file_repo: SourceFileRepo = None,
):
    if not await can_view_page(user, page_id, page_repo):
        raise HTTPException(status_code=403, detail="No permission to view this page")
    page = await get_page(page_id, page_repo)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    result = await apply_page_mutation(RequestType.delete, user, page_repo, req_repo, page_id=page_id)
    if result.get("status") == "deleted":
        await cleanup_source_file_for_page(page_id, source_file_repo)
    return result
