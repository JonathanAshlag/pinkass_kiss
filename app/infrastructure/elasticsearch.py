"""Elasticsearch async client lifecycle."""

from __future__ import annotations

from elasticsearch import AsyncElasticsearch

_es_client: AsyncElasticsearch | None = None


def init_es(hosts: list[str], **kwargs) -> None:
    global _es_client
    _es_client = AsyncElasticsearch(hosts=hosts, **kwargs)


async def close_es() -> None:
    global _es_client
    if _es_client:
        await _es_client.close()
        _es_client = None


def get_es() -> AsyncElasticsearch | None:
    return _es_client
