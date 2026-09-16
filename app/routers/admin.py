"""Admin-only endpoints for data that's intentionally invisible everywhere else.

Deleted pages are hard-removed from every live table/collection (see
PageRepository.archive()) so nothing about them is reachable through normal
browse/search/Q&A — these two endpoints exist purely so the data is still
retrievable by an admin if it's ever needed, without any dedicated UI.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.container import PageRepo
from app.models.user import User
from app.routers.deps import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/deleted-pages")
async def list_deleted_pages(
    admin: User = Depends(require_admin),
    page_repo: PageRepo = None,
):
    return {"deleted_pages": await page_repo.list_deleted_pages()}


@router.get("/deleted-pages/{page_id}")
async def get_deleted_page(
    page_id: str,
    admin: User = Depends(require_admin),
    page_repo: PageRepo = None,
):
    record = await page_repo.get_deleted_page(page_id)
    if not record:
        raise HTTPException(status_code=404, detail="Deleted page not found")
    return record
