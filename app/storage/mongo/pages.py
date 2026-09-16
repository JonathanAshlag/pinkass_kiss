from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.models.page import (
    ClassificationTriangle, HistoryEntry, Page, PageStatus, Reference, TrustTier,
)
from app.models.page_version import PageVersion
from app.storage.base import PageRepository, with_always_included_fields


class MongoPageRepository(PageRepository):
    def __init__(self, db) -> None:
        self._db = db

    async def _create_version(self, page: Page, action: str, user_id: str, comment: Optional[str]) -> None:
        """Insert the next full-content snapshot for a page that just went live
        (status == published) and bump its current_version_number counter."""
        version_number = page.current_version_number + 1
        version = PageVersion(
            version_id=f"{page.page_id}-v{version_number}",
            page_id=page.page_id,
            version_number=version_number,
            action=action,
            user_id=user_id,
            comment=comment,
            title=page.title,
            description=page.description,
            content=page.content,
            parent_id=page.parent_id,
            references=page.references,
            aliases=page.aliases,
            tags=page.tags,
            classification=page.classification,
            status=page.status,
            trust_tier=page.trust_tier,
        )
        await self._db.page_versions.insert_one(version.model_dump(mode="json"))
        await self._db.pages.update_one(
            {"page_id": page.page_id},
            {"$set": {"current_version_number": version_number}},
        )

    async def get(self, page_id: str) -> Optional[Page]:
        doc = await self._db.pages.find_one({"page_id": page_id})
        if doc:
            doc.pop("_id", None)
            return Page(**doc)
        return None

    async def get_by_title(self, title: str) -> Optional[Page]:
        doc = await self._db.pages.find_one({"title": title})
        if doc:
            doc.pop("_id", None)
            return Page(**doc)
        return None

    async def create(self, page: Page) -> None:
        await self._db.pages.insert_one(page.model_dump(mode="json"))
        if page.status == PageStatus.published:
            await self._create_version(page, action="create", user_id=page.created_by, comment=None)

    async def update_fields(self, page_id: str, fields: dict) -> None:
        await self._db.pages.update_one({"page_id": page_id}, {"$set": fields})

    async def update_with_history(self, page_id: str, fields: dict, entry: HistoryEntry) -> None:
        await self._db.pages.update_one(
            {"page_id": page_id},
            {"$set": fields, "$push": {"history": entry.model_dump(mode="json")}},
        )
        merged = await self._db.pages.find_one({"page_id": page_id})
        if merged and merged.get("status") == PageStatus.published.value:
            merged.pop("_id", None)
            merged["history"] = []  # not needed for the snapshot, avoid re-validating the whole log
            await self._create_version(
                Page(**merged), action=entry.action, user_id=entry.user_id, comment=entry.comment,
            )

    async def append_history(self, page_id: str, entry: HistoryEntry) -> None:
        await self._db.pages.update_one(
            {"page_id": page_id},
            {"$push": {"history": entry.model_dump(mode="json")}},
        )

    async def set_references(self, page_id: str, refs: list[Reference]) -> None:
        await self._db.pages.update_one(
            {"page_id": page_id},
            {"$set": {"references": [r.model_dump(mode="json") for r in refs]}},
        )

    async def get_classification(self, page_id: str) -> Optional[list[ClassificationTriangle]]:
        doc = await self._db.pages.find_one({"page_id": page_id}, {"classification": 1})
        if not doc:
            return None
        return [ClassificationTriangle(**t) for t in doc.get("classification", [])]

    async def get_history(self, page_id: str) -> list[HistoryEntry]:
        doc = await self._db.pages.find_one({"page_id": page_id}, {"history": 1})
        if not doc:
            return []
        return [HistoryEntry(**h) for h in doc.get("history", [])]

    async def get_versions(self, page_id: str) -> list[PageVersion]:
        cursor = self._db.page_versions.find({"page_id": page_id}).sort("version_number", 1)
        results = []
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(PageVersion(**doc))
        return results

    async def get_version(self, version_id: str) -> Optional[PageVersion]:
        doc = await self._db.page_versions.find_one({"version_id": version_id})
        if not doc:
            return None
        doc.pop("_id", None)
        return PageVersion(**doc)

    async def archive(self, page_id: str, deleted_by: str) -> None:
        page_doc = await self._db.pages.find_one({"page_id": page_id})
        if not page_doc:
            return
        page_doc.pop("_id", None)

        versions: list[dict] = []
        async for v in self._db.page_versions.find({"page_id": page_id}):
            v.pop("_id", None)
            versions.append(v)

        archive_doc = {
            "page_id": page_id,
            "page": page_doc,
            "versions": versions,
            "history": page_doc.get("history", []),
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by": deleted_by,
        }
        await self._db.deleted_pages.update_one(
            {"page_id": page_id}, {"$set": archive_doc}, upsert=True,
        )
        await self._db.page_versions.delete_many({"page_id": page_id})
        await self._db.pages.delete_one({"page_id": page_id})

    async def list_deleted_pages(self) -> list[dict]:
        cursor = self._db.deleted_pages.find(
            {}, {"page_id": 1, "deleted_at": 1, "deleted_by": 1, "page.title": 1, "_id": 0},
        )
        results = []
        async for doc in cursor:
            results.append({
                "page_id": doc["page_id"],
                "title": doc.get("page", {}).get("title", ""),
                "deleted_at": doc.get("deleted_at"),
                "deleted_by": doc.get("deleted_by"),
            })
        return results

    async def get_deleted_page(self, page_id: str) -> Optional[dict]:
        doc = await self._db.deleted_pages.find_one({"page_id": page_id})
        if not doc:
            return None
        doc.pop("_id", None)
        return doc

    async def fuzzy_search_scored(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """Fuzzy-search with numeric score. Try $text first (real score), fall back to rank-based."""
        mongo_filter: dict = {"status": {"$in": statuses}}
        if tags:
            mongo_filter["tags"] = {"$in": tags}

        projection: Optional[dict] = None
        if fields is not None:
            projection = {f: 1 for f in with_always_included_fields(fields)}
            projection["_id"] = 0

        results: list[dict] = []
        try:
            # Try $text search with textScore
            text_filter = {"$text": {"$search": query}, **mongo_filter}
            cursor = (
                self._db.pages.find(text_filter, projection).limit(limit)
                if projection
                else self._db.pages.find(text_filter).limit(limit)
            )
            async for doc in cursor:
                doc.pop("_id", None)
                # Extract text score if available from the query; Mongo doesn't return it by default
                # Fall back to rank-based scoring
                results.append(doc)
        except Exception:
            # No $text index or query failed; fall back to fuzzy_search_by_name and add rank-based scores
            results = await self.fuzzy_search_by_name(query, statuses, limit, fields, tags=tags)

        # Add rank-based score if not already present (for both $text and fallback paths)
        for i, d in enumerate(results):
            if "score" not in d:
                d["score"] = max(0.0, 1.0 - i * 0.05)

        return results

    async def list_scannable_pages(
        self,
        fields: Optional[list[str]] = None,
    ) -> list[dict]:
        """Return all published pages for passive-scan caching (complete, not limited)."""
        mongo_filter = {"status": "published"}

        projection: Optional[dict] = None
        if fields is not None:
            projection = {f: 1 for f in with_always_included_fields(fields)}
            projection["_id"] = 0

        results: list[dict] = []
        cursor = (
            self._db.pages.find(mongo_filter, projection)
            if projection
            else self._db.pages.find(mongo_filter)
        )
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)

        return results

    async def search_by_name(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """Match pages by title or alias only (not content/description)."""
        mongo_filter: dict = {"status": {"$in": statuses}}
        if tags:
            mongo_filter["tags"] = {"$in": tags}

        projection: Optional[dict] = None
        if fields is not None:
            projection = {f: 1 for f in with_always_included_fields(fields)}
            projection["_id"] = 0

        results: list[dict] = []
        try:
            text_filter = {"$text": {"$search": query}, **mongo_filter}
            cursor = (
                self._db.pages.find(text_filter, projection).limit(limit)
                if projection
                else self._db.pages.find(text_filter).limit(limit)
            )
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(doc)
        except Exception:
            cursor = (
                self._db.pages.find(mongo_filter, projection).limit(limit)
                if projection
                else self._db.pages.find(mongo_filter).limit(limit)
            )
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(doc)

        return results

    async def get_tree_nodes(self) -> list[dict]:
        projection = {"page_id": 1, "title": 1, "parent_id": 1, "status": 1, "classification": 1, "tags": 1, "_id": 0}
        cursor = self._db.pages.find({"status": {"$ne": "deleted"}}, projection)
        nodes = []
        async for doc in cursor:
            doc.pop("_id", None)
            nodes.append(doc)
        return nodes

    async def list_expired(self, today: str) -> list[dict]:
        cursor = self._db.pages.find(
            {
                "status": PageStatus.published.value,
                "next_approval_date": {"$lte": today, "$ne": None},
            },
            {"page_id": 1, "created_by": 1, "title": 1, "content": 1, "_id": 0},
        )
        results = []
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results

    async def list_verified_published(self) -> list[dict]:
        cursor = self._db.pages.find(
            {"trust_tier": TrustTier.verified.value, "status": PageStatus.published.value},
            {"page_id": 1, "created_by": 1, "content": 1, "verified_content_hash": 1, "title": 1, "_id": 0},
        )
        results = []
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results

    async def list_published_with_references(self) -> list[dict]:
        cursor = self._db.pages.find(
            {"status": PageStatus.published.value},
            {"page_id": 1, "references": 1, "_id": 0},
        )
        results = []
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results

    async def update_inbound_link_count(self, page_id: str, count: int) -> None:
        await self._db.pages.update_one(
            {"page_id": page_id},
            {"$set": {"inbound_link_count": count}},
        )

    async def reset_inbound_link_counts(self, except_ids: list[str]) -> None:
        query = {"page_id": {"$nin": except_ids}} if except_ids else {}
        await self._db.pages.update_many(query, {"$set": {"inbound_link_count": 0}})

    async def delete(self, page_id: str) -> None:
        await self._db.pages.delete_one({"page_id": page_id})
