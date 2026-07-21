"""Page CRUD service."""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db import get_db
from app.models.page import Page, PageCreate, PageUpdate, PageStatus, TrustTier, HistoryEntry, Reference, ClassificationTriangle
from app.models.user import User
from app.services.classification import get_user_triangles, user_satisfies_classification


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
        description=data.description,
        parent_id=data.parent_id,
        content=data.content,
        references=data.references,
        classification=data.classification,
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
    if data.description is not None:
        update_fields["description"] = data.description
    if data.parent_id is not None:
        update_fields["parent_id"] = data.parent_id
    if data.content is not None:
        update_fields["content"] = data.content
    if data.references is not None:
        update_fields["references"] = [r.model_dump(mode="json") for r in data.references]
    if data.classification is not None:
        update_fields["classification"] = [c.model_dump(mode="json") for c in data.classification]
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


_TIER_RANK = {"verified": 3, "source_checked": 2, "unverified": 1}


async def _find_raw_docs(
    query: str,
    user: User,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
    projection: Optional[dict] = None,
) -> list[dict]:
    """Internal search helper. Callers use find_pages or find_page_docs."""
    db = get_db()
    mongo_filter: dict = {}
    if statuses is not None:
        mongo_filter["status"] = {"$in": [s.value for s in statuses]}
    else:
        mongo_filter["status"] = PageStatus.published.value

    # Ensure classification field is fetched for filtering even if not in caller's projection
    effective_projection = projection
    strip_classification = False
    if projection is not None and "classification" not in projection:
        effective_projection = {**projection, "classification": 1}
        strip_classification = True

    results = []
    try:
        text_filter = {"$text": {"$search": query}, **mongo_filter}
        cursor = (
            db.pages.find(text_filter, effective_projection).limit(limit)
            if effective_projection
            else db.pages.find(text_filter).limit(limit)
        )
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
    except Exception:
        cursor = (
            db.pages.find(mongo_filter, effective_projection).limit(limit)
            if effective_projection
            else db.pages.find(mongo_filter).limit(limit)
        )
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)

    # Post-filter by classification triangles
    user_triangles = await get_user_triangles(user.user_id)
    results = [
        doc for doc in results
        if user_satisfies_classification(
            user_triangles,
            [ClassificationTriangle(**t) for t in doc.get("classification", [])],
        )
    ]
    if strip_classification:
        for doc in results:
            doc.pop("classification", None)

    if ranked:
        results.sort(
            key=lambda d: (_TIER_RANK.get(d.get("trust_tier", "unverified"), 0), d.get("inbound_link_count", 0)),
            reverse=True,
        )
    return results


async def find_pages(
    query: str,
    user: User,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
) -> list[Page]:
    """Full-text page search with permission and classification filtering."""
    docs = await _find_raw_docs(query, user=user, ranked=ranked, statuses=statuses, limit=limit)
    return [Page(**doc) for doc in docs]


async def find_page_docs(
    query: str,
    projection: dict,
    user: User,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
) -> list[dict]:
    """Full-text page search returning projected dicts."""
    return await _find_raw_docs(query, user=user, ranked=ranked, statuses=statuses, limit=limit, projection=projection)


async def set_page_references(page_id: str, references: list[Reference]) -> None:
    """Update a page's reference list directly (provenance, no workflow routing)."""
    db = get_db()
    await db.pages.update_one(
        {"page_id": page_id},
        {"$set": {"references": [r.model_dump(mode="json") for r in references]}},
    )


async def search_pages(query: str, user: User) -> list[Page]:
    """Full-text search filtered by user permissions."""
    return await find_pages(query, user=user)


async def get_page_tree(user: User) -> list[dict]:
    """Get the page hierarchy visible to the user."""
    db = get_db()
    cursor = db.pages.find(
        {"status": {"$ne": "deleted"}},
        {"page_id": 1, "title": 1, "parent_id": 1, "status": 1, "classification": 1}
    )
    user_triangles = await get_user_triangles(user.user_id)
    pages = []
    async for doc in cursor:
        doc.pop("_id", None)
        page_classification = [ClassificationTriangle(**t) for t in doc.pop("classification", [])]
        if not user_satisfies_classification(user_triangles, page_classification):
            continue
        pages.append(doc)
    return pages


async def get_page_history(page_id: str) -> list[dict]:
    """Get change log for a page."""
    page = await get_page(page_id)
    if not page:
        return []
    return [h.model_dump(mode="json") for h in page.history]
