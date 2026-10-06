"""
Rebuilds or refreshes the semantic index (kb_chunks) from the files table (see kb.semantic_index).

--all rebuilds from every active node with content and removes chunks of deleted or
emptied nodes; --file-id (re)indexes just those nodes (deleted/missing/empty ones are
unindexed). Unchanged chunks are skipped, not re-embedded.

Needs DATABASE_URL (and EMBEDDINGS_MODEL / EMBEDDING_DIM if not the defaults; see
.env.example).

Usage:
    python scripts/reindex.py --all
    python scripts/reindex.py --file-id UUID [UUID ...]
"""

import argparse
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="rebuild the whole index (full cleanup)")
    group.add_argument("--file-id", type=uuid.UUID, nargs="+", help="node(s) to (re)index")
    args = p.parse_args()

    from kb.semantic_index.indexer import index_files, reindex_all

    result = reindex_all() if args.all else index_files(args.file_id)

    print(f"added:   {result.num_added}")
    print(f"updated: {result.num_updated}")
    print(f"skipped: {result.num_skipped}")
    print(f"deleted: {result.num_deleted}")
    if result.failed:
        print(f"failed:  {len(result.failed)}")
        for file_id, error in result.failed:
            print(f"  {file_id}: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
