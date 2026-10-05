"""
Loads QASPER (questions about NLP papers) from the HuggingFace hub.

Each record is one paper: id (an arXiv id), title, abstract, `sections` ([name, text]
pairs of the full text; nested sections are named "Parent ::: Child"), `floats`
(figures/tables: {file, caption}) and `questions` (id, question, and the gold `answers`
from every annotator: unanswerable / yes_no / free_form_answer / extractive_spans /
evidence).

With --to-kb, the papers are loaded into the knowledge base: a root "QASPER" folder,
and under it one folder per paper that mirrors the paper's own outline:

    <paper title>/            content: title, abstract, outline; description: abstract's first sentence
    ├── metadata.md           id, arXiv link, counts
    ├── 01-introduction.md
    ├── 02-related-work.md
    ├── 03-approach/          a section with subsections; content: its lead text, if any
    │   ├── 01-masked-and-translation-language-model-pretraining.md
    │   └── 02-transfer-protocol.md
    ├── ...
    ├── figures/figure-1.md   one file per caption
    └── tables/table-1.md

Number prefixes keep reading order under `ls`. Every section node keeps its original
heading as an alias and gets a `role:<role>` tag (intro / related / method / training
/ setup / baselines / results / conclusion / other) from keyword heuristics on the
heading (`classify_section`; subsections inherit a parent's role when their own heading
is unspecific) -- the role is a label only, it never moves text.
Questions and answers are never stored in the KB.

Needs DATABASE_URL for --to-kb (see .env.example).

Usage:
    pip install datasets
    python scripts/load_qasper.py --split validation --limit 20 --out tests/fixtures/qasper_20.jsonl
    python scripts/load_qasper.py --to-kb --limit 20 [--index]

--index also embeds the loaded papers into the semantic index (kb_chunks); it needs
the embeddings model (EMBEDDINGS_MODEL) reachable. Off by default.

Importable too: `from load_qasper import load_qasper`.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # HF_TOKEN, DATABASE_URL (for --to-kb)

from kb.ingest.plan import PlannedNode  # noqa: E402 -- after load_dotenv: kb.db reads DATABASE_URL on import

ROOT_TITLE = "QASPER"
TAG = "qasper"

# (regex on the lowercased heading, role); first match wins, so specific topics come
# before the generic "method" bucket.
ROLE_RULES: list[tuple[str, str]] = [
    (r"introduc|overview|motivation", "intro"),
    (r"related|prior work|previous work|literature|background", "related"),
    (r"conclu|future work|summary|acknowledg|limitation|appendix", "conclusion"),
    (r"baseline|compar", "baselines"),
    (r"train|optimi[sz]|loss|learning|fine-?tun|pre-?train", "training"),
    (r"result|evaluat|analysis|ablation|discussion|finding|error|performance|case stud", "results"),
    (
        r"experiment|setup|set-up|setting|implement|dataset|data\b|corpus|corpora|"
        r"hyper-?param|preprocess|metric|configur|annotat",
        "setup",
    ),
    (
        r"model|approach|method|architecture|system|framework|network|algorithm|"
        r"feature|problem|formulation|task|definition|encoder|decoder|representation",
        "method",
    ),
]


def classify_section(heading: str, parent_role: str = "other") -> str:
    """The role of one section heading ("Main Results" -> "results"). A subsection whose
    own heading is unmatched or only generically "method"-like inherits its parent's
    role, so "Analysis ::: Sentence Representation" stays "results"."""
    role = next((r for pattern, r in ROLE_RULES if re.search(pattern, heading.lower())), "other")
    if role in ("other", "method") and parent_role != "other":
        return parent_role
    return role


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "section"


def first_sentence(text: str, max_len: int = 200) -> str:
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return sentence if len(sentence) <= max_len else sentence[: max_len - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------------


def load_qasper(split: str = "validation", limit: int | None = None) -> Iterator[dict]:
    """Yield QASPER papers. split: "train" / "validation" / "test"."""
    from datasets import load_dataset

    ds = load_dataset("allenai/qasper", split=split)
    for i, row in enumerate(ds):
        if limit is not None and i >= limit:
            break
        ft = row["full_text"]
        qas = row["qas"]
        fts = row["figures_and_tables"]
        yield {
            "id": row["id"],
            "title": row["title"],
            "abstract": (row["abstract"] or "").strip(),
            "sections": [
                [name or "", "\n\n".join(p.strip() for p in paras if p.strip())]
                for name, paras in zip(ft["section_name"], ft["paragraphs"])
            ],
            "floats": [
                {"file": f, "caption": c.strip()}
                for f, c in zip(fts["file"], fts["caption"])
                if c.strip()
            ],
            "questions": [
                {
                    "id": qid,
                    "question": question,
                    "answers": [
                        {
                            "unanswerable": a["unanswerable"],
                            "yes_no": a["yes_no"],
                            "free_form_answer": a["free_form_answer"],
                            "extractive_spans": a["extractive_spans"],
                            "evidence": a["evidence"],
                        }
                        for a in answers["answer"]
                    ],
                }
                for qid, question, answers in zip(qas["question_id"], qas["question"], qas["answers"])
            ],
        }


# --------------------------------------------------------------------------
# Paper -> nodes (pure; no DB)
# --------------------------------------------------------------------------


def _node(
    path: str,
    kind: str,
    content: str | None = None,
    description: str | None = None,
    aliases: list[str] | None = None,
    tags: list[str] | None = None,
    sources: list[dict] | None = None,
    title: str | None = None,
) -> PlannedNode:
    """One planned node under the paper folder ("" is the folder itself), with every
    frontmatter column the loader sets spelled out."""
    fields = {"description": description, "aliases": aliases or [], "tags": tags or [], "sources": sources or []}
    return PlannedNode(path, kind, title=title, content=content, fields=fields)


@dataclass
class _Section:
    heading: str
    texts: list[str] = field(default_factory=list)
    children: dict[str, "_Section"] = field(default_factory=dict)


def _outline(sections: list[list[str]]) -> list[_Section]:
    """The section tree, in order of first appearance. Parents that QASPER doesn't
    list on their own ("Experiments" before "Experiments ::: Setup") are implied."""
    top: dict[str, _Section] = {}
    for name, text in sections:
        parts = [p.strip() for p in (name or "").split(":::")]
        parts = [p for p in parts if p] or ["Untitled"]
        level, node = top, None
        for part in parts:
            if part not in level:
                level[part] = _Section(part)
            node = level[part]
            level = node.children
        if text.strip():
            node.texts.append(text.strip())

    def prune(level: dict[str, _Section]) -> list[_Section]:
        kept = []
        for s in level.values():
            s.children = {c.heading: c for c in prune(s.children)}
            if s.texts or s.children:
                kept.append(s)
        return kept

    return prune(top)


def _float_name(file: str, used: set[str]) -> tuple[str, str]:
    """("figures" | "tables", "<kind>-<n>.md") for a float's image filename."""
    m = re.search(r"(Figure|Table)(\d+)", file)
    folder, stem = ("tables", f"table-{m[2]}") if m and m[1] == "Table" else ("figures", f"figure-{m[2]}" if m else "figure")
    name, i = f"{stem}.md", 2
    while f"{folder}/{name}" in used:
        name, i = f"{stem}-{i}.md", i + 1
    used.add(f"{folder}/{name}")
    return folder, name


def paper_nodes(paper: dict) -> list[PlannedNode]:
    """Every node for one paper, parents before children, the paper folder first."""
    nodes: list[PlannedNode] = []
    outline_lines: list[str] = []

    def add(sections: list[_Section], prefix: str, depth: int, parent_role: str = "other") -> None:
        for i, s in enumerate(sections, 1):
            name = f"{i:02d}-{slugify(s.heading)}"
            body = "\n\n".join(s.texts)
            role = classify_section(s.heading, parent_role)
            common = {"aliases": [s.heading], "tags": [TAG, f"role:{role}"]}
            if s.children:
                path = f"{prefix}{name}"
                nodes.append(
                    _node(path, "folder", content=f"# {s.heading}\n\n{body}" if body else None, **common)
                )
                outline_lines.append(f"{'  ' * depth}- {name}/ -- {s.heading}")
                add(list(s.children.values()), f"{path}/", depth + 1, role)
            else:
                nodes.append(_node(f"{prefix}{name}.md", "file", content=f"# {s.heading}\n\n{body}", **common))
                outline_lines.append(f"{'  ' * depth}- {name}.md -- {s.heading}")

    add(_outline(paper["sections"]), "", 0)

    used: set[str] = set()
    floats = []
    for f in paper["floats"]:
        folder, name = _float_name(f["file"], used)
        floats.append(
            _node(
                f"{folder}/{name}",
                "file",
                content=f["caption"],
                description=first_sentence(f["caption"], 120),
                tags=[TAG, f"role:{folder[:-1]}"],
            )
        )
    n_figures = sum(n.path.startswith("figures/") for n in floats)
    n_tables = len(floats) - n_figures
    float_folders = [_node(name, "folder", tags=[TAG]) for name in ("figures", "tables") if any(n.path.startswith(f"{name}/") for n in floats)]
    for name in (n.path for n in float_folders):
        outline_lines.append(f"- {name}/ -- {n_figures if name == 'figures' else n_tables} captions")

    arxiv = f"https://arxiv.org/abs/{paper['id']}"
    folder = _node(
        "",
        "folder",
        title=paper["title"],
        content=(
            f"# {paper['title']}\n\n## Abstract\n\n{paper['abstract'] or '(none)'}\n\n"
            "## Outline\n\n" + "\n".join(["- metadata.md -- paper id, links, counts", *outline_lines])
        ),
        description=first_sentence(paper["abstract"]) if paper["abstract"] else None,
        tags=[TAG],
    )
    metadata = _node(
        "metadata.md",
        "file",
        content=(
            f"# {paper['title']}\n\n- paper id: {paper['id']}\n- arXiv: {arxiv}\n"
            f"- sections: {sum(n.kind == 'file' and n.path[:1].isdigit() for n in nodes)} "
            f"(top level: {sum('/' not in n.path for n in nodes)})\n"
            f"- figures: {n_figures}\n- tables: {n_tables}\n"
        ),
        description="Paper id, arXiv link, section and figure/table counts",
        tags=[TAG, "role:metadata"],
        sources=[{"resource": arxiv}],
    )
    return [folder, metadata, *nodes, *float_folders, *floats]


# --------------------------------------------------------------------------
# KB
# --------------------------------------------------------------------------


def load_into_kb(papers: list[dict], *, index: bool = False) -> dict[str, str]:
    """Create the KB from the papers. Returns {paper id: paper folder node id}. With
    `index`, every created node is also added to the semantic index after the commit."""
    from kb import service
    from kb.db import SessionLocal
    from kb.ingest import materialize

    with SessionLocal() as session:
        if any(n.title == ROOT_TITLE for n in service.list_children(session, None)):
            sys.exit(f"a root '{ROOT_TITLE}' folder already exists; reset the DB or delete it first")
        root = service.create_file(
            session,
            parent_id=None,
            kind="folder",
            title=ROOT_TITLE,
            description="QASPER papers, one folder per paper, laid out like the paper's own outline.",
            tags=[TAG],
        )
        ids: dict[str, str] = {}
        node_ids = [root.id]
        for paper in papers:
            created = materialize(session, paper_nodes(paper), parent_id=root.id)
            ids[paper["id"]] = str(created[""].id)
            node_ids.extend(n.id for n in created.values())
        session.commit()
    if index:
        result = service.index_files(node_ids)
        print(
            f"indexed {len(node_ids)} nodes: {result.num_added} chunks added, "
            f"{result.num_skipped} unchanged, {len(result.failed)} failed",
            file=sys.stderr,
        )
    return ids


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--split", default="validation", choices=["train", "validation", "test"])
    p.add_argument("--limit", type=int, default=None, help="number of papers")
    p.add_argument("--out", default="-", help="JSONL output path, or - for stdout")
    p.add_argument(
        "--to-kb",
        action="store_true",
        help="load the first --limit papers into the KB DB and write the eval set (questions only) to --out",
    )
    p.add_argument("--index", action="store_true", help="with --to-kb: also build the semantic index")
    args = p.parse_args()

    if args.index and not args.to_kb:
        p.error("--index requires --to-kb")
    if args.to_kb and args.limit is None:
        p.error("--to-kb requires --limit")
    papers = list(load_qasper(args.split, args.limit))
    if args.to_kb:
        ids = load_into_kb(papers, index=args.index)
        out_path = "qasper_eval.jsonl" if args.out == "-" else args.out
        with open(out_path, "w", encoding="utf-8") as f:
            for paper in papers:
                rec = {k: v for k, v in paper.items() if k not in ("abstract", "sections", "floats")}
                rec["folder_id"] = ids[paper["id"]]
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{len(ids)} papers -> KB; eval set -> {out_path}", file=sys.stderr)
        return

    out = sys.stdout if args.out == "-" else open(args.out, "w", encoding="utf-8")
    try:
        for paper in papers:
            out.write(json.dumps(paper, ensure_ascii=False) + "\n")
    finally:
        if out is not sys.stdout:
            out.close()
    print(f"wrote {len(papers)} papers", file=sys.stderr)


if __name__ == "__main__":
    main()
