"""
Deterministic tests for the agent-facing DCI tools (`kb.service.list_paths` /
`search_lines` / `read_lines`, and their REST routes). Dataset-independent: a tiny
synthetic corpus is built through `kb.service`, so a failure here means the tools
broke, not that some dataset changed. No LLM, no network.

Corpus (under a uniquely-named root so it can't collide with other tests' data):

    <root>/
      alpha/            folder, no content
        notes.md        tags=[topic:x], description="First notes", 4 lines of body
        sub/
          deep.md       one "needle" line
      beta/
        other.md        one "needle" line
      Dup               two sibling files with the same title

Manifests: `alpha` (just the alpha folder) and `all` (the root).
"""

import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text


@pytest.fixture(scope="module")
def corpus(migrated_db):
    from kb import service
    from kb.storage.db import SessionLocal

    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as s:

        def folder(title, parent):
            return service.create_folder(s, parent_id=parent, title=title)

        def file(title, parent, content, **cols):
            return service.create_file(s, parent_id=parent, title=title, content=content, **cols)

        root = folder(f"tools-{tag}", None)
        alpha, beta = folder("alpha", root.id), folder("beta", root.id)
        sub = folder("sub", alpha.id)
        notes = file(
            "notes.md",
            alpha.id,
            "line one\nneedle in alpha\nline three\nNEEDLE shouting\n",
            tags=["topic:x"],
            description="First notes",
        )
        deep = file("deep.md", sub.id, "a deep needle")
        other = file("other.md", beta.id, "needle in beta")
        dup_a, dup_b = file("Dup", root.id, "first dup"), file("Dup", root.id, "second dup")

        m_alpha = service.create_manifest(s, f"tools-alpha-{tag}")
        service.add_manifest_member(s, m_alpha.id, node_id=alpha.id)
        m_all = service.create_manifest(s, f"tools-all-{tag}")
        service.add_manifest_member(s, m_all.id, node_id=root.id)
        s.commit()

        ns = SimpleNamespace(
            root=root.title, alpha=m_alpha.id, all=m_all.id,
            notes=notes.id, deep=deep.id, other=other.id, dups=(dup_a.id, dup_b.id),
        )

    yield ns

    with SessionLocal() as s:  # committed rows (the API needs them), so clean up by hand
        s.execute(text("DELETE FROM manifest_members WHERE manifest_id IN (:a, :b)"), {"a": ns.alpha, "b": ns.all})
        s.execute(text("DELETE FROM manifests WHERE id IN (:a, :b)"), {"a": ns.alpha, "b": ns.all})
        # files RESTRICT their folder, so: files in the subtree, then the folders themselves
        subtree = (
            "WITH RECURSIVE t AS (SELECT id FROM folders WHERE title = :t AND parent_id IS NULL"
            " UNION ALL SELECT f.id FROM folders f JOIN t ON f.parent_id = t.id)"
        )
        s.execute(text(f"{subtree} DELETE FROM files WHERE parent_id IN (SELECT id FROM t)"), {"t": ns.root})
        s.execute(text(f"{subtree} DELETE FROM folders WHERE id IN (SELECT id FROM t)"), {"t": ns.root})
        s.commit()


@pytest.fixture
def session(corpus):
    from kb.storage.db import SessionLocal

    with SessionLocal() as s:
        yield s


def _lines(out) -> list[str]:
    return out.text.split("\n")


# --------------------------------------------------------------------------
# list_paths
# --------------------------------------------------------------------------


def test_list_top_level_is_the_manifest_root(session, corpus):
    from kb import service

    out = service.list_paths(session, corpus.alpha)
    # alpha's parent (the root) is out of scope, so alpha itself is the top level
    assert _lines(out) == [f"{corpus.root}/alpha/"]  # folders carry no status
    assert not out.truncated


def test_list_under_shows_children_with_status_and_description(session, corpus):
    from kb import service

    out = service.list_paths(session, corpus.alpha, under=f"{corpus.root}/alpha")
    lines = _lines(out)
    assert any(l.startswith(f"{corpus.root}/alpha/notes.md  [") and l.endswith("First notes") for l in lines)
    assert f"{corpus.root}/alpha/sub/" in lines  # folders end in "/" and have no status
    assert not any("deep.md" in l for l in lines)  # not recursive


def test_list_recursive_includes_the_subtree(session, corpus):
    from kb import service

    text_ = service.list_paths(session, corpus.alpha, under=f"{corpus.root}/alpha", recursive=True).text
    assert f"{corpus.root}/alpha/sub/deep.md" in text_


def test_manifest_scope_hides_other_branches(session, corpus):
    from kb import service

    listing = service.list_paths(session, corpus.alpha, recursive=True).text
    assert "beta" not in listing and "other.md" not in listing
    assert "other.md" in service.list_paths(session, corpus.all, recursive=True).text


def test_colliding_sibling_titles_get_id_suffixes(session, corpus):
    from kb import service

    listing = service.list_paths(session, corpus.all, under=corpus.root).text
    for node_id in corpus.dups:
        assert f"{corpus.root}/Dup~{node_id.hex[:8]}" in listing


def test_soft_deleted_nodes_disappear(session, corpus):
    from kb import service

    service.delete_node(session, corpus.deep)
    try:
        assert "deep.md" not in service.list_paths(session, corpus.alpha, recursive=True).text
    finally:
        session.rollback()


def test_bad_path_error_suggests_the_closest_valid_paths(session, corpus):
    from kb import service

    def error(path):
        with pytest.raises(ValueError) as exc:
            service.read_lines(session, corpus.alpha, path)
        return str(exc.value)

    root = corpus.root
    # a shortened path (models cut titles at a ':' or a space) -> the full path
    assert f"did you mean: {root}/alpha" in error(f"{root}/alp")
    assert f"did you mean: {root}/alpha" in error(root.upper())  # case-insensitive, out-of-scope prefix
    # a guessed child of a real folder -> the deepest folder that does exist
    assert f"did you mean: {root}/alpha/sub)" in error(f"{root}/alpha/sub/nope.md")
    # nothing close: no hint, just the plain error
    assert error("zzz/qqq") == "no such path in manifest: zzz/qqq"


def test_list_unknown_manifest_and_path_raise(session, corpus):
    from kb import service

    with pytest.raises(ValueError):
        service.list_paths(session, uuid.uuid4())
    with pytest.raises(ValueError):
        service.list_paths(session, corpus.alpha, under=f"{corpus.root}/beta")  # outside the manifest


# --------------------------------------------------------------------------
# search_lines
# --------------------------------------------------------------------------


def test_search_hit_format_is_path_line_text(session, corpus):
    from kb import service

    out = service.search_lines(session, corpus.alpha, "needle in alpha")
    [hit] = _lines(out)
    path, lineno, line = hit.split(":", 2)
    assert path == f"{corpus.root}/alpha/notes.md" and line == "needle in alpha"
    # the reported line number is the one read_lines uses
    read = service.read_lines(session, corpus.alpha, path, offset=int(lineno), limit=1)
    assert _lines(read)[1] == f"{lineno}\tneedle in alpha"


def test_search_is_scoped_to_the_manifest(session, corpus):
    from kb import service

    scoped = service.search_lines(session, corpus.alpha, "needle", files_only=True).text
    wide = service.search_lines(session, corpus.all, "needle", files_only=True).text
    assert f"{corpus.root}/beta/other.md" not in scoped
    assert set(_lines(service.search_lines(session, corpus.alpha, "needle", files_only=True))) <= set(wide.split("\n"))
    assert f"{corpus.root}/beta/other.md" in wide


def test_search_case_sensitivity(session, corpus):
    from kb import service

    exact = service.search_lines(session, corpus.alpha, "needle", paths=[str(corpus.notes)]).text
    assert "shouting" not in exact
    folded = service.search_lines(session, corpus.alpha, "needle", paths=[str(corpus.notes)], ignore_case=True).text
    assert "shouting" in folded


def test_search_multiple_patterns_must_all_match_one_line(session, corpus):
    from kb import service

    assert "needle in alpha" in service.search_lines(session, corpus.alpha, ["needle", "alpha"]).text
    assert service.search_lines(session, corpus.alpha, ["needle", "beta"]).text == "(no matches)"


def test_search_paths_restricts_to_a_subtree(session, corpus):
    from kb import service

    out = service.search_lines(session, corpus.all, "needle", paths=[f"{corpus.root}/alpha/sub"], files_only=True)
    assert _lines(out) == [f"{corpus.root}/alpha/sub/deep.md"]


def test_search_context_lines(session, corpus):
    from kb import service

    out = service.search_lines(session, corpus.alpha, "needle in alpha", paths=[str(corpus.notes)], context=1)
    path = f"{corpus.root}/alpha/notes.md"
    assert any(l.startswith(f"{path}-") and l.endswith("line one") for l in _lines(out))  # "-" marks context
    assert any(l.startswith(f"{path}:") and l.endswith("needle in alpha") for l in _lines(out))  # ":" marks hits


def test_search_sees_frontmatter(session, corpus):
    from kb import service

    out = service.search_lines(session, corpus.alpha, "topic:x", files_only=True)
    assert _lines(out) == [f"{corpus.root}/alpha/notes.md"]


def test_search_no_match(session, corpus):
    from kb import service

    assert service.search_lines(session, corpus.all, "zzz-no-such-text").text == "(no matches)"


def test_search_bad_input(session, corpus):
    from kb import service

    with pytest.raises(service.PatternError):
        service.search_lines(session, corpus.all, "(unclosed")
    with pytest.raises(service.PatternError):
        service.search_lines(session, corpus.all, [])
    with pytest.raises(ValueError):
        service.search_lines(session, corpus.all, "x", context=-1)


# --------------------------------------------------------------------------
# read_lines
# --------------------------------------------------------------------------


def test_read_whole_file_has_header_frontmatter_and_numbered_lines(session, corpus):
    from kb import service

    out = service.read_lines(session, corpus.alpha, f"{corpus.root}/alpha/notes.md")
    lines = _lines(out)
    assert re.fullmatch(rf"{re.escape(corpus.root)}/alpha/notes\.md \(lines 1-\d+ of \d+\)", lines[0])
    assert lines[1] == "1\t---"  # frontmatter comes first
    assert any(l.endswith('"First notes"') for l in lines)
    assert "\tneedle in alpha" in out.text


def test_read_offset_and_limit(session, corpus):
    from kb import service

    full = _lines(service.read_lines(session, corpus.alpha, str(corpus.notes)))[1:]
    part = service.read_lines(session, corpus.alpha, str(corpus.notes), offset=3, limit=2)
    assert _lines(part)[1:] == full[2:4]
    assert "(lines 3-4 of " in _lines(part)[0]


def test_read_past_the_end_says_so(session, corpus):
    from kb import service

    assert "past the end" in service.read_lines(session, corpus.alpha, str(corpus.notes), offset=999).text


def test_read_accepts_a_uuid_and_rejects_out_of_scope(session, corpus):
    from kb import service

    assert service.read_lines(session, corpus.alpha, str(corpus.notes)).text.startswith(f"{corpus.root}/alpha/notes.md")
    with pytest.raises(ValueError):
        service.read_lines(session, corpus.alpha, str(corpus.other))  # in beta, outside the manifest


def test_read_bad_range(session, corpus):
    from kb import service

    with pytest.raises(ValueError):
        service.read_lines(session, corpus.alpha, str(corpus.notes), offset=0)
    with pytest.raises(ValueError):
        service.read_lines(session, corpus.alpha, str(corpus.notes), limit=0)


# --------------------------------------------------------------------------
# output bounding
# --------------------------------------------------------------------------


def test_outputs_are_capped_at_max_chars(session, corpus):
    from kb import service

    for out in (
        service.list_paths(session, corpus.all, recursive=True, max_chars=60),
        service.search_lines(session, corpus.all, ".", max_chars=60),
        service.read_lines(session, corpus.alpha, str(corpus.notes), max_chars=60),
    ):
        assert out.truncated and len(out.text) <= 60 and "[truncated at 60 chars" in out.text


def test_max_chars_out_of_range(session, corpus):
    from kb import service

    with pytest.raises(ValueError):
        service.list_paths(session, corpus.all, max_chars=0)


# --------------------------------------------------------------------------
# what gets loaded (#20: file text only where a tool needs it)
# --------------------------------------------------------------------------


@pytest.fixture
def content_selects(session):
    """The SELECTs on this session's connection that fetch `files.content`."""
    from sqlalchemy import event

    seen: list[str] = []

    def record(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT") and "files.content" in statement:
            seen.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine, "before_cursor_execute", record)


def test_list_paths_loads_no_file_text(session, corpus, content_selects):
    from kb import service

    service.list_paths(session, corpus.all, recursive=True)
    assert content_selects == []


def test_search_loads_no_file_text_into_python(session, corpus):
    from sqlalchemy import inspect

    from kb import service
    from kb.storage.models import File

    assert "needle" in service.search_lines(session, corpus.all, "needle").text
    assert all("content" in inspect(f).unloaded for f in session.identity_map.values() if isinstance(f, File))


# --------------------------------------------------------------------------
# the regex runs in Postgres (#27, #32)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern, ignore_case, expected",
    [
        (r"^a deep", False, True),  # ^ anchors per line
        (r"needle$", False, True),
        (r"\yneedle\y", False, True),
        (r"\yeedle", False, False),
        (r"needle(?= in beta)", False, True),
        (r"(?<=a )deep", False, True),
        (r"NEEDLE", True, True),
        (r"NEEDLE", False, True),  # notes.md has "NEEDLE shouting"
        (r"n\wedle", False, True),
        (r"ne{2}dle|zzz", False, True),
        (r"\d{20}", False, False),
    ],
)
def test_search_pattern_table(session, corpus, pattern, ignore_case, expected):
    from kb import service

    out = service.search_lines(session, corpus.all, pattern, ignore_case=ignore_case).text
    assert (out != "(no matches)") == expected


def test_search_hebrew(session, corpus):
    from kb import service
    from kb.storage import dal

    folder = dal.create_folder(session, parent_id=None, title="עברית")
    dal.create_file(session, parent_id=folder.id, title="בדיקה", content="שורה ראשונה\nשלום עולם, 123\nסוף")
    m = service.create_manifest(session, f"he-{uuid.uuid4().hex[:8]}")
    service.add_manifest_member(session, m.id, node_id=folder.id)

    hit = service.search_lines(session, m.id, r"\yעולם\y").text
    assert hit.endswith(":5:שלום עולם, 123") or ":שלום עולם, 123" in hit
    assert "עברית/בדיקה:" in hit  # Hebrew titles in paths
    assert ":" in service.search_lines(session, m.id, r"^[א-ת]+ [א-ת]+").text
    assert ":" in service.search_lines(session, m.id, r"^\w+ \w+, \d{3}$").text
    assert service.search_lines(session, m.id, "עולם", paths=["עברית"], files_only=True).text == "עברית/בדיקה"


def test_search_backspace_escape_is_rejected(session, corpus):
    from kb import service

    for p in (r"\bneedle", r"x\B", r"[\b]"):
        with pytest.raises(service.PatternError, match="word boundary"):
            service.search_lines(session, corpus.all, p)
    service.search_lines(session, corpus.all, r"\\bneedle")  # an escaped backslash is fine


def test_search_context_across_files_and_gaps(session, corpus):
    from kb import service

    out = service.search_lines(session, corpus.all, "needle", ignore_case=True, context=1).text
    assert out.count("--") >= 2 and not out.startswith("--") and not out.endswith("--")


def test_runaway_search_is_cut_off_and_the_session_survives(session, corpus, monkeypatch):
    from kb import service
    from kb.retrieval import dci
    from kb.storage import dal

    folder = dal.create_folder(session, parent_id=None, title="big")
    dal.create_file(session, parent_id=folder.id, title="big.md", content="a" * 20_000_000)
    manifest = service.create_manifest(session, f"big-{uuid.uuid4().hex[:8]}")
    service.add_manifest_member(session, manifest.id, node_id=folder.id)

    monkeypatch.setattr(dci, "_SEARCH_TIMEOUT_MS", 1)
    with pytest.raises(service.PatternError, match="too slow"):
        service.search_lines(session, manifest.id, r"(a*)*\1b")
    monkeypatch.undo()
    assert service.search_lines(session, manifest.id, "^id:", files_only=True).text  # still usable
    assert session.execute(text("SHOW statement_timeout")).scalar_one() != "1ms"


def test_too_complex_pattern_is_a_pattern_error(session, corpus):
    from kb import service

    with pytest.raises(service.PatternError, match="too complex"):
        service.search_lines(session, corpus.all, r"((a{200}){200}){200}")


# --------------------------------------------------------------------------
# agent-facing adapter (kb.retrieval.agent_tools)
# --------------------------------------------------------------------------


def test_agent_tools_treat_root_spellings_as_the_top_level(corpus):
    from kb.retrieval.agent_tools import AgentTools

    tools = AgentTools(corpus.alpha)
    top = tools.list_paths()
    assert top.startswith(f"{corpus.root}/alpha/")
    for spelling in (".", "./", "/", "", "  "):
        assert tools.list_paths(spelling) == top, spelling


def test_agent_tools_bad_path_is_an_error_string_with_a_hint(corpus):
    from kb.retrieval.agent_tools import AgentTools

    tools = AgentTools(corpus.alpha)
    out = tools.list_paths("no/such/place")
    assert out.startswith("error: no such path") and "no arguments" in out  # recoverable, not raised
    assert tools.read_lines("no/such/place").startswith("error:")
    assert tools.search_lines("(unclosed").startswith("error:")


def test_agent_tools_langchain_docstrings_explain_paths(corpus):
    from kb.retrieval.agent_tools import AgentTools

    described = {t.name: t.description for t in AgentTools(corpus.alpha).as_langchain()}
    assert set(described) == {"list_paths", "search_lines", "read_lines"}
    assert "no arguments" in described["list_paths"] and "copied exactly" in described["read_lines"]


# --------------------------------------------------------------------------
# REST routes
# --------------------------------------------------------------------------


def test_api_routes_mirror_the_service(corpus):
    from fastapi.testclient import TestClient

    from kb.api import app

    client = TestClient(app)
    base = f"/manifests/{corpus.alpha}"

    r = client.get(f"{base}/paths", params={"recursive": True})
    assert r.status_code == 200 and f"{corpus.root}/alpha/sub/deep.md" in r.json()["text"]

    r = client.get(f"{base}/search", params={"pattern": ["needle", "alpha"], "files_only": True})
    assert r.status_code == 200 and r.json()["text"] == f"{corpus.root}/alpha/notes.md"

    r = client.get(f"{base}/read", params={"path": str(corpus.notes), "offset": 2, "limit": 1})
    assert r.status_code == 200 and "(lines 2-2 of " in r.json()["text"] and r.json()["truncated"] is False


def test_api_error_mapping(corpus):
    from fastapi.testclient import TestClient

    from kb.api import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(f"/manifests/{uuid.uuid4()}/paths").status_code == 404
    assert client.get(f"/manifests/{corpus.alpha}/read", params={"path": str(corpus.other)}).status_code == 404
    assert client.get(f"/manifests/{corpus.alpha}/read", params={"path": "x", "offset": 0}).status_code == 422


def test_dev_ui_sanitizes_rendered_markdown():
    # #18: marked keeps raw HTML, so document content must go through DOMPurify before innerHTML
    from fastapi.testclient import TestClient

    from kb.api import app

    html = TestClient(app).get("/ui/").text
    assert "dompurify" in html and "DOMPurify.sanitize(marked.parse(md)" in html
