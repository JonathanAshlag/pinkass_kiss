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
from kb.storage.models import Node


@dataclass
class PlannedNode:
    path: str  # "" is the plan's root; "guide/setup.md" is two levels below it
    type: str = "file"  # "file" | "folder"
    title: str | None = None  # default: the last path segment
    content: str | None = None  # required for files; folders have none
    # Other columns of `files` / `folders` (kind, aliases, description, tags, sources, ...).
    fields: dict[str, Any] = field(default_factory=dict)


def materialize(
    session: Session,
    plan: list[PlannedNode],
    *,
    parent_id: uuid.UUID | None = None,
    folder_fields: dict[str, Any] | None = None,
    root_is_parent: bool = False,
) -> dict[str, Node]:
    """
    Creates every planned node, plus any implied ancestor folders (with `folder_fields`),
    under `parent_id`, which must be an existing folder (None = the KB root). The plan
    must contain its root (""), which must be a folder; files need content, folders
    can't have any. Returns {path: node}, in creation order. Never commits.

    `root_is_parent`: the plan's root *is* `parent_id` (which must then be set) rather
    than a new folder under it, so the plan's top-level nodes land directly in
    `parent_id`. `{"": <that folder>}` is still in the result.
    """
    if root_is_parent and parent_id is None:
        raise ValueError("root_is_parent needs a parent_id")
    if parent_id is not None and service.get_folder(session, parent_id) is None:
        raise ValueError(f"no such folder: {parent_id}")
    planned: dict[str, PlannedNode] = {}
    for node in plan:
        if node.path in planned:
            raise ValueError(f"path planned twice: {node.path!r}")
        if node.type == "folder" and node.content is not None:
            raise ValueError(f"folders have no content: {node.path!r}")
        if node.type == "file" and node.content is None:
            raise ValueError(f"file has no content: {node.path!r}")
        planned[node.path] = node
    if "" not in planned:
        raise ValueError("plan has no root node (path '')")
    if planned[""].type != "folder":
        raise ValueError("the plan's root (path '') must be a folder")

    created: dict[str, Node] = {}

    def ensure(path: str) -> Node:
        if path in created:
            return created[path]
        if path == "" and root_is_parent:
            created[path] = service.get_folder(session, parent_id)
            return created[path]
        node = planned.get(path) or PlannedNode(path, "folder", fields=dict(folder_fields or {}))
        if path == "":
            node_parent_id = parent_id
        else:
            node_parent_id = ensure(path.rpartition("/")[0]).id
        title = node.title if node.title is not None else path.rpartition("/")[2]
        if node.type == "folder":
            created[path] = service.create_folder(
                session, parent_id=node_parent_id, title=title, **node.fields
            )
        else:
            created[path] = service.create_file(
                session, parent_id=node_parent_id, title=title, content=node.content, **node.fields
            )
        return created[path]

    for node in plan:
        ensure(node.path)
    return created
