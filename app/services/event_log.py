"""Fire-and-forget event log emission to Elasticsearch (audit + retrieval events, one index)."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Union

from app.config import settings
from app.infrastructure.elasticsearch import get_es
from app.models.audit import AuditLogEntry
from app.models.retrieval_log import RetrievalLogEntry

logger = logging.getLogger("pinkas.event_log")

EventEntry = Union[AuditLogEntry, RetrievalLogEntry]


def emit_audit_log(entry: AuditLogEntry) -> None:
    """Emit an audit log entry to Elasticsearch (fire-and-forget, no-op if ES disabled)."""
    _emit(entry, "audit")


def emit_retrieval_log(entry: RetrievalLogEntry) -> None:
    """Emit a retrieval log entry to Elasticsearch (fire-and-forget, no-op if ES disabled)."""
    _emit(entry, "retrieval")


def _emit(entry: EventEntry, kind: str) -> None:
    if not settings.es_audit_enabled:
        return
    es = get_es()
    if es is None:
        return
    try:
        asyncio.create_task(_write_to_es(entry, kind))
    except RuntimeError:
        logger.warning(f"Cannot emit {kind} log: no running event loop")


async def _write_to_es(entry: EventEntry, kind: str) -> None:
    """Write an event entry to the pinkas-events-{YYYY.MM} index, tagged with its kind."""
    try:
        es = get_es()
        if es is None:
            return
        index = f"pinkas-events-{datetime.now(timezone.utc):%Y.%m}"
        document = entry.model_dump(mode="json", exclude_none=True)
        document["event_kind"] = kind
        await es.index(index=index, document=document)
    except Exception as e:
        logger.error(f"Failed to write {kind} log: {e}", exc_info=True)
