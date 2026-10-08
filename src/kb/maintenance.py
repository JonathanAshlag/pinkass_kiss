"""
Garbage collection for what a crashed write can leave behind (`scripts/gc.py`).

Writes are all or nothing with one commit point, the DB commit (see kb.service):
chunks and S3 originals are written before it and undone if it doesn't happen. A crash
(kill -9, OOM) between the two skips the undo. What it leaves is unreachable -- search
scopes to committed files, and only a committed row points at an original -- so
correctness never depends on this; it only reclaims space.

Both sweeps only touch things older than a grace period, so a write that is still in
flight (staged, not yet committed) is never collected.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from kb.storage.blobs import ORIGINALS_PREFIX, BlobStore
from kb.storage.db import SessionLocal
from kb.storage.models import File

log = logging.getLogger(__name__)

ORIGINALS_GRACE = timedelta(hours=24)
CHUNKS_GRACE = timedelta(hours=1)


@dataclass
class GcResult:
    originals: list[str] = field(default_factory=list)  # keys deleted (or that would be)
    chunk_files: list[uuid.UUID] = field(default_factory=list)  # file ids unindexed
    chunks: int = 0


def gc_originals(
    store: BlobStore, *, grace: timedelta = ORIGINALS_GRACE, dry_run: bool = False, now: datetime | None = None
) -> list[str]:
    """Deletes `originals/...` objects older than `grace` that no `files` row (deleted
    ones included: restoring a file must find its original) references."""
    cutoff = (now or datetime.now(timezone.utc)) - grace
    old = [key for key, modified in store.list_originals() if modified < cutoff]
    if not old:
        return []
    with SessionLocal() as s:
        kept = set(s.scalars(select(File.blob_key).where(File.blob_key.in_(old))))
    orphans = [key for key in old if key not in kept and key.startswith(ORIGINALS_PREFIX)]
    if orphans and not dry_run:
        store.delete_originals(orphans)
    return orphans


def gc_chunks(*, grace: timedelta = CHUNKS_GRACE, dry_run: bool = False, store=None) -> tuple[list[uuid.UUID], int]:
    """Unindexes record-manager groups (files) last written more than `grace` ago that
    have no `files` row: chunks staged by a write that never committed. Returns (file
    ids, chunks removed). Soft-deleted files keep their chunks here (search excludes
    them; the post-commit cleanup or reindex drops them)."""
    from kb.semantic_index.indexer import _unindex
    from kb.semantic_index.vectorstore import get_index_store

    store = store or get_index_store()
    rm = store.record_manager
    cutoff = rm.get_time() - grace.total_seconds()
    with rm.engine.connect() as conn:
        groups = conn.execute(
            text(
                "SELECT DISTINCT r.group_id FROM upsertion_record r"
                " WHERE r.namespace = :ns AND r.group_id IS NOT NULL"
                " GROUP BY r.group_id HAVING max(r.updated_at) < :cutoff"
                " AND NOT EXISTS (SELECT 1 FROM files f WHERE f.id::text = r.group_id)"
            ),
            {"ns": rm.namespace, "cutoff": cutoff},
        ).scalars().all()
    ids = [uuid.UUID(g) for g in groups]
    if dry_run or not ids:
        return ids, 0
    return ids, _unindex(store, ids)


def collect(store: BlobStore | None, *, dry_run: bool = False) -> GcResult:
    """Both sweeps (originals only when a blob store is configured)."""
    result = GcResult()
    if store is not None:
        result.originals = gc_originals(store, dry_run=dry_run)
    result.chunk_files, result.chunks = gc_chunks(dry_run=dry_run)
    return result
