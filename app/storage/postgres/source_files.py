"""PostgreSQL implementation of SourceFileRepository."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

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
