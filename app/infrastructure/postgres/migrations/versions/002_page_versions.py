"""Page version history and deleted-page archival.

Adds:
- pages.current_version_number (counter for page_versions.version_number)
- page_versions: full-content snapshot per page, created whenever a mutation
  makes new content live (separate from page_revisions, the action/diff/comment log)
- deleted_pages / deleted_page_versions / deleted_page_revisions: archive copies
  of a page + its full version chain + its history log, written by
  PageRepository.archive() before the live `pages` row is hard-deleted

Revision ID: 002
Revises: 001
Create Date: 2026-09-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pinkass"


def upgrade() -> None:
    op.add_column(
        "pages",
        sa.Column("current_version_number", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )

    op.create_table(
        "page_versions",
        sa.Column("version_id", sa.String(), primary_key=True),
        sa.Column("page_id", sa.String(), sa.ForeignKey(f"{SCHEMA}.pages.page_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("references", JSONB(), nullable=False, server_default="[]"),
        sa.Column("aliases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("classification", JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trust_tier", sa.String(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_page_versions_page_id_version_number", "page_versions", ["page_id", "version_number"], schema=SCHEMA,
    )

    op.create_table(
        "deleted_pages",
        sa.Column("page_id", sa.String(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trust_tier", sa.String(), nullable=False, server_default="unverified"),
        sa.Column("next_approval_date", sa.String(), nullable=True),
        sa.Column("verified_content_hash", sa.String(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(), nullable=True),
        sa.Column("inbound_link_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_version_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classification", JSONB(), nullable=False, server_default="[]"),
        sa.Column("references", JSONB(), nullable=False, server_default="[]"),
        sa.Column("aliases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("meta", JSONB(), nullable=False, server_default="{}"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_by", sa.String(), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "deleted_page_versions",
        sa.Column("version_id", sa.String(), primary_key=True),
        sa.Column("page_id", sa.String(), sa.ForeignKey(f"{SCHEMA}.deleted_pages.page_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("references", JSONB(), nullable=False, server_default="[]"),
        sa.Column("aliases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("classification", JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trust_tier", sa.String(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_deleted_page_versions_page_id", "deleted_page_versions", ["page_id"], schema=SCHEMA)

    op.create_table(
        "deleted_page_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("page_id", sa.String(), sa.ForeignKey(f"{SCHEMA}.deleted_pages.page_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_deleted_page_revisions_page_id", "deleted_page_revisions", ["page_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("deleted_page_revisions", schema=SCHEMA)
    op.drop_table("deleted_page_versions", schema=SCHEMA)
    op.drop_table("deleted_pages", schema=SCHEMA)
    op.drop_table("page_versions", schema=SCHEMA)
    op.drop_column("pages", "current_version_number", schema=SCHEMA)
