"""kb.policy: the folder/file kind and agent-lock permission table (pure, no DB)."""

import pytest

from kb.policy import Action, PermissionDenied, allowed, check
from kb.storage.models import File, Folder

A = Action
ALL = list(Action)
STRUCTURE = {A.RENAME, A.MOVE, A.DELETE}
LOCKS = {A.SET_KIND, A.SET_AGENT_LOCK}


def folder(kind="manual", agent_locked=False, title="f"):
    return Folder(kind=kind, agent_locked=agent_locked, title=title)


def file(kind="manual", agent_locked=False, title="doc"):
    return File(kind=kind, agent_locked=agent_locked, title=title, content="x")


def permitted(actor, node, ancestors=()):
    return {a for a in ALL if allowed(actor, a, node, ancestors)}


# --- folders ------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, human",
    [
        ("manual", set(ALL)),
        ("skeleton", set(ALL) - STRUCTURE),  # create inside / edit / unlock, but no rename/move/delete
        ("auto_updated", set()),  # the next job run would overwrite any change
    ],
)
def test_folder_kinds(kind, human):
    node = folder(kind)
    assert permitted("human", node) == human
    assert permitted("agent", node) == human - LOCKS  # agents never change kinds or locks


def test_skeleton_lock_covers_that_folder_only():
    skeleton = folder("skeleton")
    assert permitted("human", folder("manual"), [skeleton]) == set(ALL)
    assert permitted("human", file(), [skeleton]) == set(ALL) - {A.CREATE_INSIDE}


# --- files ----------------------------------------------------------------------


def test_files_cannot_contain_nodes():
    assert not allowed("human", A.CREATE_INSIDE, file(), [folder()])


@pytest.mark.parametrize(
    "file_kind, folder_kind, human",
    [
        ("manual", "manual", set(ALL) - {A.CREATE_INSIDE}),
        ("manual", "skeleton", set(ALL) - {A.CREATE_INSIDE}),
        ("manual", "auto_updated", set()),  # inside an auto-updated folder
        ("auto_updated", "manual", set()),
    ],
)
def test_file_kinds(file_kind, folder_kind, human):
    node, ancestors = file(file_kind), [folder(folder_kind)]
    assert permitted("human", node, ancestors) == human
    assert permitted("agent", node, ancestors) == human - LOCKS


# --- agent lock -------------------------------------------------------------------


AGENT_FILE = set(ALL) - LOCKS - {A.CREATE_INSIDE}  # what an agent may do to an unlocked file


@pytest.mark.parametrize("where", ["self", "parent"])
def test_agent_lock_covers_the_file_and_its_folder(where):
    node = file(agent_locked=where == "self")
    ancestors = [folder(agent_locked=where == "parent"), folder()]
    assert permitted("agent", node, ancestors) == set()
    assert permitted("human", node, ancestors) == set(ALL) - {A.CREATE_INSIDE}  # humans unaffected


def test_agent_lock_is_not_recursive():
    locked = folder(agent_locked=True)
    assert permitted("agent", file(), [folder(), locked]) == AGENT_FILE  # file in a sub-folder
    assert permitted("agent", folder(), [locked]) == set(ALL) - LOCKS  # the sub-folder itself


def test_agent_locked_folder_blocks_creating_inside():
    assert not allowed("agent", A.CREATE_INSIDE, folder(agent_locked=True))
    assert allowed("agent", A.CREATE_INSIDE, folder(), [folder(agent_locked=True)])
    assert allowed("agent", A.CREATE_INSIDE, folder())


def test_denial_message_names_the_reason():
    with pytest.raises(PermissionDenied, match="skeleton"):
        check("human", A.RENAME, folder("skeleton", title="Engineering"))
    with pytest.raises(PermissionDenied, match="folder 'Ops' is locked for agents"):
        check("agent", A.EDIT, file(), [folder(agent_locked=True, title="Ops"), folder()])
