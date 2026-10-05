"""
Evaluates the KB against a committed 20-paper QASPER slice (regenerate with `python
scripts/load_qasper.py --limit 20 --out tests/fixtures/qasper_20.jsonl`). No network needed.

The corpus is the papers, loaded through the real loader (`load_qasper.load_into_kb`)
into the folder layout documented there. Then:

- layout: each paper's tree mirrors its own outline (numbered section files/folders,
  figures/, tables/, metadata.md); the paper folder holds title, abstract and outline
- ingestion: no section text is lost, every section keeps its heading and a role tag
- manifests: one manifest per paper resolves to exactly that paper's subtree
- DCI grep: an annotator's evidence paragraph is found by a search of the paper's
  manifest (a deterministic stand-in for "can an agent find this with the tools"), never
  leaks outside the manifest, and the corpus-wide search sees a superset
- the REST API serves the same data
"""

import re
import uuid

import load_qasper

ROOT_TITLE = "QASPER"


def _path(paper: dict, rel: str = "") -> str:
    # mirrors kb.dci's path segment for a (unique-among-siblings) title
    base = f"{ROOT_TITLE}/{paper['title'].replace('/', '-').strip()}"
    return f"{base}/{rel}" if rel else base


def _subtree(session, folder_id) -> dict[str, object]:
    """{path relative to the paper folder: node}"""
    from kb import dal, service

    out = {}
    for node in dal.list_descendants(session, uuid.UUID(folder_id)):
        parts, cur = [node.title], node
        while cur.parent_id != uuid.UUID(folder_id):
            cur = service.get_node(session, cur.parent_id)
            parts.append(cur.title)
        out["/".join(reversed(parts))] = node
    return out


def _evidence(paper: dict) -> list[tuple[str, str]]:
    """(question id, evidence paragraph) for answerable questions' text evidence."""
    found = []
    for q in paper["questions"]:
        for a in q["answers"]:
            if a["unanswerable"]:
                continue
            for ev in a["evidence"]:
                if len(ev) > 40 and "FLOAT SELECTED" not in ev:
                    found.append((q["id"], ev.strip()))
    return found


def test_fixture_titles_are_unique(papers):
    titles = [p["title"].replace("/", "-").strip() for p in papers]
    assert len(set(titles)) == len(titles)  # keeps _path() valid


def test_layout_and_ingestion(session, papers, ids):
    for paper in papers:
        tree = _subtree(session, ids[paper["id"]])
        folder = service_node(session, ids[paper["id"]])
        expected = {n.path: n for n in load_qasper.paper_nodes(paper)}

        # the tree is exactly what paper_nodes describes, field for field
        assert set(tree) == set(expected) - {""}, paper["id"]
        for path, node in tree.items():
            want = expected[path]
            assert (node.kind, node.content, node.description) == (want.kind, want.content, want.fields["description"]), path
            assert node.tags == want.fields["tags"] and node.aliases == want.fields["aliases"], path

        # paper folder: title, abstract and outline; metadata.md points at arXiv
        assert folder.title == paper["title"]
        assert paper["abstract"] in folder.content and "## Outline" in folder.content
        assert f"arxiv.org/abs/{paper['id']}" in tree["metadata.md"].content

        # sections are numbered in reading order and carry their heading and a role
        sections = [p for p in expected if p[:1].isdigit()]  # paper_nodes yields reading order
        top = [p for p in sections if "/" not in p]
        assert top == sorted(top) and top[0].startswith("01-")  # so `ls` order is reading order
        for path in sections:
            assert tree[path].aliases and any(t.startswith("role:") for t in tree[path].tags), path

        # nothing lost: every section's text is stored inside one section node
        bodies = [tree[p].content or "" for p in sections]
        for name, text in paper["sections"]:
            if text.strip():
                assert any(text.strip() in b for b in bodies), (paper["id"], name)

        # one file per figure/table caption
        captions = [tree[p].content for p in tree if p.startswith(("figures/", "tables/")) and tree[p].kind == "file"]
        assert sorted(captions) == sorted(f["caption"] for f in paper["floats"])


def service_node(session, node_id: str):
    from kb import service

    return service.get_node(session, uuid.UUID(node_id))


def test_role_classification():
    c = load_qasper.classify_section
    assert c("Introduction") == "intro"
    assert c("Related Work") == "related"
    assert c("Conclusion and Future Work") == "conclusion"
    assert c("Main Results") == "results"
    assert c("Sentence Representation", parent_role="results") == "results"  # generic child inherits
    assert c("Training Details", parent_role="method") == "training"  # specific child wins
    assert c("Worker Adversarial") == "other"


def test_paper_manifest_resolves_to_its_subtree(session, papers, ids, manifests):
    from kb import service

    for paper in papers:
        resolved = {n.id for n in service.resolve_manifest(session, manifests[paper["id"]])}
        subtree = {n.id for n in _subtree(session, ids[paper["id"]]).values()}
        assert resolved == subtree | {uuid.UUID(ids[paper["id"]])}, paper["id"]


def test_corpus_manifest_resolves_to_everything(session, ids, manifests):
    from kb import service

    resolved = service.resolve_manifest(session, manifests["corpus"])
    assert len(resolved) > len(ids) * 5  # root + papers + folders + files


def test_grep_for_evidence_finds_it_in_the_paper(session, papers, manifests):
    from kb import service

    checked, failures = 0, []
    for paper in papers:
        scope = {_path(paper, n.path) for n in load_qasper.paper_nodes(paper)}
        for qid, ev in _evidence(paper):
            checked += 1
            pattern = re.escape(ev)
            scoped = service.search_lines(
                session, manifests[paper["id"]], pattern, files_only=True
            ).text.split("\n")
            hits = set(scoped) - {"(no matches)"}

            # scoping: a manifest search never leaks outside the manifest
            assert hits <= scope, qid
            if not hits:
                failures.append(qid)
                continue

            # corpus-wide search sees everything the scoped search saw
            wide = set(
                service.search_lines(session, manifests["corpus"], pattern, files_only=True).text.split("\n")
            )
            assert hits <= wide, qid

    assert checked > 50
    assert len(failures) / checked < 0.05, f"{len(failures)}/{checked} evidence paragraphs not found: {failures}"


def test_read_lines_returns_paper_overview(session, papers, manifests):
    from kb import service

    paper = papers[0]
    out = service.read_lines(session, manifests[paper["id"]], _path(paper))
    assert not out.truncated
    assert paper["abstract"] in out.text


def test_manifests_do_not_see_other_papers(session, papers, manifests):
    from kb import service

    a, b = papers[0], papers[1]
    listing = service.list_paths(session, manifests[a["id"]], recursive=True).text
    assert _path(a, "metadata.md") in listing
    assert _path(b, "metadata.md") not in listing


def test_api_serves_search_and_files(ids, papers, manifests):
    from fastapi.testclient import TestClient

    from kb.api import app

    client = TestClient(app)
    paper = papers[0]
    ev = _evidence(paper)[0][1]

    body = client.get(f"/files/{ids[paper['id']]}").json()
    assert body["title"] == paper["title"] and body["kind"] == "folder"

    r = client.get(
        f"/manifests/{manifests[paper['id']]}/search",
        params={"pattern": re.escape(ev), "files_only": True},
    )
    assert r.status_code == 200
    hits = set(r.json()["text"].split("\n"))
    assert hits & {_path(paper, n.path) for n in load_qasper.paper_nodes(paper)}


def test_agent_tools_return_text_and_report_errors(papers, manifests):
    from kb.agent_tools import AgentTools

    paper = papers[0]
    tools = AgentTools(manifests[paper["id"]])

    assert _path(paper, "metadata.md") in tools.list_paths(recursive=True)
    assert paper["abstract"] in tools.read_lines(_path(paper), limit=500)
    # bad agent input comes back as text the model can read, never as an exception
    assert tools.search_lines("(").startswith("error: invalid pattern")
    assert tools.read_lines("no/such/path").startswith("error: no such path")
    assert tools.read_lines(_path(paper), offset=0).startswith("error:")
    assert _path(papers[1], "metadata.md") not in tools.list_paths(recursive=True)  # scoped
