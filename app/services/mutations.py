"""Single seam for page mutations — owns the workflow-routing decision."""

from datetime import datetime, timezone

from app.db import get_db
from app.models.page import PageCreate, PageUpdate, TrustTier
from app.models.request import RequestType, CreatePayload, EditPayload, DeletePayload
from app.models.user import User
from app.services.pages import create_page, update_page, delete_page, compute_content_hash, set_page_references
from app.services.requests import create_request


async def apply_page_mutation(
    req_type: RequestType,
    user: User,
    data: PageCreate | PageUpdate | None = None,
    page_id: str | None = None,
    trust_tier: TrustTier | None = None,
) -> dict:
    """Apply a page mutation, routing through workflow if the user has one.

    Returns a dict with at minimum {"status": ...}. When a page is
    created/updated directly, includes {"page": ...}. When routed through
    a workflow, includes {"request_id": ...}.

    trust_tier: when TrustTier.verified and result is "published", promotes the
    page trust tier inline. Only meaningful for create mutations.
    """
    if req_type == RequestType.create:
        assert isinstance(data, PageCreate), "data must be PageCreate for create mutations"
        page = await create_page(data, user)
        if user.workflow_id:
            payload = CreatePayload(
                title=data.title,
                description=getattr(data, "description", ""),
                content=data.content,
                classification=[c.model_dump(mode="json") for c in data.classification] if data.classification else None,
            )
            req = await create_request(
                req_type=RequestType.create,
                page_id=page.page_id,
                user=user,
                proposed_content=payload,
            )
            return {"page": page.model_dump(mode="json"), "request_id": req.request_id, "status": "pending_approval"}
        result: dict = {"page": page.model_dump(mode="json"), "status": "published"}
        if trust_tier == TrustTier.verified:
            db = get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.pages.update_one(
                {"page_id": page.page_id},
                {"$set": {
                    "trust_tier": TrustTier.verified.value,
                    "verified_content_hash": compute_content_hash(page.content),
                    "verified_at": now,
                    "verified_by": user.user_id,
                }},
            )
            result["page"]["trust_tier"] = TrustTier.verified.value
        return result

    elif req_type == RequestType.edit:
        assert isinstance(data, PageUpdate), "data must be PageUpdate for edit mutations"
        assert page_id is not None, "page_id required for edit mutations"
        if user.workflow_id:
            payload = EditPayload(
                title=data.title,
                description=getattr(data, "description", None),
                content=data.content,
                parent_id=data.parent_id,
                references=[r.model_dump(mode="json") for r in data.references] if data.references else None,
                classification=[c.model_dump(mode="json") for c in data.classification] if data.classification else None,
                next_approval_date=data.next_approval_date.isoformat() if data.next_approval_date else None,
            )
            req = await create_request(
                req_type=RequestType.edit,
                page_id=page_id,
                user=user,
                proposed_content=payload,
            )
            # Persist references immediately regardless of workflow routing (provenance).
            if data.references is not None:
                await set_page_references(page_id, data.references)
            return {"request_id": req.request_id, "status": "pending_approval"}
        updated = await update_page(page_id, data, user)
        return {"page": updated.model_dump(mode="json"), "status": "published"}

    elif req_type == RequestType.delete:
        assert page_id is not None, "page_id required for delete mutations"
        if user.workflow_id:
            req = await create_request(
                req_type=RequestType.delete,
                page_id=page_id,
                user=user,
                proposed_content=DeletePayload(),
            )
            return {"request_id": req.request_id, "status": "pending_approval"}
        await delete_page(page_id, user)
        return {"status": "deleted"}

    raise ValueError(f"Unsupported req_type for apply_page_mutation: {req_type}")
