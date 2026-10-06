"""
The virtual-file contract (`kb.okf`): one rendering of a node (frontmatter block +
body) whose line numbers DCI's `search_lines`/`read_lines` and the semantic index's
chunk `start_line`/`end_line` all share. Pure: model objects are built in memory, no DB.
"""

import uuid
from datetime import datetime, timezone

from kb.okf import (
    body_line_offset,
    render_frontmatter,
    render_virtual_file,
    virtual_file_body,
)
from kb.storage.models import File, Folder

FILE_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
FOLDER_ID = uuid.UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")


def _file(**overrides) -> File:
    fields = dict(
        id=FILE_ID,
        parent_id=FOLDER_ID,
        kind="manual",
        title="Methods",
        status="draft",
        content="# Methods\n\nWe did things.\nThen more.",
    )
    fields.update(overrides)
    return File(**fields)


def _folder(**overrides) -> Folder:
    fields = dict(id=FOLDER_ID, kind="skeleton", title="Paper")
    fields.update(overrides)
    return Folder(**fields)


def test_golden_minimal_file():
    assert render_virtual_file(_file()) == (
        "---\n"
        'id: "11111111-2222-3333-4444-555555555555"\n'
        'type: "file"\n'
        'kind: "manual"\n'
        'title: "Methods"\n'
        'status: "draft"\n'
        "---\n"
        "# Methods\n\nWe did things.\nThen more."
    )


def test_golden_full_file():
    node = _file(
        status="stable",
        aliases=["Approach"],
        description="How it was done — briefly",
        tags=["role:method", "x"],
        stale_after=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        sources=[{"id": "s1", "resource": "okf://a"}],
    )
    assert render_virtual_file(node) == (
        "---\n"
        'id: "11111111-2222-3333-4444-555555555555"\n'
        'type: "file"\n'
        'kind: "manual"\n'
        'title: "Methods"\n'
        'status: "stable"\n'
        'aliases: ["Approach"]\n'
        'description: "How it was done — briefly"\n'
        'tags: ["role:method", "x"]\n'
        'stale_after: "2026-01-02T03:04:05+00:00"\n'
        'sources: [{"id": "s1", "resource": "okf://a"}]\n'
        "---\n"
        "# Methods\n\nWe did things.\nThen more."
    )


def test_golden_folder_has_empty_body():
    node = _folder(description="A paper", tags=["qasper"])
    assert virtual_file_body(node) == ""
    assert render_virtual_file(node) == (
        "---\n"
        'id: "66666666-7777-8888-9999-aaaaaaaaaaaa"\n'
        'type: "folder"\n'
        'kind: "skeleton"\n'
        'title: "Paper"\n'
        'description: "A paper"\n'
        'tags: ["qasper"]\n'
        "---\n"
    )
    assert render_virtual_file(node) == render_frontmatter(node)


def test_render_is_frontmatter_then_body():
    node = _file(tags=["a"])
    assert render_virtual_file(node) == render_frontmatter(node) + node.content
    assert render_frontmatter(node).endswith("---\n")


def test_body_starts_at_offset_plus_one():
    for node in (_file(), _file(tags=["a"], aliases=["b"], description="d")):
        lines = render_virtual_file(node).split("\n")
        offset = body_line_offset(node)
        body = node.content.split("\n")
        # body line i (1-based) is virtual-file line offset + i
        for i, text in enumerate(body, start=1):
            assert lines[offset + i - 1] == text
        assert len(lines) == offset + len(body)
        assert lines[offset - 1] == "---"


def test_offset_counts_frontmatter_lines():
    # ---, id, type, kind, title, status, --- = 7 lines before the body
    assert body_line_offset(_file()) == 7


def test_offset_shifts_when_optional_field_appears():
    untagged = body_line_offset(_file(tags=[]))
    tagged = body_line_offset(_file(tags=["a"]))
    assert tagged == untagged + 1
    # retagging a file that already has tags doesn't move the body
    assert body_line_offset(_file(tags=["a", "b", "c"])) == tagged
    assert body_line_offset(_file(tags=["a"], description="d")) == tagged + 1


def test_offset_for_folder_covers_whole_file():
    node = _folder()
    assert body_line_offset(node) == render_virtual_file(node).count("\n")


def test_empty_content_file():
    node = _file(content="")
    assert render_virtual_file(node) == render_frontmatter(node)
    assert body_line_offset(node) == render_virtual_file(node).count("\n")
