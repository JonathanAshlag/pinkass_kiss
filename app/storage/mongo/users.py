from __future__ import annotations

from typing import Optional

from app.models.user import User
from app.storage.base import UserRepository


class MongoUserRepository(UserRepository):
    def __init__(self, db) -> None:
        self._db = db

    async def get(self, user_id: str) -> Optional[User]:
        doc = await self._db.users.find_one({"user_id": user_id})
        if doc:
            doc.pop("_id", None)
            return User(**doc)
        return None

    async def create(self, user: User) -> None:
        await self._db.users.insert_one(user.model_dump(mode="json"))

    async def list_all(self) -> list[User]:
        users = []
        async for doc in self._db.users.find():
            doc.pop("_id", None)
            users.append(User(**doc))
        return users

    async def update_fields(self, user_id: str, fields: dict) -> None:
        await self._db.users.update_one({"user_id": user_id}, {"$set": fields})

    async def delete(self, user_id: str) -> bool:
        result = await self._db.users.delete_one({"user_id": user_id})
        return result.deleted_count > 0
