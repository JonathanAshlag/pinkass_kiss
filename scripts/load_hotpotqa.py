"""
Loads HotPotQA (sources, questions, answers) from the HuggingFace hub.

Each record has: id, question, answer, level, type, `sources` (the context
paragraphs: title + text) and `supporting_titles` (the subset of source titles
that are the gold supporting facts).

The full knowledge base (the Wikipedia abstracts dump that the `fullwiki` setting
retrieves from, ~1.5GB, ~5M articles) is downloaded with --download-kb and streamed
with `iter_kb()`; each article is {id, url, title, text}.

With --to-kb, the first --limit questions are loaded and their source paragraphs
become the knowledge base: one file node per unique paragraph title, under a root
"HotPotQA" folder. The questions/answers are written to --out (default
hotpotqa_eval.jsonl) with `supporting_ids`, the KB node ids of the gold paragraphs,
so a retrieval check can compare against them. Needs DATABASE_URL (see .env.example).

Usage:
    pip install datasets
    python scripts/load_hotpotqa.py --to-kb --limit 100
    python scripts/load_hotpotqa.py --split validation --limit 100 --out hotpotqa.jsonl

Importable too: `from load_hotpotqa import load_hotpotqa`.
"""

import argparse
import bz2
import json
import sys
import tarfile
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # HF_TOKEN, DATABASE_URL (for --to-kb)

KB_URL = (
    "https://nlp.stanford.edu/projects/hotpotqa/"
    "enwiki-20171001-pages-meta-current-withlinks-abstracts.tar.bz2"
)
KB_DEFAULT_PATH = Path("hotpotqa_wiki_abstracts.tar.bz2")
ROOT_TITLE = "HotPotQA"
TAG = "hotpotqa"


def download_kb(path: Path = KB_DEFAULT_PATH) -> Path:
    """Download the Wikipedia abstracts dump (skipped if already present)."""
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".part")
        print(f"downloading {KB_URL} -> {path}", file=sys.stderr)
        urllib.request.urlretrieve(KB_URL, tmp)
        tmp.rename(path)
    return path


def iter_kb(path: Path = KB_DEFAULT_PATH, limit: int | None = None) -> Iterator[dict]:
    """Stream articles out of the dump without extracting it to disk."""
    n = 0
    with tarfile.open(path, "r|bz2") as tar:
        for member in tar:
            if not member.isfile():
                continue
            with bz2.open(tar.extractfile(member), "rt", encoding="utf-8") as f:
                for line in f:
                    a = json.loads(line)
                    # "text" is a list of paragraphs, each a list of sentences
                    yield {
                        "id": a["id"],
                        "url": a["url"],
                        "title": a["title"],
                        "text": "\n\n".join("".join(p).strip() for p in a["text"]).strip(),
                    }
                    n += 1
                    if limit is not None and n >= limit:
                        return


def load_hotpotqa(
    split: str = "validation", config: str = "distractor", limit: int | None = None
) -> Iterator[dict]:
    """Yield HotPotQA examples. config: "distractor" or "fullwiki"; split: "train"/"validation"."""
    from datasets import load_dataset

    ds = load_dataset("hotpotqa/hotpot_qa", config, split=split)
    for i, row in enumerate(ds):
        if limit is not None and i >= limit:
            break
        ctx = row["context"]  # {"title": [...], "sentences": [[...], ...]}
        yield {
            "id": row["id"],
            "question": row["question"],
            "answer": row["answer"],
            "level": row["level"],
            "type": row["type"],
            "sources": [
                {"title": t, "text": " ".join(s).strip()}
                for t, s in zip(ctx["title"], ctx["sentences"])
            ],
            "supporting_titles": list(dict.fromkeys(row["supporting_facts"]["title"])),
        }


def load_into_kb(examples: list[dict]) -> dict[str, str]:
    """Create the KB from the examples' sources. Returns {paragraph title: node id}."""
    from kb import service
    from kb.db import SessionLocal

    with SessionLocal() as session:
        if any(n.title == ROOT_TITLE for n in service.list_children(session, None)):
            sys.exit(f"a root '{ROOT_TITLE}' folder already exists; reset the DB or delete it first")
        root = service.create_file(
            session,
            parent_id=None,
            kind="folder",
            title=ROOT_TITLE,
            description="HotPotQA source paragraphs (distractor setting).",
            tags=[TAG],
        )
        ids: dict[str, str] = {}
        for ex in examples:
            for src in ex["sources"]:
                if src["title"] in ids:
                    continue
                node = service.create_file(
                    session,
                    parent_id=root.id,
                    kind="file",
                    title=src["title"],
                    content=src["text"],
                    tags=[TAG],
                )
                ids[src["title"]] = str(node.id)
        session.commit()
    return ids


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--split", default="validation", choices=["train", "validation"])
    p.add_argument("--config", default="distractor", choices=["distractor", "fullwiki"])
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", default="-", help="JSONL output path, or - for stdout")
    p.add_argument(
        "--download-kb",
        action="store_true",
        help="download the full Wikipedia KB and write its articles (honors --limit) instead of QA examples",
    )
    p.add_argument(
        "--to-kb",
        action="store_true",
        help="load the first --limit questions' sources into the KB DB and write the eval set to --out",
    )
    args = p.parse_args()

    if args.to_kb:
        if args.limit is None:
            p.error("--to-kb requires --limit")
        examples = list(load_hotpotqa(args.split, args.config, args.limit))
        ids = load_into_kb(examples)
        out_path = "hotpotqa_eval.jsonl" if args.out == "-" else args.out
        with open(out_path, "w", encoding="utf-8") as f:
            for ex in examples:
                rec = {k: v for k, v in ex.items() if k != "sources"}
                rec["supporting_ids"] = [ids[t] for t in ex["supporting_titles"]]
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{len(ids)} paragraphs -> KB; {len(examples)} questions -> {out_path}", file=sys.stderr)
        return

    out = sys.stdout if args.out == "-" else open(args.out, "w", encoding="utf-8")
    try:
        n = 0
        if args.download_kb:
            it = iter_kb(download_kb(), args.limit)
        else:
            it = load_hotpotqa(args.split, args.config, args.limit)
        for ex in it:
            out.write(json.dumps(ex, ensure_ascii=False) + "\n")
            n += 1
    finally:
        if out is not sys.stdout:
            out.close()
    print(f"wrote {n} examples", file=sys.stderr)


if __name__ == "__main__":
    main()
