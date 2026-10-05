"""semantic index: pgvector kb_chunks + LangChain record manager table

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05

`kb_chunks` is the table `kb.index.store` binds a langchain_postgres PGVectorStore to
(default PGVectorStore column names: langchain_id / content / embedding /
langchain_metadata, plus the custom metadata columns file_id / heading / start_line /
end_line). `upsertion_record` is langchain_classic's SQLRecordManager table, with DDL
copied verbatim from its `UpsertionRecord` model (langchain-classic 1.0.8) so the
migration is frozen and doesn't import LangChain.

Downgrade drops both tables but leaves the `vector` extension installed (extensions
are database-wide and harmless to keep; same as 0001 leaving pgcrypto).
"""
import os
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match the embedding model's output size (kb.index.store.EMBEDDING_DIM).
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        f"""
        CREATE TABLE kb_chunks (
            langchain_id uuid PRIMARY KEY,
            content text NOT NULL,
            embedding vector({EMBEDDING_DIM}) NOT NULL,
            file_id uuid NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            heading text,
            start_line integer NOT NULL,
            end_line integer NOT NULL,
            langchain_metadata json
        )
        """
    )
    op.execute("CREATE INDEX ix_kb_chunks_file_id ON kb_chunks (file_id)")
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding_hnsw ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # SQLRecordManager's UpsertionRecord table, exactly as SQLAlchemy would emit it.
    op.execute(
        """
        CREATE TABLE upsertion_record (
            uuid VARCHAR NOT NULL,
            key VARCHAR,
            namespace VARCHAR NOT NULL,
            group_id VARCHAR,
            updated_at FLOAT,
            PRIMARY KEY (uuid),
            CONSTRAINT uix_key_namespace UNIQUE (key, namespace)
        )
        """
    )
    op.execute("CREATE INDEX ix_upsertion_record_uuid ON upsertion_record (uuid)")
    op.execute("CREATE INDEX ix_upsertion_record_key ON upsertion_record (key)")
    op.execute("CREATE INDEX ix_upsertion_record_namespace ON upsertion_record (namespace)")
    op.execute("CREATE INDEX ix_upsertion_record_group_id ON upsertion_record (group_id)")
    op.execute("CREATE INDEX ix_upsertion_record_updated_at ON upsertion_record (updated_at)")
    op.execute("CREATE INDEX ix_key_namespace ON upsertion_record (key, namespace)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS upsertion_record")
    op.execute("DROP TABLE IF EXISTS kb_chunks")
    # The `vector` extension is intentionally left installed.
