"""
Sweeps what a crashed write left behind (see kb.maintenance): S3 originals no `files`
row references (older than 24 h) and indexed chunks of files that never committed
(older than 24 h). Safe to run any time, e.g. from a cron job; never needed for
correctness, only to reclaim space.

Needs DATABASE_URL, plus BLOB_BUCKET (and AWS_*) to sweep originals; see .env.example.

Usage:
    python scripts/gc.py [--dry-run]
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="list what would be deleted, delete nothing")
    args = p.parse_args()

    from kb.maintenance import collect
    from kb.storage.blobs import get_blob_store

    store = get_blob_store()
    result = collect(store, dry_run=args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    if store is None:
        print("originals: no blob store configured, skipped")
    else:
        print(f"originals: {verb} {len(result.originals)}")
        for key in result.originals:
            print(f"  {key}")
    print(f"chunks: {verb} those of {len(result.chunk_files)} uncommitted file(s)" + ("" if args.dry_run else f" ({result.chunks} chunks)"))


if __name__ == "__main__":
    main()
