from __future__ import annotations

from typing import Optional

from app.models.page import (
    ClassificationTriangle, HistoryEntry, Page, PageStatus, Reference, TrustTier,
)
from app.storage.base import PageRepository, with_always_included_fields


class MongoPageRepository(PageRepository):
    def __init__(self, db) -> None:
        self._db = db

    async def get(self, page_id: str) -> Optional[Page]:
        doc = await self._db.pages.find_one({"page_id": page_id})
        if doc:
            doc.pop("_id", None)
            return Page(**doc)
        return None

    async def create(self, page: Page) -> None:
        await self._db.pages.insert_one(page.model_dump(mode="json"))

    async def update_fields(self, page_id: str, fields: dict) -> None:
        await self._db.pages.update_one({"page_id": page_id}, {"$set": fields})

    async def update_with_history(self, page_id: str, fields: dict, entry: HistoryEntry) -> None:
        await self._db.pages.update_one(
            {"page_id": page_id},
            {"$set": fields, "$push": {"history": entry.model_dump(mode="json")}},
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
