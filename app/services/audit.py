"""Fire-and-forget audit log emission to Elasticsearch."""

import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.infrastructure.elasticsearch import get_es
from app.models.audit import AuditLogEntry

logger = logging.getLogger("pinkas.audit")


def emit_audit_log(entry: AuditLogEntry) -> None:
    if not settings.es_audit_enabled:
        return
    es = get_es()
    if es is None:
        return
    try:
        asyncio.create_task(_write_to_es(entry))
    except RuntimeError:
        logger.warning("Cannot emit audit log: no running event loop")


async def _write_to_es(entry: AuditLogEntry) -> None:
    try:
        es = get_es()
        if es is None:
            return
        index = f"pinkas-audit-{datetime.now(timezone.utc):%Y.%m}"
        await es.index(index=index, document=entry.model_dump(mode="json"))
    except Exception as e:
        logger.error(f"Failed to write audit log: {e}", exc_info=True)
