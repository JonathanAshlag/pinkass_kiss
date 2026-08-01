"""Single seam for page mutations — owns the workflow-routing decision."""

from datetime import datetime, timezone
from typing import Literal, Optional, Union

from pydantic import BaseModel

from app.models.page import PageCreate, PageUpdate, TrustTier
from app.models.request import RequestType, CreatePayload, EditPayload, DeletePayload
from app.models.user import User
from app.storage.base import PageRepository, RequestRepository
from app.services.pages import create_page, update_page, delete_page, compute_content_hash, set_page_references
from app.services.requests import create_request


class PublishedResult(BaseModel):
    """Mutation applied directly — page is live."""
    status: Literal["published"] = "published"
    page: dict


class PendingResult(BaseModel):
    """Mutation queued for workflow approval."""
    status: Literal["pending_approval"] = "pending_approval"
    request_id: str
    page: Optional[dict] = None  # present for create, absent for edit/delete


class DeletedResult(BaseModel):
    """Page deleted directly (no workflow)."""
    status: Literal["deleted"] = "deleted"


MutationResult = Union[PublishedResult, PendingResult, DeletedResult]


async def apply_page_mutation(
    req_type: RequestType,
    user: User,
    page_repo: PageRepository,
    req_repo: RequestRepository,
    data: PageCreate | PageUpdate | None = None,
    page_id: str | None = None,
    trust_tier: TrustTier | None = None,
) -> MutationResult:
    """Apply a page mutation, routing through workflow if the user has one."""
    if req_type == RequestType.create:
        assert isinstance(data, PageCreate), "data must be PageCreate for create mutations"
        page = await create_page(data, user, page_repo)
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
                req_repo=req_repo,
                page_repo=page_repo,
                proposed_content=payload,
            )
            return PendingResult(request_id=req.request_id, page=page.model_dump(mode="json"))
        result = PublishedResult(page=page.model_dump(mode="json"))
        if trust_tier == TrustTier.verified:
            now = datetime.now(timezone.utc).isoformat()
            await page_repo.update_fields(
                page.page_id,
                {
                    "trust_tier": TrustTier.verified.value,
                    "verified_content_hash": compute_content_hash(page.content),
                    "verified_at": now,
                    "verified_by": user.user_id,
                },
            )
            result.page["trust_tier"] = TrustTier.verified.value
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
                req_repo=req_repo,
                page_repo=page_repo,
                proposed_content=payload,
            )
            if data.references is not None:
                await set_page_references(page_id, data.references, page_repo)
            return PendingResult(request_id=req.request_id)
        updated = await update_page(page_id, data, user, page_repo)
        return PublishedResult(page=updated.model_dump(mode="json"))

    elif req_type == RequestType.delete:
        assert page_id is not None, "page_id required for delete mutations"
        if user.workflow_id:
            req = await create_request(
                req_type=RequestType.delete,
                page_id=page_id,
                user=user,
                req_repo=req_repo,
                page_repo=page_repo,
                proposed_content=DeletePayload(),
            )
            return PendingResult(request_id=req.request_id)
        await delete_page(page_id, user, page_repo)
        return DeletedResult()

    raise ValueError(f"Unsupported req_type for apply_page_mutation: {req_type}")
