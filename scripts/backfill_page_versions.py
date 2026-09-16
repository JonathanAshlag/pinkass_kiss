"""One-time backfill: synthesize a v1 PageVersion for every existing published page,
so pages created before the version-history feature shipped have something for the
history UI to show and something for scripts/migrate_bundle_pins.py to pin bundles to.

Going forward, PageRepository.create()/update_with_history() create versions
automatically whenever a mutation makes new content live — this script only
covers the one-time gap for pre-existing data. Run this BEFORE migrate_bundle_pins.py.

Respects DB_BACKEND env var (mongodb / postgres).

Usage:
    python scripts/backfill_page_versions.py            # dry run: prints what would be created
    python scripts/backfill_page_versions.py --apply     # actually writes the versions
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models.page_version import PageVersion


async def _backfill_mongo(apply: bool) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    docs = [
        doc async for doc in db.pages.find(
            {"status": "published", "current_version_number": {"$in": [0, None]}}
        )
    ]
    print(f"{len(docs)} published page(s) without a version yet.")
    for doc in docs:
        print(f"  {doc['page_id']!r}")

    if not apply:
        print("\nDry run only — pass --apply to write these changes.")
        client.close()
        return

    for doc in docs:
        version = PageVersion(
            version_id=f"{doc['page_id']}-v1",
            page_id=doc["page_id"],
            version_number=1,
            action="create",
            user_id=doc.get("created_by", "system"),
            timestamp=doc.get("created_at"),
            title=doc["title"],
            description=doc.get("description", ""),
            content=doc.get("content", ""),
            parent_id=doc.get("parent_id"),
            references=doc.get("references", []),
            aliases=doc.get("aliases", []),
            tags=doc.get("tags", []),
            classification=doc.get("classification", []),
            status=doc["status"],
            trust_tier=doc.get("trust_tier", "unverified"),
        )
        await db.page_versions.insert_one(version.model_dump(mode="json"))
        await db.pages.update_one(
            {"page_id": doc["page_id"]}, {"$set": {"current_version_number": 1}},
        )

    print(f"\n✓ Backfilled v1 for {len(docs)} page(s) in MongoDB.")
    client.close()


async def _backfill_postgres(apply: bool) -> None:
    from sqlalchemy import select, update
    from app.infrastructure.postgres.engine import get_session_factory, init_engine, close_engine
    from app.infrastructure.postgres.models import PageORM, PageVersionORM

    init_engine(settings.postgres_uri)
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PageORM).where(PageORM.status == "published", PageORM.current_version_number == 0)
        )
        rows = result.scalars().all()
        print(f"{len(rows)} published page(s) without a version yet.")
        for row in rows:
            print(f"  {row.page_id!r}")

        if not apply:
            print("\nDry run only — pass --apply to write these changes.")
            await session.rollback()
            await close_engine()
            return

        for row in rows:
            session.add(PageVersionORM(
                version_id=f"{row.page_id}-v1",
                page_id=row.page_id,
                version_number=1,
                action="create",
                user_id=row.created_by,
                timestamp=row.created_at,
                comment=None,
                title=row.title,
                description=row.description,
                content=row.content,
                parent_id=row.parent_id,
                references=row.references or [],
                aliases=row.aliases or [],
                tags=row.tags or [],
                classification=row.classification or [],
                status=row.status,
                trust_tier=row.trust_tier,
            ))
            await session.execute(
                update(PageORM).where(PageORM.page_id == row.page_id).values(current_version_number=1)
            )
        await session.commit()

    print(f"\n✓ Backfilled v1 for {len(rows)} page(s) in PostgreSQL.")
    await close_engine()


async def main() -> None:
    apply = "--apply" in sys.argv[1:]
    print(f"Backend: {settings.db_backend} ({'APPLY' if apply else 'DRY RUN'})")
    if settings.db_backend == "postgres":
        await _backfill_postgres(apply)
    else:
        await _backfill_mongo(apply)


if __name__ == "__main__":
    asyncio.run(main())
