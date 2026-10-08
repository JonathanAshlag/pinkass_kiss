"""
Ingests an uploaded folder: (relative path, data) pairs, e.g. from a browser
`<input webkitdirectory>`, where every path starts with the picked folder's name
("docs/guide/setup.md"). Data is bytes or a readable binary file (an API upload's
spooled file), which is streamed in chunks rather than read whole.

The files are written into a temp directory that mirrors the uploaded tree, then the
regular folder walker runs over it -- so processors stay path-based (what PDF/docx
libraries want) and uploads behave exactly like `ingest_folder` on a local dir. The
temp dir is `tempfile`'s default (TMPDIR, else /tmp); on OpenShift mount an emptyDir
there (see deploy/openshift/upload-scratch.yaml).

Every path is validated before anything is written, so a bad upload never reaches the
filesystem: duplicate paths (compared case-insensitively, since two names that differ
only in case would overwrite each other on macOS and are almost always a mistake) and a
path that is both a file and a directory are UploadErrors. Hidden files are not
uploaded into the tree but reported in `skipped`, like unsupported extensions.
"""

import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from sqlalchemy.orm import Session

from kb.ingest.processors.base import ProcessorRegistry
from kb.ingest.folder import DEFAULT_BLOB_STORE, IngestFailed, IngestReport, ingest_folder
from kb.settings import get_settings
from kb.storage.blobs import BlobStore

UPLOAD_SCHEME = "upload:"
CHUNK_BYTES = 1024 * 1024


class UploadError(ValueError):
    """The upload itself is malformed (bad/unsafe paths, nothing uploaded)."""


class UploadTooLarge(UploadError):
    """The upload exceeds an UploadLimits cap (API: 413)."""


@dataclass(frozen=True)
class UploadLimits:
    """Caps on one upload; None = no cap. `from_settings` = the configured KB_UPLOAD_* caps."""

    max_files: int | None = None
    max_file_bytes: int | None = None
    max_total_bytes: int | None = None

    @classmethod
    def from_settings(cls) -> "UploadLimits":
        s = get_settings()
        return cls(s.upload_max_files, s.upload_max_file_bytes, s.upload_max_total_bytes)


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


def _check_collisions(paths: list[PurePosixPath]) -> None:
    """Duplicate paths (case-insensitively) and paths that are both a file and a dir."""
    seen: dict[str, PurePosixPath] = {}
    for path in paths:
        key = path.as_posix().casefold()
        if key in seen:
            raise UploadError(f"duplicate upload path: {seen[key].as_posix()!r} and {path.as_posix()!r}")
        seen[key] = path
    for path in paths:
        for parent in path.parents:
            other = seen.get(parent.as_posix().casefold())
            if other is not None:
                raise UploadError(f"{other.as_posix()!r} is both a file and a directory")


def _write(target: Path, data: bytes | BinaryIO, path: PurePosixPath, limits: UploadLimits, total: int) -> int:
    """Streams `data` to `target`, enforcing the byte caps as it goes; returns its size."""
    chunks = (data,) if isinstance(data, (bytes, bytearray)) else iter(lambda: data.read(CHUNK_BYTES), b"")
    size = 0
    with target.open("wb") as out:
        for chunk in chunks:
            size += len(chunk)
            if limits.max_file_bytes is not None and size > limits.max_file_bytes:
                raise UploadTooLarge(f"{path.as_posix()!r} is over the {limits.max_file_bytes}-byte limit per file")
            if limits.max_total_bytes is not None and total + size > limits.max_total_bytes:
                raise UploadTooLarge(f"upload is over the {limits.max_total_bytes}-byte total limit")
            out.write(chunk)
    return size


def ingest_upload(
    session: Session,
    files: Iterable[tuple[str, bytes | BinaryIO]],
    *,
    parent_id: uuid.UUID | None = None,
    registry: ProcessorRegistry | None = None,
    tags: list[str] | None = None,
    blob_store: BlobStore | None = DEFAULT_BLOB_STORE,
    limits: UploadLimits | None = None,
) -> IngestReport:
    """Either a folder or loose files. Paths must be relative and use `/`.

    - Folder: every path shares one top-level folder ("docs/a.md", "docs/b/c.pdf"); its
      name becomes a new root folder node under `parent_id`.
    - Loose files: every path is a bare file name ("report.pdf"); they're created
      directly in `parent_id`, which is required (files can't live at the KB root), and
      `root_id` is `parent_id`.

    Anything else, duplicate paths, or a path that's both a file and a directory raises
    UploadError; exceeding `limits` (default: none) raises UploadTooLarge. All or
    nothing, as `ingest_folder` (whose errors it raises, IngestFailed with upload paths).
    Report paths are the upload paths, e.g. "docs/logo.png"; hidden files ("docs/.env")
    are listed in `skipped`. `blob_store` as in `ingest_folder` (originals keep their
    upload file name, so the MIME type is right). Originals are uploaded from the temp
    dir before this returns, so the caller can commit after it's gone."""
    limits = limits or UploadLimits()
    files = [(_safe_relative(raw), data) for raw, data in files]
    if not files:
        raise UploadError("no files uploaded")
    if limits.max_files is not None and len(files) > limits.max_files:
        raise UploadTooLarge(f"{len(files)} files uploaded, the limit is {limits.max_files}")
    loose = all(len(path.parts) == 1 for path, _ in files)
    if loose:
        if parent_id is None:
            raise UploadError("loose files need a parent folder: files can't be at the KB root")
        root_name = ""
    else:
        tops = {path.parts[0] for path, _ in files}
        if len(tops) != 1 or any(len(path.parts) < 2 for path, _ in files):
            raise UploadError(
                "upload either one folder (paths 'folder/...') or loose files (bare file names)"
            )
        (root_name,) = tops
    _check_collisions([path for path, _ in files])

    # Hidden below the root, as the walker sees it (an uploaded folder may itself be hidden).
    def hidden(path: PurePosixPath) -> bool:
        return any(part.startswith(".") for part in path.parts[0 if loose else 1 :])

    with tempfile.TemporaryDirectory(prefix="kb-upload-") as tmp:
        base = Path(tmp).resolve()
        (base / root_name).mkdir(parents=True, exist_ok=True)
        total = 0
        for path, data in files:
            if hidden(path):
                continue
            target = base.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            total += _write(target, data, path, limits, total)

        try:
            report = ingest_folder(
                session,
                base / root_name,
                parent_id=parent_id,
                registry=registry,
                tags=tags,
                root_title=root_name or None,
                resource_for=lambda p: UPLOAD_SCHEME + p.relative_to(base).as_posix(),
                blob_store=blob_store,
                root_is_parent=loose,
            )
        except IngestFailed as exc:  # temp-dir paths mean nothing to the caller
            raise IngestFailed([(p.relative_to(base), err) for p, err in exc.failures]) from None
        report.skipped = [Path(path) for path, _ in files if hidden(path)] + [
            p.relative_to(base) for p in report.skipped
        ]
    return report
