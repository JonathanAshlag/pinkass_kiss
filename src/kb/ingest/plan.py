"""
A tree plan: the nodes a producer (folder walk, QASPER paper, ...) wants to create,
addressed by `/`-joined path relative to the plan's root. Producers build plans without
touching the DB; `materialize` is the one place that turns a plan into nodes.

Ancestors a plan doesn't list are implied: `materialize` creates them as plain folders
(title = path segment) only when something below them is created, so a producer that
lists files only never gets empty folders.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from kb import service
from kb.storage.models import File


@dataclass
class PlannedNode:
    path: str  # "" is the plan's root; "guide/setup.md" is two levels below it
    kind: str = "file"  # "file" | "folder"
    title: str | None = None  # default: the last path segment
    content: str | None = None
    # Other `files` columns (aliases, description, tags, sources, ...).
    fields: dict[str, Any] = field(default_factory=dict)


def materialize(
    session: Session,
    plan: list[PlannedNode],
    *,
    parent_id: uuid.UUID | None = None,
    folder_fields: dict[str, Any] | None = None,
) -> dict[str, File]:
    """
    Creates every planned node, plus any implied ancestor folders (with `folder_fields`),
    under `parent_id`, which must be an existing folder (None = the KB root). The plan
    must contain its root (""). Returns {path: node}, in creation order. Never commits.
    """
    if parent_id is not None:
        parent = service.get_node(session, parent_id)
        if parent is None or parent.kind != "folder":
            raise ValueError(f"no such folder: {parent_id}")
    planned: dict[str, PlannedNode] = {}
    for node in plan:
        if node.path in planned:
            raise ValueError(f"path planned twice: {node.path!r}")
        planned[node.path] = node
    if "" not in planned:
        raise ValueError("plan has no root node (path '')")

    created: dict[str, File] = {}

    def ensure(path: str) -> File:
        if path in created:
            return created[path]
        node = planned.get(path) or PlannedNode(path, "folder", fields=dict(folder_fields or {}))
        if path == "":
            node_parent_id = parent_id
        else:
            node_parent_id = ensure(path.rpartition("/")[0]).id
        created[path] = service.create_file(
            session,
            parent_id=node_parent_id,
            kind=node.kind,
            title=node.title if node.title is not None else path.rpartition("/")[2],
            content=node.content,
            **node.fields,
        )
        return created[path]

    for node in plan:
        ensure(node.path)
    return created
