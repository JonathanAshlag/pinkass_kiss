"""
Ingests a git repository (cloned shallowly) into the knowledge base (see kb.ingest.git).

The repo is cloned at --ref (a branch or tag; default HEAD) and its working tree ingested
like a folder; each node's source is git+<url>@<commit>#<path>. Re-running creates a new subtree.

Needs DATABASE_URL (see .env.example).

Usage:
    python scripts/ingest_git.py URL_OR_PATH [--ref BRANCH_OR_TAG] [--parent-id UUID] [--tag TAG ...] [--dry-run]

All or nothing: if any file fails to convert, every failure is listed and nothing is
ingested. Every created file is embedded into the semantic index (kb_chunks) before the
commit, so the embeddings model (EMBEDDINGS_MODEL, see .env.example) must be reachable;
retained originals go to the blob store (BLOB_BUCKET) the same way.
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
    args = p.parse_args()

    from kb import service
    from kb.service import IndexingError
    from kb.storage.blobs import BlobStoreError
    from kb.storage.db import SessionLocal
    from kb.ingest import IngestFailed, ingest_git_repo

    with SessionLocal() as session:
        try:
            report = ingest_git_repo(session, args.url, ref=args.ref, parent_id=args.parent_id, tags=args.tag)
        except IngestFailed as exc:
            for path, error in exc.failures:
                print(f"  {path}: {error}", file=sys.stderr)
            sys.exit(str(exc))
        except (ValueError, IndexingError, BlobStoreError) as exc:
            sys.exit(f"nothing was ingested: {exc}")
        if args.dry_run:
            session.rollback()  # also undoes the staged chunks and uploaded originals
        else:
            try:
                committed = service.commit(session)
            except IndexingError as exc:
                sys.exit(f"nothing was ingested: {exc}")

    print(f"{'[dry run] ' if args.dry_run else ''}root: {report.root_id}")
    print(f"files created:   {len(report.files_created)}")
    print(f"folders created: {len(report.folders_created)}")
    if report.skipped:
        by_ext = Counter(path.suffix.lower() or "(none)" for path in report.skipped)
        summary = ", ".join(f"{ext} x{n}" for ext, n in by_ext.most_common())
        print(f"skipped (unsupported): {len(report.skipped)} -- {summary}")
    if not args.dry_run and committed.indexed is not None:
        result = committed.indexed
        print(f"indexed: {result.num_added} chunks added, {result.num_skipped} unchanged")


if __name__ == "__main__":
    main()
