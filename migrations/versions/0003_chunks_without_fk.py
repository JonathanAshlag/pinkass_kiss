"""kb_chunks.file_id is no longer a foreign key

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08

Writes are all-or-nothing across the DB, the semantic index and S3 with one commit
point: the `files` commit (see kb.service). A file's chunks are staged through
LangChain's own connections *before* that commit, while the row is still invisible to
them, which the FK would reject. Chunks of a file that never committed are unreachable
(search scopes by committed file ids), compensated on rollback, and swept by
`scripts/gc.py` after a crash. `ix_kb_chunks_file_id` stays.

Downgrade deletes chunks whose file is gone, then re-adds the FK.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE kb_chunks DROP CONSTRAINT IF EXISTS kb_chunks_file_id_fkey")


def downgrade() -> None:
    op.execute("DELETE FROM kb_chunks c WHERE NOT EXISTS (SELECT 1 FROM files f WHERE f.id = c.file_id)")
    op.execute(
        "ALTER TABLE kb_chunks ADD CONSTRAINT kb_chunks_file_id_fkey "
        "FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE"
    )
