"""
Who may do what to a node: the permission rules behind folder/file kinds and the
agent lock. Pure -- no Session, no I/O. kb.service loads the node and its ancestors
and calls `check` before every mutation.

| Folder kind  | Create inside, edit | Rename, move, delete |
| ------------ | ------------------- | -------------------- |
| skeleton     | yes                 | locked               |
| manual       | yes                 | yes                  |
| auto_updated | locked              | locked               |

- A skeleton's lock covers that folder only: what's inside it (manual sub-folders,
  files) can be renamed, moved and deleted. The way to change a skeleton is to switch
  its kind to manual first (SET_KIND).
- Files are `manual` or `auto_updated`. A file is locked if it, or the folder it sits
  in, is auto_updated (the next job run would overwrite any change).
- Agents get the same rules, plus: a node with `agent_locked` set is read-only for
  them, and so is a file whose own folder is agent-locked. The lock is not recursive:
  sub-folders of a locked folder, and everything below them, stay open. A locked
  folder also can't have things created in (or moved into) it by agents. Agents can
  never change a kind or a lock.

auto_updated only exists for future auto-update jobs; kb.service refuses to set it.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from kb.storage.models import File, Folder, Node

Actor = Literal["human", "agent"]


class Action(StrEnum):
    CREATE_INSIDE = "create inside"  # target: the folder something is created/moved/restored into
    EDIT = "edit"  # file content or metadata; folder description/tags
    RENAME = "rename"
    MOVE = "move"
    DELETE = "delete"
    SET_KIND = "change the kind of"
    SET_AGENT_LOCK = "change the agent lock of"


class PermissionDenied(Exception):
    """The actor may not perform the action on the node. Deliberately not a ValueError
    (which the API maps to 404)."""


_STRUCTURE = {Action.RENAME, Action.MOVE, Action.DELETE}


def check(actor: Actor, action: Action, node: Node, ancestors: Sequence[Folder] = ()) -> None:
    """Raises PermissionDenied unless `actor` may do `action` to `node`. `ancestors` are
    the folders above `node`, nearest first (kb.storage.dal.list_ancestors)."""
    label = f"{type(node).__name__.lower()} {node.title!r}"

    def deny(reason: str) -> None:
        raise PermissionDenied(f"cannot {action} {label}: {reason}")

    if actor == "agent":
        if action in (Action.SET_KIND, Action.SET_AGENT_LOCK):
            deny("agents cannot change kinds or locks")
        if node.agent_locked:
            deny("it is locked for agents")
        if isinstance(node, File) and ancestors and ancestors[0].agent_locked:
            deny(f"its folder {ancestors[0].title!r} is locked for agents")

    if isinstance(node, Folder):
        if node.kind == "auto_updated":
            deny("it is auto-updated")
        if node.kind == "skeleton" and action in _STRUCTURE:
            deny("it is a skeleton folder (change its kind to manual first)")
    elif isinstance(node, File):
        if action == Action.CREATE_INSIDE:
            deny("files cannot contain other nodes")
        if node.kind == "auto_updated":
            deny("it is auto-updated")
        if ancestors and ancestors[0].kind == "auto_updated":
            deny(f"its folder {ancestors[0].title!r} is auto-updated")


def allowed(actor: Actor, action: Action, node: Node, ancestors: Sequence[Folder] = ()) -> bool:
    try:
        check(actor, action, node, ancestors)
    except PermissionDenied:
        return False
    return True
