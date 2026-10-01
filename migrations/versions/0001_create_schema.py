"""create schema: files, manifests, manifest_members

Revision ID: 0001
Revises:
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

file_status = postgresql.ENUM(
    "draft", "stable", "deprecated", name="file_status", create_type=False
)
file_kind = postgresql.ENUM("file", "folder", name="file_kind", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    file_status.create(op.get_bind(), checkfirst=True)
    file_kind.create(op.get_bind(), checkfirst=True)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS trigger AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # --- files -------------------------------------------------------------
    op.create_table(
        "files",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", file_kind, nullable=False, server_default="file"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "aliases", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "verified",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "generated", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("stale_after", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", file_status, nullable=False, server_default="draft"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("blob_key", sa.Text(), nullable=True),
        sa.Column("blob_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("blob_mime_type", sa.Text(), nullable=True),
        sa.Column("blob_checksum", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind = 'folder' OR content IS NOT NULL",
            name="content_required_for_file",
        ),
    )

    op.create_index("ix_files_parent_id", "files", ["parent_id"])
    op.create_index("ix_files_kind", "files", ["kind"])
    op.create_index("ix_files_status", "files", ["status"])
    op.create_index("ix_files_tags", "files", ["tags"], postgresql_using="gin")
    op.create_index("ix_files_sources_gin", "files", ["sources"], postgresql_using="gin")
    op.create_index("ix_files_verified_gin", "files", ["verified"], postgresql_using="gin")

    op.execute(
        """
        CREATE TRIGGER trg_files_set_updated_at
        BEFORE UPDATE ON files
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
        """
    )

    # --- manifests -----------------------------------------------------------
    op.create_table(
        "manifests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_manifests_name", "manifests", ["name"], unique=True)

    op.execute(
        """
        CREATE TRIGGER trg_manifests_set_updated_at
        BEFORE UPDATE ON manifests
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
        """
    )

    # --- manifest_members ------------------------------------------------------
    op.create_table(
        "manifest_members",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "manifest_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manifests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "child_manifest_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manifests.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(file_id IS NOT NULL AND child_manifest_id IS NULL) "
            "OR (file_id IS NULL AND child_manifest_id IS NOT NULL)",
            name="manifest_member_exactly_one_target",
        ),
        sa.CheckConstraint(
            "child_manifest_id IS DISTINCT FROM manifest_id",
            name="manifest_member_no_direct_self_reference",
        ),
    )

    op.create_index(
        "ux_manifest_members_file",
        "manifest_members",
        ["manifest_id", "file_id"],
        unique=True,
        postgresql_where=sa.text("file_id IS NOT NULL"),
    )
    op.create_index(
        "ux_manifest_members_bundle",
        "manifest_members",
        ["manifest_id", "child_manifest_id"],
        unique=True,
        postgresql_where=sa.text("child_manifest_id IS NOT NULL"),
    )
    op.create_index("ix_manifest_members_file_id", "manifest_members", ["file_id"])
    op.create_index(
        "ix_manifest_members_child_manifest_id", "manifest_members", ["child_manifest_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_manifest_members_child_manifest_id", table_name="manifest_members")
    op.drop_index("ix_manifest_members_file_id", table_name="manifest_members")
    op.drop_index("ux_manifest_members_bundle", table_name="manifest_members")
    op.drop_index("ux_manifest_members_file", table_name="manifest_members")
    op.drop_table("manifest_members")

    op.execute("DROP TRIGGER IF EXISTS trg_manifests_set_updated_at ON manifests")
    op.drop_index("ix_manifests_name", table_name="manifests")
    op.drop_table("manifests")

    op.execute("DROP TRIGGER IF EXISTS trg_files_set_updated_at ON files")
    op.drop_index("ix_files_verified_gin", table_name="files")
    op.drop_index("ix_files_sources_gin", table_name="files")
    op.drop_index("ix_files_tags", table_name="files")
    op.drop_index("ix_files_status", table_name="files")
    op.drop_index("ix_files_kind", table_name="files")
    op.drop_index("ix_files_parent_id", table_name="files")
    op.drop_table("files")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    file_kind.drop(op.get_bind(), checkfirst=True)
    file_status.drop(op.get_bind(), checkfirst=True)
