"""
Ingests a local directory tree into the KB: directories become folder nodes, every file
a registered processor handles becomes a file node, everything else is skipped.

Format-agnostic -- which files are supported is entirely the registry's business (see
kb.ingest.base). Goes through kb.service only, never kb.dal. Never commits: the caller
owns the transaction, so one run is all-or-nothing.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from kb import service
from kb.ingest.base import ProcessorRegistry, default_registry


@dataclass
class IngestReport:
    root_id: uuid.UUID
    files_created: list[uuid.UUID] = field(default_factory=list)
    folders_created: list[uuid.UUID] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)  # no processor for the extension
    failed: list[tuple[Path, str]] = field(default_factory=list)  # processor raised


def ingest_folder(
    session: Session,
    root: Path,
    *,
    parent_id: uuid.UUID | None = None,
    registry: ProcessorRegistry | None = None,
    tags: list[str] | None = None,
    root_title: str | None = None,
    resource_for: Callable[[Path], str] | None = None,
) -> IngestReport:
    """
    `root_title` overrides the root folder node's title (default: the directory name).
    `resource_for` maps a file's path to its `sources[].resource` URI (default: its
    file:// URI) -- uploads use it so temp-dir paths don't end up in the KB.
    """
    root = Path(root).resolve()  # so "." still gets a real title
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    if parent_id is not None:
        parent = service.get_node(session, parent_id)
        if parent is None or parent.kind != "folder":
            raise ValueError(f"no such folder: {parent_id}")
    registry = registry or default_registry()
    tags = list(tags or [])
    resource_for = resource_for or (lambda path: path.as_uri())

    root_node = service.create_file(
        session, parent_id=parent_id, kind="folder", title=root_title or root.name, tags=list(tags)
    )
    report = IngestReport(root_id=root_node.id, folders_created=[root_node.id])

    def walk(directory: Path, get_parent_id) -> None:
        # get_parent_id creates this directory's folder node on first call, so
        # directories with no supported files never get a node.
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                walk(entry, _lazy_folder(entry, get_parent_id))
                continue
            processor = registry.for_path(entry)
            if processor is None:
                report.skipped.append(entry)
                continue
            try:
                doc = processor.process(entry)
            except Exception as exc:  # noqa: BLE001 -- any processor error is per-file
                report.failed.append((entry, f"{type(exc).__name__}: {exc}"))
                continue
            node = service.create_file(
                session,
                parent_id=get_parent_id(),
                kind="file",
                title=doc.title,
                content=doc.content,
                tags=list(tags),
                sources=[{"resource": resource_for(entry)}],
                **doc.extra,
            )
            report.files_created.append(node.id)

    def _lazy_folder(directory: Path, get_grandparent_id):
        node_id: uuid.UUID | None = None

        def get() -> uuid.UUID:
            nonlocal node_id
            if node_id is None:
                node = service.create_file(
                    session,
                    parent_id=get_grandparent_id(),
                    kind="folder",
                    title=directory.name,
                    tags=list(tags),
                )
                node_id = node.id
                report.folders_created.append(node_id)
            return node_id

        return get

    walk(root, lambda: root_node.id)
    return report
