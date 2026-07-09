"""Page endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from app.models.page import Page, PageCreate, PageUpdate, PageStatus
from app.models.request import RequestType
from app.models.user import User
from app.routers.deps import get_current_user, require_editor
from app.services.pages import (
    create_page, get_page, update_page, delete_page,
    search_pages, get_page_tree, get_page_history, is_trust_stale,
)
from app.services.permissions import can_view_page
from app.services.requests import create_request

router = APIRouter(prefix="/pages", tags=["pages"])


@router.post("", response_model=dict)
async def create_page_endpoint(data: PageCreate, user: User = Depends(require_editor)):
    """Create a new page. Routes through workflow if user has one."""
    page = await create_page(data, user)

    if user.workflow_id:
        req = await create_request(
            req_type=RequestType.create,
            page_id=page.page_id,
            user=user,
            proposed_content={"title": data.title, "content": data.content},
        )
        return {"page": page.model_dump(mode="json"), "request_id": req.request_id, "status": "pending_approval"}

    return {"page": page.model_dump(mode="json"), "status": "published"}


@router.get("/tree")
async def get_tree(user: User = Depends(get_current_user)):
    """Get the page hierarchy visible to the user."""
    return await get_page_tree(user)


@router.get("/search")
async def search(query: str = Query(...), user: User = Depends(get_current_user)):
    """Full-text search over pages."""
    pages = await search_pages(query, user)
    return [p.model_dump(mode="json") for p in pages]


@router.get("/{page_id}")
async def get_page_endpoint(page_id: str, user: User = Depends(get_current_user)):
    """Get a page by ID (permission-checked)."""
    if not await can_view_page(user, page_id):
        raise HTTPException(status_code=403, detail="No permission to view this page")
    page = await get_page(page_id)
    if not page or page.status == PageStatus.deleted:
        raise HTTPException(status_code=404, detail="Page not found")
    result = page.model_dump(mode="json")
    result["trust_is_stale"] = is_trust_stale(page)
    return result


@router.get("/{page_id}/history")
async def get_history(page_id: str, user: User = Depends(get_current_user)):
    """Get the change log for a page."""
    if not await can_view_page(user, page_id):
        raise HTTPException(status_code=403, detail="No permission to view this page")
    return await get_page_history(page_id)


@router.put("/{page_id}", response_model=dict)
async def update_page_endpoint(page_id: str, data: PageUpdate, user: User = Depends(require_editor)):
    """Edit a page. Routes through workflow if user has one."""
    page = await get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    if user.workflow_id:
        proposed = data.model_dump(mode="json", exclude_none=True)
        req = await create_request(
            req_type=RequestType.edit,
            page_id=page_id,
            user=user,
            proposed_content=proposed,
        )
        return {"request_id": req.request_id, "status": "pending_approval"}

    updated = await update_page(page_id, data, user)
    return {"page": updated.model_dump(mode="json"), "status": "published"}


@router.delete("/{page_id}", response_model=dict)
async def delete_page_endpoint(page_id: str, user: User = Depends(require_editor)):
    """Delete a page. Routes through workflow if user has one."""
    page = await get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    if user.workflow_id:
        req = await create_request(
            req_type=RequestType.delete,
            page_id=page_id,
            user=user,
        )
        return {"request_id": req.request_id, "status": "pending_approval"}

    await delete_page(page_id, user)
    return {"status": "deleted"}
