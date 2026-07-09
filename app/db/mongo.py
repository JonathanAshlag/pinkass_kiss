from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from app.config import settings

_client: AsyncIOMotorClient | None = None


def init_client(uri: str | None = None, db_name: str | None = None) -> None:
    global _client
    _client = AsyncIOMotorClient(uri or settings.mongo_uri)


def close_client() -> None:
    global _client
    if _client:
        _client.close()
        _client = None


def get_db():
    if _client is None:
        init_client()
    return _client[settings.mongo_db]


def get_gridfs():
    return AsyncIOMotorGridFSBucket(get_db())
