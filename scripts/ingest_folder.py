"""
Ingests a local directory tree into the knowledge base (see kb.ingest).

Directories become folder nodes, supported files (currently Markdown only) become file
nodes, everything else is skipped and listed in the summary. Re-running creates a new
subtree -- there is no dedupe.

Needs DATABASE_URL (see .env.example).

Usage:
    python scripts/ingest_folder.py PATH [--parent-id UUID] [--tag TAG ...] [--dry-run]
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
    p.add_argument("path", type=Path, help="directory to ingest")
    p.add_argument("--parent-id", type=uuid.UUID, default=None, help="folder node to ingest under (default: root)")
    p.add_argument("--tag", action="append", default=[], help="tag applied to every created node (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="roll back instead of committing")
    args = p.parse_args()

    from kb.db import SessionLocal
    from kb.ingest import ingest_folder

    with SessionLocal() as session:
        try:
            report = ingest_folder(session, args.path, parent_id=args.parent_id, tags=args.tag)
        except ValueError as exc:
            sys.exit(str(exc))
        if args.dry_run:
            session.rollback()
        else:
            session.commit()

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


if __name__ == "__main__":
    main()
