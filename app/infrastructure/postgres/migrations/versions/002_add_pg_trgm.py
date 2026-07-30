"""Enable pg_trgm extension and add GiST trigram indexes for fuzzy search.

Revision ID: 002
Revises: 001
Create Date: 2026-07-30

"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pinkass"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_pages_title_trgm "
        f"ON {SCHEMA}.pages USING GiST (title gist_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_pages_description_trgm "
        f"ON {SCHEMA}.pages USING GiST (description gist_trgm_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_pages_description_trgm")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_pages_title_trgm")
