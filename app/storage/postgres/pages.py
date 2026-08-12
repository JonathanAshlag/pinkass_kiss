"""PostgreSQL implementation of PageRepository."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import (
    ClassificationTriangle, HistoryEntry, Page, PageStatus, Reference,
    ReferenceType, TrustTier,
)
from app.storage.base import PageRepository, with_always_included_fields
from app.infrastructure.postgres.models import PageORM, PageRefORM, PageRevisionORM


def _orm_to_page(row: PageORM) -> Page:
    """Convert an ORM row to a Pydantic Page (history excluded — use get_history)."""
    refs = [Reference(**r) for r in (row.references or [])]
    classification = [ClassificationTriangle(**c) for c in (row.classification or [])]
    return Page(
        page_id=row.page_id,
        title=row.title,
        description=row.description,
        parent_id=row.parent_id,
        content=row.content,
        references=refs,
        aliases=row.aliases or [],
        tags=row.tags or [],
        classification=classification,
        status=PageStatus(row.status),
        trust_tier=TrustTier(row.trust_tier),
        next_approval_date=row.next_approval_date,
        verified_content_hash=row.verified_content_hash,
        verified_at=row.verified_at,
        verified_by=row.verified_by,
        inbound_link_count=row.inbound_link_count,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        history=[],
    )


class PostgresPageRepository(PageRepository):
    def __init__(self, session: AsyncSession, *, dialect: str = "postgresql") -> None:
        self._s = session
        self._dialect = dialect

    async def get(self, page_id: str) -> Optional[Page]:
        result = await self._s.execute(select(PageORM).where(PageORM.page_id == page_id))
        row = result.scalar_one_or_none()
        if not row:
            return None
        page = _orm_to_page(row)
        page.history = await self.get_history(page_id)
        return page

    async def create(self, page: Page) -> None:
        orm = PageORM(
            page_id=page.page_id,
            title=page.title,
            description=page.description,
            parent_id=page.parent_id,
            content=page.content,
            status=page.status.value,
            trust_tier=page.trust_tier.value,
            next_approval_date=page.next_approval_date.isoformat() if page.next_approval_date else None,
            verified_content_hash=page.verified_content_hash,
            verified_at=page.verified_at,
            verified_by=page.verified_by,
            inbound_link_count=page.inbound_link_count,
            created_by=page.created_by,
            created_at=page.created_at,
            updated_at=page.updated_at,
            classification=[c.model_dump(mode="json") for c in page.classification],
            references=[r.model_dump(mode="json") for r in page.references],
            aliases=page.aliases,
            tags=page.tags,
        )
        self._s.add(orm)
        for entry in page.history:
            rev = PageRevisionORM(
                page_id=page.page_id,
                user_id=entry.user_id,
                action=entry.action,
                diff=entry.diff,
                snapshot=entry.snapshot,
                comment=entry.comment,
                created_at=entry.timestamp,
            )
            self._s.add(rev)
        # Persist page refs in the normalized table
        for ref in page.references:
            self._s.add(PageRefORM(
                from_page_id=page.page_id,
                ref_type=ref.type.value,
                to_page_id=ref.page_id if ref.type == ReferenceType.page else None,
                file_id=ref.file_id if ref.type == ReferenceType.file else None,
            ))
        await self._s.flush()

    async def update_fields(self, page_id: str, fields: dict) -> None:
        if not fields:
            return
        await self._s.execute(
            update(PageORM).where(PageORM.page_id == page_id).values(**self._coerce(fields))
        )
        await self._s.flush()

    async def update_with_history(self, page_id: str, fields: dict, entry: HistoryEntry) -> None:
        await self.update_fields(page_id, fields)
        await self.append_history(page_id, entry)

    async def append_history(self, page_id: str, entry: HistoryEntry) -> None:
        rev = PageRevisionORM(
            page_id=page_id,
            user_id=entry.user_id,
            action=entry.action,
            diff=entry.diff,
            snapshot=entry.snapshot,
            comment=entry.comment,
            created_at=entry.timestamp,
        )
        self._s.add(rev)
        await self._s.flush()

    async def set_references(self, page_id: str, refs: list[Reference]) -> None:
        await self._s.execute(delete(PageRefORM).where(PageRefORM.from_page_id == page_id))
        for ref in refs:
            self._s.add(PageRefORM(
                from_page_id=page_id,
                ref_type=ref.type.value,
                to_page_id=ref.page_id if ref.type == ReferenceType.page else None,
                file_id=ref.file_id if ref.type == ReferenceType.file else None,
            ))
        ref_json = [r.model_dump(mode="json") for r in refs]
        await self._s.execute(
            update(PageORM).where(PageORM.page_id == page_id).values(references=ref_json)
        )
        await self._s.flush()

    async def get_classification(self, page_id: str) -> Optional[list[ClassificationTriangle]]:
        result = await self._s.execute(
            select(PageORM.classification).where(PageORM.page_id == page_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return [ClassificationTriangle(**c) for c in (row[0] or [])]

    async def get_history(self, page_id: str) -> list[HistoryEntry]:
        result = await self._s.execute(
            select(PageRevisionORM)
            .where(PageRevisionORM.page_id == page_id)
            .order_by(PageRevisionORM.created_at)
        )
        rows = result.scalars().all()
        return [
            HistoryEntry(
                timestamp=row.created_at,
                user_id=row.user_id,
                action=row.action,
                diff=row.diff,
                snapshot=row.snapshot,
                comment=row.comment,
            )
            for row in rows
        ]

    def _tags_clause(self, tags: list[str]):
        """WHERE clause matching pages that have at least one of the given tags."""
        if self._dialect == "postgresql":
            return PageORM.tags.op("?|")(pg_array(tags))
        # SQLite's JSON serializer escapes non-ASCII text (ensure_ascii=True),
        # so match against the same json.dumps() encoding rather than raw text.
        return or_(*[PageORM.tags.cast(String).like(f"%{json.dumps(t)}%") for t in tags])

    async def fuzzy_search_scored(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """Fuzzy-search with numeric score. Postgres: real word_similarity, others: rank-based."""
        from app.search_config import FUZZY_TITLE_THRESHOLD, FUZZY_ALIASES_THRESHOLD

        base = select(PageORM).where(PageORM.status.in_(statuses))
        if tags:
            base = base.where(self._tags_clause(tags))

        if query and self._dialect == "postgresql":
            # Real Postgres fuzzy matching with word_similarity scores
            sim_title = func.word_similarity(query, PageORM.title)
            sim_aliases = func.word_similarity(query, func.cast(PageORM.aliases, String))
            sim_max = func.greatest(sim_title, sim_aliases).label("score")
            stmt = (
                select(PageORM, sim_max)
                .where(or_(
                    sim_title > FUZZY_TITLE_THRESHOLD,
                    sim_aliases > FUZZY_ALIASES_THRESHOLD,
                ))
                .order_by(sim_max.desc())
                .limit(limit)
            )
            result = await self._s.execute(stmt)
            rows = result.tuples().all()
            rows_list = [self._row_to_dict(row[0], fields) for row in rows]
            for i, row in enumerate(rows_list):
                row["score"] = float(rows[i][1])  # Add the real score
            return rows_list
        else:
            # Fall back to rank-based scoring
            stmt = base.limit(limit)
            result = await self._s.execute(stmt)
            rows = result.scalars().all()
            rows_list = [self._row_to_dict(row, fields) for row in rows]
            for i, row in enumerate(rows_list):
                row["score"] = max(0.0, 1.0 - i * 0.05)
            return rows_list

    async def list_scannable_pages(
        self,
        fields: Optional[list[str]] = None,
    ) -> list[dict]:
        """Return all published pages (unfiltered by limit) for caching."""
        base = select(PageORM).where(PageORM.status == "published")
        result = await self._s.execute(base)
        rows = result.scalars().all()
        return [self._row_to_dict(row, fields) for row in rows]

    async def search_by_name(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """Match pages by title or alias only (not content/description)."""
        base = select(PageORM).where(PageORM.status.in_(statuses))
        if tags:
            base = base.where(self._tags_clause(tags))

        if query and self._dialect == "postgresql":
            tsquery = func.plainto_tsquery("english", query)
            stmt = base.where(PageORM.tsv.op("@@")(tsquery)).limit(limit)
        elif query:
            stmt = base.where(
                or_(
                    PageORM.title.ilike(f"%{query}%"),
                    PageORM.aliases.cast(String).ilike(f"%{query}%"),
                )
            ).limit(limit)
        else:
            stmt = base.limit(limit)

        result = await self._s.execute(stmt)
        rows = result.scalars().all()
        return [self._row_to_dict(row, fields) for row in rows]

    async def fuzzy_search_by_name(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """Fuzzy-match pages by title or alias only (not content/description)."""
        from app.search_config import FUZZY_TITLE_THRESHOLD, FUZZY_ALIASES_THRESHOLD
        base = select(PageORM).where(PageORM.status.in_(statuses))
        if tags:
            base = base.where(self._tags_clause(tags))

        if query and self._dialect == "postgresql":
            sim_title = func.word_similarity(query, PageORM.title)
            sim_aliases = func.word_similarity(query, func.cast(PageORM.aliases, String))
            stmt = (
                base
                .where(or_(
                    sim_title > FUZZY_TITLE_THRESHOLD,
                    sim_aliases > FUZZY_ALIASES_THRESHOLD,
                ))
                .order_by(func.greatest(sim_title, sim_aliases).desc())
                .limit(limit)
            )
        elif query:
            stmt = base.where(
                or_(
                    PageORM.title.ilike(f"%{query}%"),
                    PageORM.aliases.cast(String).ilike(f"%{query}%"),
                )
            ).limit(limit)
        else:
            stmt = base.limit(limit)

        result = await self._s.execute(stmt)
        rows = result.scalars().all()
        return [self._row_to_dict(row, fields) for row in rows]

    async def find_similar_for_dedup(
        self,
        title: str,
        description: str,
        threshold: float,
        limit: int,
        fields: Optional[list[str]] = None,
    ) -> list[dict]:
        if self._dialect != "postgresql":
            return await super().find_similar_for_dedup(title, description, threshold, limit, fields)
        sim = func.greatest(
            func.word_similarity(title, PageORM.title),
            func.word_similarity(description, PageORM.description),
        )
        stmt = (
            select(PageORM)
            .where(PageORM.status != PageStatus.deleted.value)
            .where(sim >= threshold)
            .order_by(sim.desc())
            .limit(limit)
        )
        result = await self._s.execute(stmt)
        rows = result.scalars().all()
        return [self._row_to_dict(row, fields) for row in rows]

    async def get_tree_nodes(self) -> list[dict]:
        result = await self._s.execute(
            select(PageORM).where(PageORM.status != "deleted")
        )
        rows = result.scalars().all()
        return [
            {
                "page_id": row.page_id,
                "title": row.title,
                "parent_id": row.parent_id,
                "status": row.status,
                "classification": row.classification or [],
                "tags": row.tags or [],
            }
            for row in rows
        ]

    async def list_expired(self, today: str) -> list[dict]:
        result = await self._s.execute(
            select(PageORM).where(
                PageORM.status == PageStatus.published.value,
                PageORM.next_approval_date.isnot(None),
                PageORM.next_approval_date <= today,
            )
        )
        rows = result.scalars().all()
        return [
            {
                "page_id": row.page_id,
                "created_by": row.created_by,
                "title": row.title,
                "content": row.content,
            }
            for row in rows
        ]

    async def list_verified_published(self) -> list[dict]:
        result = await self._s.execute(
            select(PageORM).where(
                PageORM.trust_tier == TrustTier.verified.value,
                PageORM.status == PageStatus.published.value,
            )
        )
        rows = result.scalars().all()
        return [
            {
                "page_id": row.page_id,
                "created_by": row.created_by,
                "content": row.content,
                "verified_content_hash": row.verified_content_hash,
                "title": row.title,
            }
            for row in rows
        ]

    async def list_published_with_references(self) -> list[dict]:
        result = await self._s.execute(
            select(PageORM.page_id, PageORM.references).where(
                PageORM.status == PageStatus.published.value
            )
        )
        rows = result.all()
        return [{"page_id": row[0], "references": row[1] or []} for row in rows]

    async def update_inbound_link_count(self, page_id: str, count: int) -> None:
        await self._s.execute(
            update(PageORM).where(PageORM.page_id == page_id).values(inbound_link_count=count)
        )
        await self._s.flush()

    async def reset_inbound_link_counts(self, except_ids: list[str]) -> None:
        if except_ids:
            await self._s.execute(
                update(PageORM)
                .where(PageORM.page_id.notin_(except_ids))
                .values(inbound_link_count=0)
            )
        else:
            await self._s.execute(update(PageORM).values(inbound_link_count=0))
        await self._s.flush()

    async def delete(self, page_id: str) -> None:
        await self._s.execute(delete(PageORM).where(PageORM.page_id == page_id))
        await self._s.flush()

    def _coerce(self, fields: dict) -> dict:
        """Convert service-layer values to ORM-compatible types (e.g. ISO strings → datetime)."""
        from datetime import datetime as dt
        _datetime_cols = {"created_at", "updated_at", "verified_at"}
        result = {}
        for k, v in fields.items():
            if k in _datetime_cols and isinstance(v, str):
                try:
                    result[k] = dt.fromisoformat(v)
                except (ValueError, TypeError):
                    result[k] = v
            else:
                result[k] = v
        return result

    def _row_to_dict(self, row: PageORM, fields: Optional[list[str]]) -> dict:
        all_fields = {
            "page_id": row.page_id,
            "title": row.title,
            "description": row.description,
            "parent_id": row.parent_id,
            "content": row.content,
            "status": row.status,
            "trust_tier": row.trust_tier,
            "next_approval_date": row.next_approval_date,
            "inbound_link_count": row.inbound_link_count,
            "classification": row.classification or [],
            "references": row.references or [],
            "aliases": row.aliases or [],
            "tags": row.tags or [],
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        if fields is None:
            return all_fields
        # Always include ranking + classification even if not in requested fields
        required = with_always_included_fields(fields)
        return {k: v for k, v in all_fields.items() if k in required}
