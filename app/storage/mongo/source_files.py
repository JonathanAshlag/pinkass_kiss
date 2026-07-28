from __future__ import annotations

from app.storage.base import SourceFileRepository


class MongoSourceFileRepository(SourceFileRepository):
    def __init__(self, db) -> None:
        self._db = db

    async def create(
        self,
        file_id: str,
        filename: str,
        content_type: str,
        uploaded_by: str,
        extracted_text: str,
    ) -> None:
        await self._db.source_files.insert_one({
            "file_id": file_id,
            "filename": filename,
            "content_type": content_type,
            "uploaded_by": uploaded_by,
            "extracted_text": extracted_text,
            "generated_page_ids": [],
        })

    async def set_page_ids(self, file_id: str, page_ids: list[str]) -> None:
        await self._db.source_files.update_one(
            {"file_id": file_id},
            {"$set": {"generated_page_ids": page_ids}},
        )

    async def get_for_page(self, page_id: str) -> dict | None:
        return await self._db.source_files.find_one({"generated_page_ids": page_id})

    async def remove_page_id(self, file_id: str, page_id: str) -> list[str]:
        await self._db.source_files.update_one(
            {"file_id": file_id},
            {"$pull": {"generated_page_ids": page_id}},
        )
        doc = await self._db.source_files.find_one({"file_id": file_id})
        return doc["generated_page_ids"] if doc else []

    async def delete(self, file_id: str) -> None:
        await self._db.source_files.delete_one({"file_id": file_id})
