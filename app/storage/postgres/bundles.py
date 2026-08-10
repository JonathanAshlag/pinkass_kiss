from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models import BundleORM
from app.models.bundle import Bundle
from app.storage.base import BundleRepository


class PostgresBundleRepository(BundleRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get(self, name: str) -> Bundle | None:
        result = await self._s.execute(select(BundleORM).where(BundleORM.name == name))
        row = result.scalars().first()
        if not row:
            return None
        return Bundle(
            name=row.name,
            entries=row.entries or [],
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def upsert(self, bundle: Bundle) -> None:
        entries = [e.model_dump(mode="json") for e in bundle.entries]
        result = await self._s.execute(select(BundleORM).where(BundleORM.name == bundle.name))
        existing = result.scalars().first()
        if existing:
            existing.entries = entries
            existing.updated_at = bundle.updated_at
            await self._s.flush()
        else:
            orm = BundleORM(
                name=bundle.name,
                entries=entries,
                created_by=bundle.created_by,
                created_at=bundle.created_at,
                updated_at=bundle.updated_at,
            )
            self._s.add(orm)
            await self._s.flush()

    async def list_all(self) -> list[Bundle]:
        result = await self._s.execute(select(BundleORM))
        rows = result.scalars().all()
        return [
            Bundle(
                name=row.name,
                entries=row.entries or [],
                created_by=row.created_by,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
