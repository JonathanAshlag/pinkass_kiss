"""Page CRUD service."""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.page import (
    ClassificationTriangle, HistoryEntry, Page, PageCreate, PageStatus,
    PageUpdate, Reference, TrustTier,
)
from app.models.user import User
from app.storage.base import PageRepository
from app.services.classification import get_user_triangles, user_satisfies_classification

logger = logging.getLogger("pinkas.pages")


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def is_trust_stale(page: Page) -> bool:
    return (
        page.trust_tier == TrustTier.verified
        and page.verified_content_hash is not None
        and compute_content_hash(page.content) != page.verified_content_hash
    )


async def _merge_parent_tags(tags: list[str], parent_id: Optional[str], repo: PageRepository) -> list[str]:
    """Union a page's tags with its parent's current tags (forward-only, no retroactive cascade)."""
    if not parent_id:
        return tags
    parent = await repo.get(parent_id)
    if parent is None:
        return tags
    return sorted(set(tags) | set(parent.tags))


async def create_page(data: PageCreate, user: User, repo: PageRepository) -> Page:
    if await repo.get(data.title) is not None:
        raise ValueError(f"A page titled '{data.title}' already exists")
    merged_tags = await _merge_parent_tags(data.tags, data.parent_id, repo)
    page = Page(
        page_id=data.title,
        title=data.title,
        description=data.description,
        parent_id=data.parent_id,
        content=data.content,
        references=data.references,
        aliases=data.aliases,
        tags=merged_tags,
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
    await repo.create(page)
    return page


async def get_page(page_id: str, repo: PageRepository) -> Optional[Page]:
    return await repo.get(page_id)


async def update_page(
    page_id: str,
    data: PageUpdate,
    user: User,
    repo: PageRepository,
    trust_tier: Optional[TrustTier] = None,
) -> Optional[Page]:
    page = await repo.get(page_id)
    if not page:
        return None

    update_fields: dict = {}
    if data.title is not None:
        update_fields["title"] = data.title
    if data.description is not None:
        update_fields["description"] = data.description
    if "parent_id" in data.model_fields_set:
        update_fields["parent_id"] = data.parent_id
    if data.content is not None:
        update_fields["content"] = data.content
    if data.references is not None:
        update_fields["references"] = [r.model_dump(mode="json") for r in data.references]
    if data.aliases is not None:
        update_fields["aliases"] = data.aliases
    if data.tags is not None or "parent_id" in data.model_fields_set:
        base_tags = data.tags if data.tags is not None else page.tags
        new_parent_id = data.parent_id if "parent_id" in data.model_fields_set else page.parent_id
        update_fields["tags"] = await _merge_parent_tags(base_tags, new_parent_id, repo)
    if data.classification is not None:
        update_fields["classification"] = [c.model_dump(mode="json") for c in data.classification]
    if data.next_approval_date is not None:
        update_fields["next_approval_date"] = data.next_approval_date.isoformat()
    update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()

    if trust_tier == TrustTier.verified:
        # Deferred import: mutations.py imports create_page/update_page/etc. from this
        # module at load time, so importing back at module level would be circular.
        # By the time update_page() actually runs, both modules are fully loaded.
        from app.services.mutations import build_verification_fields
        final_content = update_fields.get("content", page.content)
        update_fields.update(build_verification_fields(final_content, user.user_id))

    history_entry = HistoryEntry(
        user_id=user.user_id,
        action="edit",
        diff=str(update_fields),
    )
    await repo.update_with_history(page_id, update_fields, history_entry)
    return await repo.get(page_id)


async def delete_page(page_id: str, user: User, repo: PageRepository) -> None:
    history_entry = HistoryEntry(user_id=user.user_id, action="delete")
    await repo.append_history(page_id, history_entry)
    await repo.delete(page_id)


_TIER_RANK = {"verified": 3, "source_checked": 2, "unverified": 1}


async def _find_raw_docs(
    query: str,
    user: User,
    repo: PageRepository,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
    fields: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    status_list = [s.value for s in statuses] if statuses else [PageStatus.published.value]

    strip_classification = False
    effective_fields: Optional[list[str]] = None
    if fields is not None:
        strip_classification = "classification" not in fields
        required = ["classification", "trust_tier", "inbound_link_count"]
        extra = [f for f in required if f not in fields]
        effective_fields = fields + extra

    results = await repo.search_by_name(query, status_list, limit, fields=effective_fields, tags=tags)

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
            key=lambda d: (
                _TIER_RANK.get(d.get("trust_tier", "unverified"), 0),
                d.get("inbound_link_count", 0),
            ),
            reverse=True,
        )
    return results


async def _find_raw_docs_fuzzy(
    query: str,
    user: User,
    repo: PageRepository,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
    fields: Optional[list[str]] = None,
    user_triangles: Optional[list[ClassificationTriangle]] = None,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    status_list = [s.value for s in statuses] if statuses else [PageStatus.published.value]

    strip_classification = False
    effective_fields: Optional[list[str]] = None
    if fields is not None:
        strip_classification = "classification" not in fields
        required = ["classification", "trust_tier", "inbound_link_count"]
        extra = [f for f in required if f not in fields]
        effective_fields = fields + extra

    results = await repo.fuzzy_search_by_name(query, status_list, limit, fields=effective_fields, tags=tags)

    if user_triangles is None:
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
            key=lambda d: (
                _TIER_RANK.get(d.get("trust_tier", "unverified"), 0),
                d.get("inbound_link_count", 0),
            ),
            reverse=True,
        )
    return results


async def find_pages(
    query: str,
    user: User,
    repo: PageRepository,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
    tags: Optional[list[str]] = None,
) -> list[Page]:
    docs = await _find_raw_docs(query, user=user, repo=repo, ranked=ranked, statuses=statuses, limit=limit, tags=tags)
    return [Page(**doc) for doc in docs]


async def find_page_docs(
    query: str,
    fields: list[str],
    user: User,
    repo: PageRepository,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    return await _find_raw_docs(
        query, user=user, repo=repo, ranked=ranked, statuses=statuses, limit=limit, fields=fields, tags=tags,
    )


async def set_page_references(page_id: str, references: list[Reference], repo: PageRepository) -> None:
    await repo.set_references(page_id, references)


async def search_pages(query: str, user: User, repo: PageRepository, tags: Optional[list[str]] = None) -> list[Page]:
    return await find_pages(query, user=user, repo=repo, tags=tags)


async def fuzzy_search_pages(
    query: str, user: User, repo: PageRepository, tags: Optional[list[str]] = None,
) -> list[Page]:
    from app.search_config import FUZZY_SEARCH_LIMIT
    docs = await _find_raw_docs_fuzzy(query, user=user, repo=repo, limit=FUZZY_SEARCH_LIMIT, tags=tags)
    return [Page(**doc) for doc in docs]


async def find_page_docs_fuzzy(
    query: str,
    fields: list[str],
    user: User,
    repo: PageRepository,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
    user_triangles: Optional[list[ClassificationTriangle]] = None,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    return await _find_raw_docs_fuzzy(
        query, user=user, repo=repo, ranked=ranked, statuses=statuses, limit=limit, fields=fields,
        user_triangles=user_triangles, tags=tags,
    )


async def _find_raw_docs_fuzzy_scored(
    query: str,
    user: User,
    repo: PageRepository,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
    fields: Optional[list[str]] = None,
    user_triangles: Optional[list[ClassificationTriangle]] = None,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    """Like _find_raw_docs_fuzzy but using fuzzy_search_scored (which includes a 'score' field)."""
    status_list = [s.value for s in statuses] if statuses else [PageStatus.published.value]

    strip_classification = False
    effective_fields: Optional[list[str]] = None
    if fields is not None:
        strip_classification = "classification" not in fields
        required = ["classification", "trust_tier", "inbound_link_count"]
        extra = [f for f in required if f not in fields]
        effective_fields = fields + extra

    results = await repo.fuzzy_search_scored(query, status_list, limit, fields=effective_fields, tags=tags)

    if user_triangles is None:
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
            key=lambda d: (
                _TIER_RANK.get(d.get("trust_tier", "unverified"), 0),
                d.get("inbound_link_count", 0),
            ),
            reverse=True,
        )
    return results


async def find_page_docs_fuzzy_scored(
    query: str,
    fields: list[str],
    user: User,
    repo: PageRepository,
    ranked: bool = False,
    statuses: Optional[list] = None,
    limit: int = 10,
    user_triangles: Optional[list[ClassificationTriangle]] = None,
    tags: Optional[list[str]] = None,
) -> list[dict]:
    """Fuzzy search returning results with numeric 'score' field."""
    return await _find_raw_docs_fuzzy_scored(
        query, user=user, repo=repo, ranked=ranked, statuses=statuses, limit=limit, fields=fields,
        user_triangles=user_triangles, tags=tags,
    )


async def get_page_tree(user: User, repo: PageRepository) -> list[dict]:
    nodes = await repo.get_tree_nodes()
    user_triangles = await get_user_triangles(user.user_id)
    pages = []
    for doc in nodes:
        page_classification = [ClassificationTriangle(**t) for t in doc.pop("classification", [])]
        if not user_satisfies_classification(user_triangles, page_classification):
            continue
        pages.append(doc)
    return pages


async def get_page_history(page_id: str, repo: PageRepository) -> list[dict]:
    history = await repo.get_history(page_id)
    return [h.model_dump(mode="json") for h in history]
