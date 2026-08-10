"""Initial PostgreSQL schema.

Revision ID: 001
Revises:
Create Date: 2026-07-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "pinkass"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "pages",
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
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("classification", JSONB(), nullable=False, server_default="[]"),
        sa.Column("references", JSONB(), nullable=False, server_default="[]"),
        sa.Column("meta", JSONB(), nullable=False, server_default="{}"),
        sa.Column("aliases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "tsv",
            TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(aliases::text, ''))",
                persisted=True,
            ),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_pages_status", "pages", ["status"], schema=SCHEMA)
    op.create_index("ix_pages_trust_inbound", "pages", ["trust_tier", "inbound_link_count"], schema=SCHEMA)
    op.create_index(
        "ix_pages_next_approval",
        "pages",
        ["next_approval_date"],
        schema=SCHEMA,
        postgresql_where=sa.text("next_approval_date IS NOT NULL"),
    )
    op.create_index("ix_pages_tsv", "pages", ["tsv"], schema=SCHEMA, postgresql_using="gin")
    op.create_index("ix_pages_tags_gin", "pages", ["tags"], schema=SCHEMA, postgresql_using="gin")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_pages_title_trgm "
        f"ON {SCHEMA}.pages USING GiST (title gist_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_pages_description_trgm "
        f"ON {SCHEMA}.pages USING GiST (description gist_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_pages_aliases_trgm "
        f"ON {SCHEMA}.pages USING GiST ((aliases::text) gist_trgm_ops)"
    )

    op.create_table(
        "page_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("page_id", sa.String(), sa.ForeignKey(f"{SCHEMA}.pages.page_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_page_revisions_page_id_created_at", "page_revisions", ["page_id", "created_at"], schema=SCHEMA)

    op.create_table(
        "page_refs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("from_page_id", sa.String(), sa.ForeignKey(f"{SCHEMA}.pages.page_id", ondelete="CASCADE"), nullable=False),
        sa.Column("ref_type", sa.String(), nullable=False),
        sa.Column("to_page_id", sa.String(), sa.ForeignKey(f"{SCHEMA}.pages.page_id", ondelete="SET NULL"), nullable=True),
        sa.Column("file_id", sa.String(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_page_refs_from", "page_refs", ["from_page_id"], schema=SCHEMA)
    op.create_index("ix_page_refs_to_page", "page_refs", ["to_page_id"], schema=SCHEMA)

    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("permission_level", sa.String(), nullable=False, server_default="read_only"),
        sa.Column("workflow_id", sa.String(), nullable=True),
        sa.Column("page_permissions", JSONB(), nullable=False, server_default="[]"),
        schema=SCHEMA,
    )

    op.create_table(
        "workflows",
        sa.Column("workflow_id", sa.String(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps", JSONB(), nullable=False, server_default="[]"),
        sa.Column("history", JSONB(), nullable=False, server_default="[]"),
        schema=SCHEMA,
    )

    op.create_table(
        "requests",
        sa.Column("request_id", sa.String(), primary_key=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("page_id", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("proposed_content", JSONB(), nullable=True),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("history", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_requests_status", "requests", ["status"], schema=SCHEMA)
    op.create_index("ix_requests_requested_by", "requests", ["requested_by"], schema=SCHEMA)
    op.create_index("ix_requests_page_id", "requests", ["page_id"], schema=SCHEMA)

    op.create_table(
        "source_files",
        sa.Column("file_id", sa.String(), primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False, server_default=""),
        sa.Column("uploaded_by", sa.String(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("generated_page_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    op.create_table(
        "agents",
        sa.Column("agent_id", sa.String(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey(f"{SCHEMA}.users.user_id"), nullable=False),
        sa.Column("api_key_hash", sa.String(), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_agents_api_key_hash", "agents", ["api_key_hash"], schema=SCHEMA, unique=True)

    op.create_table(
        "bundles",
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("entries", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("bundles", schema=SCHEMA)
    op.drop_table("agents", schema=SCHEMA)
    op.drop_table("source_files", schema=SCHEMA)
    op.drop_table("requests", schema=SCHEMA)
    op.drop_table("workflows", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
    op.drop_table("page_refs", schema=SCHEMA)
    op.drop_table("page_revisions", schema=SCHEMA)
    op.drop_table("pages", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
