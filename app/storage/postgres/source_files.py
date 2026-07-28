"""PostgreSQL implementation of SourceFileRepository."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete as sa_delete, select, update

from app.storage.base import SourceFileRepository
from app.infrastructure.postgres.models import SourceFileORM


class PostgresSourceFileRepository(SourceFileRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        file_id: str,
        filename: str,
        content_type: str,
        uploaded_by: str,
        extracted_text: str,
    ) -> None:
        self._s.add(SourceFileORM(
            file_id=file_id,
            filename=filename,
            content_type=content_type,
            uploaded_by=uploaded_by,
            extracted_text=extracted_text,
            generated_page_ids=[],
        ))
        await self._s.flush()

    async def set_page_ids(self, file_id: str, page_ids: list[str]) -> None:
        await self._s.execute(
            update(SourceFileORM)
            .where(SourceFileORM.file_id == file_id)
            .values(generated_page_ids=page_ids)
        )
        await self._s.flush()

    async def get_for_page(self, page_id: str) -> dict | None:
        result = await self._s.execute(select(SourceFileORM))
        for row in result.scalars():
            if page_id in (row.generated_page_ids or []):
                return {
                    "file_id": row.file_id,
                    "filename": row.filename,
                    "generated_page_ids": list(row.generated_page_ids),
                }
        return None

    async def remove_page_id(self, file_id: str, page_id: str) -> list[str]:
        result = await self._s.execute(
            select(SourceFileORM).where(SourceFileORM.file_id == file_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return []
        remaining = [pid for pid in (row.generated_page_ids or []) if pid != page_id]
        await self._s.execute(
            update(SourceFileORM)
            .where(SourceFileORM.file_id == file_id)
            .values(generated_page_ids=remaining)
        )
        await self._s.flush()
        return remaining

    async def delete(self, file_id: str) -> None:
        await self._s.execute(
            sa_delete(SourceFileORM).where(SourceFileORM.file_id == file_id)
        )
        await self._s.flush()
