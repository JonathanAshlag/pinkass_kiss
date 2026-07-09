"""Page CRUD service."""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db import get_db
from app.models.page import Page, PageCreate, PageUpdate, PageStatus, TrustTier, HistoryEntry
from app.models.user import User
from app.services.permissions import can_view_page, get_visible_page_ids


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def is_trust_stale(page: Page) -> bool:
    return (
        page.trust_tier == TrustTier.verified
        and page.verified_content_hash is not None
        and compute_content_hash(page.content) != page.verified_content_hash
    )

logger = logging.getLogger("pinkas.pages")


async def create_page(data: PageCreate, user: User) -> Page:
    """Create a new page (status=published if no workflow, else draft pending request)."""
    db = get_db()
    page = Page(
        title=data.title,
        parent_id=data.parent_id,
        content=data.content,
        references=data.references,
        next_approval_date=data.next_approval_date,
        status=PageStatus.published if not user.workflow_id else PageStatus.draft,
        created_by=user.user_id,
    )
    page.history.append(HistoryEntry(
        user_id=user.user_id,
        action="create",
        snapshot=data.content,
    ))
    await db.pages.insert_one(page.model_dump(mode="json"))
    return page


async def get_page(page_id: str) -> Optional[Page]:
    """Get a page by ID."""
    db = get_db()
    doc = await db.pages.find_one({"page_id": page_id})
    if doc:
        doc.pop("_id", None)
        return Page(**doc)
    return None


async def update_page(page_id: str, data: PageUpdate, user: User) -> Optional[Page]:
    """Update a page's fields."""
    db = get_db()
    page = await get_page(page_id)
    if not page:
        return None

    update_fields: dict = {}
    if data.title is not None:
        update_fields["title"] = data.title
    if data.parent_id is not None:
        update_fields["parent_id"] = data.parent_id
    if data.content is not None:
        update_fields["content"] = data.content
    if data.references is not None:
        update_fields["references"] = [r.model_dump(mode="json") for r in data.references]
    if data.next_approval_date is not None:
        update_fields["next_approval_date"] = data.next_approval_date.isoformat()

    update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()

    history_entry = HistoryEntry(
        user_id=user.user_id,
        action="edit",
        diff=str(update_fields),
    )

    await db.pages.update_one(
        {"page_id": page_id},
        {
            "$set": update_fields,
            "$push": {"history": history_entry.model_dump(mode="json")},
        }
    )
    return await get_page(page_id)


async def delete_page(page_id: str, user: User) -> bool:
    """Soft-delete a page."""
    db = get_db()
    history_entry = HistoryEntry(
        user_id=user.user_id,
        action="delete",
    )
    result = await db.pages.update_one(
        {"page_id": page_id},
        {
            "$set": {"status": PageStatus.deleted.value, "updated_at": datetime.now(timezone.utc).isoformat()},
            "$push": {"history": history_entry.model_dump(mode="json")},
        }
    )
    return result.modified_count > 0


async def search_pages(query: str, user: User) -> list[Page]:
    """Full-text search filtered by user permissions."""
    db = get_db()
    visible_ids = await get_visible_page_ids(user)
    cursor = db.pages.find(
        {
            "$text": {"$search": query},
            "status": PageStatus.published.value,
            "page_id": {"$in": list(visible_ids)},
        }
    )
    pages = []
    async for doc in cursor:
        doc.pop("_id", None)
        pages.append(Page(**doc))
    return pages


async def get_page_tree(user: User) -> list[dict]:
    """Get the page hierarchy visible to the user."""
    db = get_db()
    visible_ids = await get_visible_page_ids(user)
    cursor = db.pages.find(
        {"page_id": {"$in": list(visible_ids)}, "status": {"$ne": "deleted"}},
        {"page_id": 1, "title": 1, "parent_id": 1, "status": 1}
    )
    pages = []
    async for doc in cursor:
        doc.pop("_id", None)
        pages.append(doc)
    return pages


async def get_page_history(page_id: str) -> list[dict]:
    """Get change log for a page."""
    page = await get_page(page_id)
    if not page:
        return []
    return [h.model_dump(mode="json") for h in page.history]
