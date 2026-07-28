"""Source-file lifecycle service."""
import logging

from bson import ObjectId

from app.infrastructure.mongo import get_gridfs
from app.storage.base import SourceFileRepository

logger = logging.getLogger("pinkas.source_files")


async def cleanup_source_file_for_page(
    page_id: str,
    source_file_repo: SourceFileRepository,
) -> None:
    """Remove page_id from its source file; delete the file when no pages remain."""
    record = await source_file_repo.get_for_page(page_id)
    if not record:
        return

    file_id = record["file_id"]
    remaining = await source_file_repo.remove_page_id(file_id, page_id)

    if remaining:
        return

    # Last page deleted — purge the raw binary from GridFS and the metadata record.
    try:
        await get_gridfs().delete(ObjectId(file_id))
    except Exception:
        logger.warning("GridFS delete failed for file_id=%s", file_id, exc_info=True)

    await source_file_repo.delete(file_id)
