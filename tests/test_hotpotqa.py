"""
Evaluates the KB against a committed 100-question HotPotQA slice (distractor setting;
regenerate with `python scripts/load_hotpotqa.py --limit 100 --out
tests/fixtures/hotpotqa_100.jsonl`). No network needed.

The corpus is the questions' source paragraphs, loaded through the real loader
(`load_hotpotqa.load_into_kb`). Then:

- ingestion: every paragraph is stored verbatim
- manifests: one manifest per question resolves to exactly that question's 10 paragraphs
- DCI grep: searching a question's manifest for its (non yes/no) answer finds a gold
  supporting paragraph -- a deterministic stand-in for "can an agent answer this from
  the tools", with no LLM. The same search over the whole corpus must find a superset.
- the REST API serves the same data
"""

import re
import uuid

ROOT_TITLE = "HotPotQA"


def _path(title: str) -> str:
    # mirrors kb.dci's path segment for a (unique-among-siblings) title
    return f"{ROOT_TITLE}/{title.replace('/', '-').strip()}"


def test_ingestion_stores_every_paragraph_verbatim(session, examples, ids):
    from kb import service

    expected = {s["title"]: s["text"] for ex in examples for s in ex["sources"]}
    assert set(ids) == set(expected)
    for title, text in expected.items():
        node = service.get_node(session, uuid.UUID(ids[title]))
        assert node.kind == "file" and node.content == text


def test_question_manifest_resolves_to_its_sources(session, examples, ids, manifests):
    from kb import service

    for ex in examples:
        resolved = {str(n.id) for n in service.resolve_manifest(session, manifests[ex["id"]])}
        assert resolved == {ids[s["title"]] for s in ex["sources"]}, ex["id"]


def test_corpus_manifest_resolves_to_all_paragraphs_and_folder(session, ids, manifests):
    from kb import service

    resolved = service.resolve_manifest(session, manifests["corpus"])
    assert len(resolved) == len(ids) + 1  # the paragraphs plus the HotPotQA folder


def test_grep_for_answer_finds_a_gold_paragraph(session, examples, manifests):
    from kb import service

    failures = []
    checked = 0
    for ex in examples:
        if ex["answer"].lower() in ("yes", "no"):
            continue  # comparison questions: the answer string isn't in the text
        checked += 1
        pattern = re.escape(ex["answer"])
        scoped = service.search_lines(
            session, manifests[ex["id"]], pattern, ignore_case=True, files_only=True
        ).text.split("\n")
        scope_paths = {_path(s["title"]) for s in ex["sources"]}
        gold_paths = {_path(t) for t in ex["supporting_titles"]}

        # scoping: a manifest search never leaks outside the manifest
        assert set(scoped) <= scope_paths | {"(no matches)"}, ex["id"]
        if not gold_paths & set(scoped):
            failures.append(ex["id"])

        # corpus-wide search sees everything the scoped search saw
        wide = set(
            service.search_lines(
                session, manifests["corpus"], pattern, ignore_case=True, files_only=True
            ).text.split("\n")
        )
        assert set(scoped) - {"(no matches)"} <= wide, ex["id"]

    assert checked > 50
    assert not failures, f"{len(failures)}/{checked} answers not found in a gold paragraph: {failures}"


def test_read_lines_returns_paragraph_text(session, examples, manifests):
    from kb import service

    ex = examples[0]
    gold = next(s for s in ex["sources"] if s["title"] == ex["supporting_titles"][0])
    out = service.read_lines(session, manifests[ex["id"]], _path(gold["title"]))
    assert not out.truncated
    assert gold["text"] in out.text


def test_api_serves_search_and_files(ids, examples, manifests):
    from fastapi.testclient import TestClient

    from kb.api import app

    client = TestClient(app)
    ex = next(e for e in examples if e["answer"].lower() not in ("yes", "no"))
    title = ex["supporting_titles"][0]
    src = next(s for s in ex["sources"] if s["title"] == title)

    body = client.get(f"/files/{ids[title]}").json()
    assert body["content"] == src["text"] and body["title"] == title

    r = client.get(
        f"/manifests/{manifests[ex['id']]}/search",
        params={"pattern": re.escape(ex["answer"]), "ignore_case": True, "files_only": True},
    )
    assert r.status_code == 200
    hits = set(r.json()["text"].split("\n"))
    assert hits & {_path(t) for t in ex["supporting_titles"]}
