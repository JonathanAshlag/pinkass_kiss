"""
Ingests a local directory tree into the KB: directories become folder nodes, every file
a registered processor handles becomes a file node, everything else is skipped.

Two steps: `plan_folder` walks the directory and runs the processors (no DB), then
`kb.ingest.plan.materialize` creates the nodes. Format-agnostic -- which files are
supported is entirely the registry's business (see kb.ingest.processors.base). Never commits: the
caller owns the transaction, so one run is all-or-nothing.

Originals: for files whose processor sets `retain_original` (converted formats such as
PDF), the raw bytes go to a blob store (kb.storage.blobs) during planning and the node gets the
`blob_*` columns. That's external I/O but not DB I/O, and it's opt-in: `plan_folder`
retains nothing unless given a `blob_store`; `ingest_folder` defaults to the configured
one (`kb.storage.blobs.get_blob_store()`). Blobs are content-addressed, so a rolled-back ingest
only leaves harmless, dedupable objects behind.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.stores import ByteStore
from sqlalchemy.orm import Session

from kb.storage.blobs import get_blob_store, guess_mime, put_original
from kb.ingest.processors.base import ProcessorRegistry, default_registry
from kb.ingest.plan import PlannedNode, materialize


@dataclass
class FolderPlan:
    nodes: list[PlannedNode]  # the root, then one file node per processed file
    folder_fields: dict = field(default_factory=dict)  # for the implied directory folders
    skipped: list[Path] = field(default_factory=list)  # no processor for the extension
    failed: list[tuple[Path, str]] = field(default_factory=list)  # processor / blob store raised


# "Use the configured blob store" (vs. an explicit None = retain nothing).
DEFAULT_BLOB_STORE: Any = object()


@dataclass
class IngestReport:
    root_id: uuid.UUID
    files_created: list[uuid.UUID] = field(default_factory=list)
    folders_created: list[uuid.UUID] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)  # no processor for the extension
    failed: list[tuple[Path, str]] = field(default_factory=list)  # processor raised


def plan_folder(
    root: Path,
    *,
    registry: ProcessorRegistry | None = None,
    tags: list[str] | None = None,
    root_title: str | None = None,
    resource_for: Callable[[Path], str] | None = None,
    blob_store: ByteStore | None = None,
) -> FolderPlan:
    """
    The plan for ingesting `root`; reads files but never touches the DB. Directories
    aren't planned explicitly (except the root), so `materialize` only creates the ones
    that end up holding a file. Hidden entries and symlinks are ignored.

    `root_title` overrides the root folder node's title (default: the directory name).
    `resource_for` maps a file's path to its `sources[].resource` URI (default: its
    file:// URI) -- uploads use it so temp-dir paths don't end up in the KB.
    `blob_store`: where to keep originals of `retain_original` processors' files (None:
    don't keep them). A store error fails just that file.
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
                continue
            try:
                doc = processor.process(entry)
            except Exception as exc:  # noqa: BLE001 -- any processor error is per-file
                plan.failed.append((entry, f"{type(exc).__name__}: {exc}"))
                continue
            blob_fields: dict[str, Any] = {}
            if blob_store is not None and getattr(processor, "retain_original", False):
                try:
                    blob_fields = put_original(blob_store, entry.read_bytes(), guess_mime(entry.name))
                except Exception as exc:  # noqa: BLE001 -- a storage error is per-file too
                    plan.failed.append((entry, f"blob store: {type(exc).__name__}: {exc}"))
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
                        **blob_fields,
                    },
                )
            )

    walk(root)
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
    blob_store: ByteStore | None = DEFAULT_BLOB_STORE,
) -> IngestReport:
    """`plan_folder` + `materialize` under `parent_id` (an existing folder, or None for
    the KB root). `blob_store` defaults to `kb.storage.blobs.get_blob_store()`; pass None to keep
    no originals. See `plan_folder` for the other arguments."""
    if blob_store is DEFAULT_BLOB_STORE:
        blob_store = get_blob_store()
    plan = plan_folder(
        root,
        registry=registry,
        tags=tags,
        root_title=root_title,
        resource_for=resource_for,
        blob_store=blob_store,
    )
    created = materialize(session, plan.nodes, parent_id=parent_id, folder_fields=plan.folder_fields)
    return IngestReport(
        root_id=created[""].id,
        files_created=[n.id for n in created.values() if n.kind == "file"],
        folders_created=[n.id for n in created.values() if n.kind == "folder"],
        skipped=plan.skipped,
        failed=plan.failed,
    )
