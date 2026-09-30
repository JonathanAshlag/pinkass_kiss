import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from kb.db import Base

FileStatus = ENUM("draft", "stable", "deprecated", name="file_status", create_type=False)
FileKind = ENUM("file", "folder", name="file_kind", create_type=False)


class File(Base):
    """
    One row per knowledge file: YAML frontmatter fields as typed columns,
    markdown body in `content`. `parent_id` places the file in the OKF
    directory tree (self-referencing adjacency list).
    """

    __tablename__ = "files"

    # Matches the frontmatter `id` (uuid4) directly -- no separate internal PK.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )

    # 'file' = leaf knowledge article (content required). 'folder' = tree
    # container; content is optional (e.g. an explainer for what's inside).
    kind: Mapped[str] = mapped_column(FileKind, nullable=False, server_default="file")

    title: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")

    # Repeated nested frontmatter objects: kept as JSONB, no normalization.
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    verified: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    # Single optional {by, at} object (OKF's `generated`) -- unlike
    # sources/verified this isn't a repeated list, so NULL (not "[]") means
    # "no provenance recorded".
    generated: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    stale_after: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(FileStatus, nullable=False, server_default="draft")

    # NULL only allowed when kind='folder' (see content_required_for_file).
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # Kept in sync by the trg_files_set_updated_at DB trigger, not the ORM.
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    # Soft delete: NULL = active. Deleting a folder cascades to its subtree
    # (see kb.dal.delete_node); all DAL reads exclude these by default.
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Placeholders for a future object-storage-backed blob layer. Unused
    # until that layer exists -- content stays text-only for now.
    blob_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    blob_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    blob_mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    blob_checksum: Mapped[str | None] = mapped_column(Text, nullable=True)

    parent: Mapped["File | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["File"]] = relationship(back_populates="parent")

    __table_args__ = (
        CheckConstraint(
            "kind = 'folder' OR content IS NOT NULL",
            name="content_required_for_file",
        ),
        Index("ix_files_parent_id", "parent_id"),
        Index("ix_files_kind", "kind"),
        Index("ix_files_status", "status"),
        Index("ix_files_tags", "tags", postgresql_using="gin"),
        Index("ix_files_sources_gin", "sources", postgresql_using="gin"),
        Index("ix_files_verified_gin", "verified", postgresql_using="gin"),
    )


class Manifest(Base):
    """
    A named, curated manifest of files/directories/other manifests that an
    agent developer preclaims as relevant to their agent. Resolution
    (expanding directories/nested manifests into a concrete file set)
    happens at read time via kb.dal.resolve_manifest -- membership itself is
    static, but a directory member's resolved contents are not (new children
    under it are picked up automatically).

    Named "manifest" (not "bundle") specifically to avoid colliding with
    OKF's own use of "bundle" for the whole knowledge directory.
    """

    __tablename__ = "manifests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # Kept in sync by the trg_manifests_set_updated_at DB trigger, not the ORM.
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    members: Mapped[list["ManifestMember"]] = relationship(
        foreign_keys="ManifestMember.manifest_id", back_populates="manifest"
    )

    __table_args__ = (Index("ix_manifests_name", "name", unique=True),)


class ManifestMember(Base):
    """
    One membership row: exactly one of `file_id` (a file or directory node)
    or `child_manifest_id` (a nested manifest) is set. Multi-hop cycle
    prevention for nested manifests is enforced in kb.dal.add_manifest_member,
    not the database -- only the direct self-reference case is a CHECK here.
    """

    __tablename__ = "manifest_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manifests.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=True
    )
    child_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manifests.id", ondelete="CASCADE"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    manifest: Mapped["Manifest"] = relationship(
        foreign_keys=[manifest_id], back_populates="members"
    )
    file: Mapped["File | None"] = relationship(foreign_keys=[file_id])
    child_manifest: Mapped["Manifest | None"] = relationship(foreign_keys=[child_manifest_id])

    __table_args__ = (
        CheckConstraint(
            "(file_id IS NOT NULL AND child_manifest_id IS NULL) "
            "OR (file_id IS NULL AND child_manifest_id IS NOT NULL)",
            name="manifest_member_exactly_one_target",
        ),
        CheckConstraint(
            "child_manifest_id IS DISTINCT FROM manifest_id",
            name="manifest_member_no_direct_self_reference",
        ),
        Index(
            "ux_manifest_members_file",
            "manifest_id",
            "file_id",
            unique=True,
            postgresql_where=text("file_id IS NOT NULL"),
        ),
        Index(
            "ux_manifest_members_bundle",
            "manifest_id",
            "child_manifest_id",
            unique=True,
            postgresql_where=text("child_manifest_id IS NOT NULL"),
        ),
        Index("ix_manifest_members_file_id", "file_id"),
        Index("ix_manifest_members_child_manifest_id", "child_manifest_id"),
    )
