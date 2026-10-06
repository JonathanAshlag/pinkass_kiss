"""
Ingests an uploaded folder: (relative path, bytes) pairs, e.g. from a browser
`<input webkitdirectory>`, where every path starts with the picked folder's name
("docs/guide/setup.md").

The files are written into a temp directory that mirrors the uploaded tree, then the
regular folder walker runs over it -- so processors stay path-based (what PDF/docx
libraries want) and uploads behave exactly like `ingest_folder` on a local dir.
"""

import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from langchain_core.stores import ByteStore
from sqlalchemy.orm import Session

from kb.ingest.processors.base import ProcessorRegistry
from kb.ingest.folder import DEFAULT_BLOB_STORE, IngestReport, ingest_folder

UPLOAD_SCHEME = "upload:"


class UploadError(ValueError):
    """The upload itself is malformed (bad/unsafe paths, nothing uploaded)."""


def _safe_relative(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise UploadError(f"unsafe upload path: {raw!r}")
    return path


def ingest_upload(
    session: Session,
    files: Iterable[tuple[str, bytes]],
    *,
    parent_id: uuid.UUID | None = None,
    registry: ProcessorRegistry | None = None,
    tags: list[str] | None = None,
    blob_store: ByteStore | None = DEFAULT_BLOB_STORE,
) -> IngestReport:
    """Paths must be relative, use `/`, and share one top-level folder (its name becomes
    the root folder node's title). Raises UploadError if not; report paths are
    relative to that folder's parent, e.g. "docs/logo.png". `blob_store` as in
    `ingest_folder` (originals keep their upload file name, so the MIME type is right)."""
    files = [(_safe_relative(raw), data) for raw, data in files]
    if not files:
        raise UploadError("no files uploaded")
    tops = {path.parts[0] for path, _ in files}
    if len(tops) != 1 or any(len(path.parts) < 2 for path, _ in files):
        raise UploadError("all uploaded paths must be inside one top-level folder")
    (root_name,) = tops

    with tempfile.TemporaryDirectory(prefix="kb-upload-") as tmp:
        base = Path(tmp).resolve()
        for path, data in files:
            target = base.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        report = ingest_folder(
            session,
            base / root_name,
            parent_id=parent_id,
            registry=registry,
            tags=tags,
            root_title=root_name,
            resource_for=lambda p: UPLOAD_SCHEME + p.relative_to(base).as_posix(),
            blob_store=blob_store,
        )
        # Temp-dir paths mean nothing to the caller; report upload-relative ones.
        report.skipped = [p.relative_to(base) for p in report.skipped]
        report.failed = [(p.relative_to(base), err) for p, err in report.failed]
    return report
