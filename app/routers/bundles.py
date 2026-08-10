"""Admin endpoints for bundle authoring."""

from fastapi import APIRouter, Depends, HTTPException

from app.container import BundleRepo, PageRepo
from app.models.bundle import BundleEntriesUpdate
from app.routers.deps import require_admin
from app.models.user import User
from app.services.bundles import upsert_bundle, fetch_bundle_text

router = APIRouter(prefix="/bundles", tags=["bundles"])


def _bundle_dict(bundle) -> dict:
    return {
        "name": bundle.name,
        "entries": [{"page_id": e.page_id, "content_form": e.content_form.value} for e in bundle.entries],
        "created_by": bundle.created_by,
        "created_at": bundle.created_at.isoformat(),
        "updated_at": bundle.updated_at.isoformat(),
    }


@router.put("/{name}")
async def upsert_bundle_route(
    name: str,
    data: BundleEntriesUpdate,
    admin: User = Depends(require_admin),
    bundle_repo: BundleRepo = None,
    page_repo: PageRepo = None,
) -> dict:
    """Create a new bundle or replace an existing bundle's entries."""
    try:
        bundle = await upsert_bundle(name, data.entries, admin, bundle_repo, page_repo)
        return _bundle_dict(bundle)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("")
async def list_bundles(
    admin: User = Depends(require_admin),
    bundle_repo: BundleRepo = None,
) -> dict:
    """List all bundles."""
    bundles = await bundle_repo.list_all()
    return {
        "bundles": [
            {
                "name": b.name,
                "entry_count": len(b.entries),
                "updated_at": b.updated_at.isoformat(),
            }
            for b in bundles
        ]
    }


@router.get("/{name}")
async def get_bundle(
    name: str,
    admin: User = Depends(require_admin),
    bundle_repo: BundleRepo = None,
) -> dict:
    """Get a bundle's full detail."""
    bundle = await bundle_repo.get(name)
    if not bundle:
        raise HTTPException(status_code=404, detail="Bundle not found")
    return _bundle_dict(bundle)


@router.get("/{name}/preview")
async def preview_bundle(
    name: str,
    admin: User = Depends(require_admin),
    bundle_repo: BundleRepo = None,
    page_repo: PageRepo = None,
) -> dict:
    """Render a bundle against current page content, as an admin would see it."""
    try:
        bundle, rendered = await fetch_bundle_text(name, admin, bundle_repo, page_repo)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {
        "bundle_name": name,
        "rendered_text": rendered,
        "entries": [{"page_id": e.page_id, "content_form": e.content_form.value} for e in bundle.entries],
    }
