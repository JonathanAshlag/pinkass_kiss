"""One-time migration: reassign every page's page_id from the old "page_id == title"
scheme to the new Slack-style {normalized-title}-{suffix} scheme, and rewrite every
place that references the old id.

Respects DB_BACKEND env var (mongodb / postgres).

Usage:
    python scripts/migrate_page_ids.py            # dry run: prints the id mapping only
    python scripts/migrate_page_ids.py --apply     # actually writes the changes

The app is pre-launch (see .claude/rules/postgres-migrations.md), so this intentionally
does a direct/raw rewrite rather than an alembic-tracked schema change — the schema
itself (page_id as a String column/PK) is unchanged, only the data.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.page_id import generate_page_id


def _print_mapping(mapping: dict[str, str]) -> None:
    print(f"\n{len(mapping)} page(s) to migrate:")
    for old_id, new_id in mapping.items():
        print(f"  {old_id!r} -> {new_id!r}")


async def _migrate_mongo(apply: bool) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    docs = [doc async for doc in db.pages.find({}, {"page_id": 1, "title": 1, "_id": 0})]

    mapping: dict[str, str] = {}

    async def exists(candidate: str) -> bool:
        if candidate in mapping.values():
            return True
        return await db.pages.find_one({"page_id": candidate}) is not None

    for doc in docs:
        mapping[doc["page_id"]] = await generate_page_id(doc["title"], exists)

    _print_mapping(mapping)
    if not apply:
        print("\nDry run only — pass --apply to write these changes.")
        client.close()
        return

    for old_id, new_id in mapping.items():
        await db.pages.update_one({"page_id": old_id}, {"$set": {"page_id": new_id}})

    async for doc in db.pages.find({}, {"references": 1}):
        refs = doc.get("references") or []
        changed = False
        for ref in refs:
            if ref.get("page_id") in mapping:
                ref["page_id"] = mapping[ref["page_id"]]
                changed = True
        if changed:
            await db.pages.update_one({"_id": doc["_id"]}, {"$set": {"references": refs}})

    print(f"\n✓ Migrated {len(mapping)} page(s) and their embedded references in MongoDB.")
    client.close()


async def _migrate_postgres(apply: bool) -> None:
    from sqlalchemy import text
    from app.infrastructure.postgres.engine import get_session_factory, init_engine, close_engine
    from app.infrastructure.postgres.models import SCHEMA

    init_engine(settings.postgres_uri)
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text(f'SELECT page_id, title FROM {SCHEMA}.pages'))).all()

        mapping: dict[str, str] = {}

        async def exists(candidate: str) -> bool:
            if candidate in mapping.values():
                return True
            result = await session.execute(
                text(f'SELECT 1 FROM {SCHEMA}.pages WHERE page_id = :pid'), {"pid": candidate}
            )
            return result.first() is not None

        for old_id, title in rows:
            mapping[old_id] = await generate_page_id(title, exists)

        _print_mapping(mapping)
        if not apply:
            print("\nDry run only — pass --apply to write these changes.")
            await session.rollback()
            await close_engine()
            return

        # page_id is a FK target (page_refs.from/to_page_id, page_revisions.page_id) —
        # drop those constraints for the duration of the rename, then recreate them
        # identically (same columns/ON DELETE behavior; Postgres re-derives the name).
        fk_rows = (await session.execute(text(
            """
            SELECT tc.constraint_name, tc.table_name, kcu.column_name, rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name AND tc.table_schema = rc.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON rc.unique_constraint_name = ccu.constraint_name AND rc.unique_constraint_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = :schema
              AND ccu.table_name = 'pages' AND ccu.column_name = 'page_id'
            """
        ), {"schema": SCHEMA})).all()

        for constraint_name, table_name, _column_name, _delete_rule in fk_rows:
            await session.execute(text(f'ALTER TABLE {SCHEMA}.{table_name} DROP CONSTRAINT {constraint_name}'))

        for old_id, new_id in mapping.items():
            await session.execute(
                text(f'UPDATE {SCHEMA}.pages SET page_id = :new WHERE page_id = :old'),
                {"new": new_id, "old": old_id},
            )
            await session.execute(
                text(f'UPDATE {SCHEMA}.page_refs SET from_page_id = :new WHERE from_page_id = :old'),
                {"new": new_id, "old": old_id},
            )
            await session.execute(
                text(f'UPDATE {SCHEMA}.page_refs SET to_page_id = :new WHERE to_page_id = :old'),
                {"new": new_id, "old": old_id},
            )
            await session.execute(
                text(f'UPDATE {SCHEMA}.page_revisions SET page_id = :new WHERE page_id = :old'),
                {"new": new_id, "old": old_id},
            )
            await session.execute(
                text(f'UPDATE {SCHEMA}.requests SET page_id = :new WHERE page_id = :old'),
                {"new": new_id, "old": old_id},
            )

        # references is a JSON column embedding page_id values too — rewrite those.
        page_rows = (await session.execute(text(f'SELECT page_id, "references" FROM {SCHEMA}.pages'))).all()
        for pid, refs in page_rows:
            if not refs:
                continue
            changed = False
            for ref in refs:
                if ref.get("page_id") in mapping:
                    ref["page_id"] = mapping[ref["page_id"]]
                    changed = True
            if changed:
                await session.execute(
                    text(f'UPDATE {SCHEMA}.pages SET "references" = :refs WHERE page_id = :pid'),
                    {"refs": json.dumps(refs), "pid": pid},
                )

        for constraint_name, table_name, column_name, delete_rule in fk_rows:
            await session.execute(text(
                f'ALTER TABLE {SCHEMA}.{table_name} ADD CONSTRAINT {constraint_name} '
                f'FOREIGN KEY ({column_name}) REFERENCES {SCHEMA}.pages(page_id) ON DELETE {delete_rule}'
            ))

        await session.commit()

    print(f"\n✓ Migrated {len(mapping)} page(s) and their references in PostgreSQL.")
    await close_engine()


async def main() -> None:
    apply = "--apply" in sys.argv[1:]
    print(f"Backend: {settings.db_backend} ({'APPLY' if apply else 'DRY RUN'})")
    if settings.db_backend == "postgres":
        await _migrate_postgres(apply)
    else:
        await _migrate_mongo(apply)


if __name__ == "__main__":
    asyncio.run(main())
