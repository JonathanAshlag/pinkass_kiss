"""One-time migration: pin every existing bundle entry's version_id to its page's
current latest version, so pre-existing bundles become frozen/reproducible the
moment version pinning ships (matching newly-created entries, which are pinned by
the caller explicitly). Entries that already carry a version_id are left untouched.

Run scripts/backfill_page_versions.py FIRST — entries for pages with no version yet
are skipped and reported, not silently left unpinned forever.

Respects DB_BACKEND env var (mongodb / postgres).

Usage:
    python scripts/migrate_bundle_pins.py            # dry run: prints what would change
    python scripts/migrate_bundle_pins.py --apply     # actually writes the changes
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings


async def _run(apply: bool, bundle_repo, page_repo) -> None:
    bundles = await bundle_repo.list_all()
    total_pinned = 0
    total_skipped = 0

    for bundle in bundles:
        changed = False
        for entry in bundle.entries:
            if entry.version_id is not None:
                continue
            versions = await page_repo.get_versions(entry.page_id)
            if not versions:
                print(f"  ! {bundle.name!r}: no version found for page {entry.page_id!r} — skipped "
                      f"(run scripts/backfill_page_versions.py first)")
                total_skipped += 1
                continue
            entry.version_id = versions[-1].version_id
            changed = True
            total_pinned += 1
            print(f"  {bundle.name!r}: pinned {entry.page_id!r} -> {entry.version_id!r}")
        if changed and apply:
            await bundle_repo.upsert(bundle)

    print(f"\n{total_pinned} entr{'y' if total_pinned == 1 else 'ies'} to pin across {len(bundles)} bundle(s)"
          f"{f', {total_skipped} skipped (no version yet)' if total_skipped else ''}.")
    if not apply:
        print("Dry run only — pass --apply to write these changes.")
    else:
        print("✓ Applied.")


async def _migrate_mongo(apply: bool) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.storage.mongo.bundles import MongoBundleRepository
    from app.storage.mongo.pages import MongoPageRepository

    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]
    await _run(apply, MongoBundleRepository(db), MongoPageRepository(db))
    client.close()


async def _migrate_postgres(apply: bool) -> None:
    from app.infrastructure.postgres.engine import get_session_factory, init_engine, close_engine
    from app.storage.postgres.bundles import PostgresBundleRepository
    from app.storage.postgres.pages import PostgresPageRepository

    init_engine(settings.postgres_uri)
    factory = get_session_factory()
    async with factory() as session:
        await _run(apply, PostgresBundleRepository(session), PostgresPageRepository(session))
        if apply:
            await session.commit()
        else:
            await session.rollback()
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
