from app.models.bundle import Bundle
from app.storage.base import BundleRepository


class MongoBundleRepository(BundleRepository):
    def __init__(self, db):
        self._db = db

    async def get(self, name: str) -> Bundle | None:
        doc = await self._db.bundles.find_one({"name": name})
        if not doc:
            return None
        doc.pop("_id", None)
        return Bundle(**doc)

    async def upsert(self, bundle: Bundle) -> None:
        doc = bundle.model_dump(mode="json")
        await self._db.bundles.replace_one({"name": bundle.name}, doc, upsert=True)

    async def list_all(self) -> list[Bundle]:
        bundles = []
        async for doc in self._db.bundles.find():
            doc.pop("_id", None)
            bundles.append(Bundle(**doc))
        return bundles
