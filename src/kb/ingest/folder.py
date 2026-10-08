"""
Ingests a local directory tree into the KB: directories become folder nodes, every file
a registered processor handles becomes a file node, everything else is skipped.

Two steps: `plan_folder` walks the directory and runs the processors (no DB, no
network), then `ingest_folder` creates the nodes -- all of them or none: if any file
fails to convert, `IngestFailed` lists every failure and nothing is written anywhere.
Format-agnostic -- which files are supported is entirely the registry's business (see
kb.ingest.processors.base). Never commits: the caller owns the transaction.

Originals: for files whose processor sets `retain_original` (converted formats such as
PDF), the plan records the source file (`PlannedNode.original`, hashed while planning).
`ingest_folder` uploads them to the blob store (kb.storage.blobs, default: the configured
one; None keeps none) under `originals/<file id>` and sets the `blob_*` columns. Indexing
and uploads happen in `kb.service.stage`, before the transaction creates any row; the
commit makes them reachable, a rollback undoes them.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from kb import service
from kb.storage.blobs import BlobStore, Original, get_blob_store, original_key
from kb.ingest.convert import convert_all
from kb.ingest.processors.base import Processor, ProcessorRegistry, default_registry
from kb.ingest.plan import PlannedNode, materialize, transient_files
from kb.storage.models import Folder


@dataclass
class FolderPlan:
    nodes: list[PlannedNode]  # the root, then one file node per processed file
    folder_fields: dict = field(default_factory=dict)  # for the implied directory folders
    skipped: list[Path] = field(default_factory=list)  # no processor for the extension
    failed: list[tuple[Path, str]] = field(default_factory=list)  # processor raised


class IngestFailed(ValueError):
    """Some files couldn't be converted, so nothing was ingested. `failures` lists every
    one of them as (path, error) -- not just the first -- so they can all be fixed."""

    def __init__(self, failures: list[tuple[Path, str]]):
        self.failures = list(failures)
        first = ", ".join(f"{p}: {e}" for p, e in self.failures[:3])
        more = f" (+{len(self.failures) - 3} more)" if len(self.failures) > 3 else ""
        super().__init__(f"{len(self.failures)} file(s) failed to convert, nothing was ingested: {first}{more}")


# "Use the configured blob store" (vs. an explicit None = retain nothing).
DEFAULT_BLOB_STORE: Any = object()


@dataclass
class IngestReport:
    root_id: uuid.UUID
    files_created: list[uuid.UUID] = field(default_factory=list)
    folders_created: list[uuid.UUID] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)  # no processor for the extension


def plan_folder(
    root: Path,
    *,
    registry: ProcessorRegistry | None = None,
    tags: list[str] | None = None,
    root_title: str | None = None,
    resource_for: Callable[[Path], str] | None = None,
) -> FolderPlan:
    """
    The plan for ingesting `root`; reads files but never touches the DB. Directories
    aren't planned explicitly (except the root), so `materialize` only creates the ones
    that end up holding a file. Hidden entries and symlinks are ignored.

    `root_title` overrides the root folder node's title (default: the directory name).
    `resource_for` maps a file's path to its `sources[].resource` URI (default: its
    file:// URI) -- uploads use it so temp-dir paths don't end up in the KB.
    Every file gets its id up front; `retain_original` processors' files also get an
    `original` (path, MIME type, size, SHA-256). Files are converted in parallel across
    processes where that pays (kb.ingest.convert), the plan is in walk order regardless.
    """
    root = Path(root).resolve()  # so "." still gets a real title
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    registry = registry or default_registry()
    tags = list(tags or [])
    resource_for = resource_for or (lambda path: path.as_uri())

    plan = FolderPlan(
        nodes=[PlannedNode("", "folder", title=root_title or root.name, fields={"tags": list(tags)})],
        folder_fields={"tags": list(tags)},
    )

    found: list[tuple[Path, Processor]] = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                walk(entry)
                continue
            processor = registry.for_path(entry)
            if processor is None:
                plan.skipped.append(entry)
            else:
                found.append((entry, processor))

    walk(root)
    for (entry, processor), doc in zip(found, convert_all(found)):
        if isinstance(doc, str):
            plan.failed.append((entry, doc))
            continue
        original = None
        if getattr(processor, "retain_original", False):
            try:
                original = Original.of(entry)
            except OSError as exc:
                plan.failed.append((entry, f"reading original: {type(exc).__name__}: {exc}"))
                continue
        plan.nodes.append(
            PlannedNode(
                entry.relative_to(root).as_posix(),
                "file",
                title=doc.title,
                content=doc.content,
                fields={
                    "tags": list(tags),
                    "sources": [{"resource": resource_for(entry)}],
                    **doc.extra,
                },
                id=uuid.uuid4(),
                original=original,
            )
        )
    return plan


def ingest_folder(
    session: Session,
    root: Path,
    *,
    parent_id: uuid.UUID | None = None,
    registry: ProcessorRegistry | None = None,
    tags: list[str] | None = None,
    root_title: str | None = None,
    resource_for: Callable[[Path], str] | None = None,
    blob_store: BlobStore | None = DEFAULT_BLOB_STORE,
    root_is_parent: bool = False,
) -> IngestReport:
    """`plan_folder` + `materialize` under `parent_id` (an existing folder, or None for
    the KB root), all or nothing: raises `IngestFailed` (listing every file that failed
    to convert) before writing anything, and `kb.service.stage` indexes the files and
    uploads their originals before any row is created (IndexingError / BlobStoreError,
    undone, if that fails). `blob_store` defaults to `kb.storage.blobs.get_blob_store()`;
    pass None to keep no originals. `root_is_parent`: put `root`'s contents straight
    into `parent_id` instead of a new folder for `root` (then `root_id` is `parent_id`).
    See `plan_folder` for the other arguments."""
    if blob_store is DEFAULT_BLOB_STORE:
        blob_store = get_blob_store()
    if parent_id is not None and service.get_folder(session, parent_id) is None:
        raise ValueError(f"no such folder: {parent_id}")  # before any embedding/upload
    plan = plan_folder(
        root,
        registry=registry,
        tags=tags,
        root_title=root_title,
        resource_for=resource_for,
    )
    if plan.failed:
        raise IngestFailed(plan.failed)
    originals = []
    for node in plan.nodes:
        if node.original is not None and blob_store is not None:
            key = original_key(node.id)
            node.fields.update(node.original.columns(key))
            originals.append((blob_store, key, node.original))
    service.stage(session, files=transient_files(plan.nodes), originals=originals)
    created = materialize(
        session,
        plan.nodes,
        parent_id=parent_id,
        folder_fields=plan.folder_fields,
        root_is_parent=root_is_parent,
    )
    new = [n for path, n in created.items() if not (root_is_parent and path == "")]
    return IngestReport(
        root_id=created[""].id,
        files_created=[n.id for n in new if not isinstance(n, Folder)],
        folders_created=[n.id for n in new if isinstance(n, Folder)],
        skipped=plan.skipped,
    )
