"""
Ingests a git repository (cloned shallowly) into the knowledge base (see kb.ingest.git).

The repo is cloned at --ref (a branch or tag; default HEAD) and its working tree ingested
like a folder; each node's source is git+<url>@<commit>#<path>. Re-running creates a new subtree.

Needs DATABASE_URL (see .env.example).

Usage:
    python scripts/ingest_git.py URL_OR_PATH [--ref BRANCH_OR_TAG] [--parent-id UUID] [--tag TAG ...] [--dry-run] [--index]

--index embeds the created nodes into the semantic index (kb_chunks) after the commit;
it needs the embeddings model (EMBEDDINGS_MODEL, see .env.example) to be reachable.
"""

import argparse
import sys
import uuid
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("url", help="repository URL or local path")
    p.add_argument("--ref", default=None, help="branch or tag to clone (default: remote HEAD)")
    p.add_argument("--parent-id", type=uuid.UUID, default=None, help="folder node to ingest under (default: root)")
    p.add_argument("--tag", action="append", default=[], help="tag applied to every created node (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="roll back instead of committing")
    p.add_argument("--index", action="store_true", help="after committing, add the created nodes to the semantic index")
    args = p.parse_args()

    from kb import service
    from kb.storage.db import SessionLocal
    from kb.ingest import ingest_git_repo

    with SessionLocal() as session:
        try:
            report = ingest_git_repo(session, args.url, ref=args.ref, parent_id=args.parent_id, tags=args.tag)
        except ValueError as exc:
            sys.exit(str(exc))
        if args.dry_run:
            session.rollback()
        else:
            # --index: index everything the ingest touched, synchronously, after the commit
            committed = service.commit(session, index=args.index)

    print(f"{'[dry run] ' if args.dry_run else ''}root: {report.root_id}")
    print(f"files created:   {len(report.files_created)}")
    print(f"folders created: {len(report.folders_created)}")
    if report.skipped:
        by_ext = Counter(path.suffix.lower() or "(none)" for path in report.skipped)
        summary = ", ".join(f"{ext} x{n}" for ext, n in by_ext.most_common())
        print(f"skipped (unsupported): {len(report.skipped)} -- {summary}")
    if report.failed:
        print(f"failed: {len(report.failed)}")
        for path, error in report.failed:
            print(f"  {path}: {error}")

    if args.index and not args.dry_run:
        print_index_result(committed.indexed)


def print_index_result(result) -> None:
    if result is None:  # service.commit logged why (e.g. index stack not installed)
        sys.exit("--index: indexing failed, see the log above")
    print(
        f"indexed: {result.num_added} chunks added, {result.num_updated} updated, "
        f"{result.num_skipped} unchanged, {result.num_deleted} deleted"
    )
    for file_id, error in result.failed:
        print(f"  index failed {file_id}: {error}")


if __name__ == "__main__":
    main()
