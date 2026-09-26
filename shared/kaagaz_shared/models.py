"""Every table, as SQLAlchemy 2 models. They describe the schema after the latest migration.

The migrations in `migrations/versions/` create the schema, and `alembic check` fails in CI
if these models and the migrations ever disagree. Change both together, with
`docs/data-dictionary.md`.

Conventions (docs/prd/day-1-prd.html section 4, and the approved schema plan):
- UUID keys from `gen_random_uuid()`; `created_at` and `updated_at` set by the database.
- Status and type fields are text with a named CHECK, easier to extend than enums.
- Every foreign key has an index. Foreign keys are NO ACTION, so deleting something still
  in use fails loudly.
"""

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine

# Predictable constraint names, so a migration can drop one by name and a test can tell
# which rule an insert broke.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def bbox_check(column: str) -> str:
    """Four numbers, none missing, from 0 up, with x0 <= x1 and y0 <= y1."""
    return (
        f"array_ndims({column}) = 1 AND array_lower({column}, 1) = 1"
        f" AND cardinality({column}) = 4 AND array_position({column}, NULL) IS NULL"
        f" AND {column}[1] >= 0 AND {column}[2] >= 0"
        f" AND {column}[1] <= {column}[3] AND {column}[2] <= {column}[4]"
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Python types to column types, so most columns need no explicit type.
    type_annotation_map: ClassVar[dict[Any, TypeEngine[Any]]] = {
        str: Text(),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB(),
        list[float]: ARRAY(REAL()),
    }


class IdMixin:
    """A UUID key and the time the row was created, both set by the database."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TimestampsMixin(IdMixin):
    """Adds `updated_at`, which the ORM refreshes on every update it makes."""

    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Files and management (migration 0002)
# ---------------------------------------------------------------------------


class Folder(TimestampsMixin, Base):
    """A virtual folder. Files are never moved in storage."""

    __tablename__ = "folders"

    name: Mapped[str]
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id"), index=True)
    created_by: Mapped[str]
    deleted_at: Mapped[datetime | None]


class Document(TimestampsMixin, Base):
    """One row per unique file, found by its SHA-256 fingerprint."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint(
            "status IN ('processing', 'needs_review', 'published', 'failed')", name="status"
        ),
        CheckConstraint("page_count >= 0", name="page_count_not_negative"),
    )

    sha256: Mapped[str | None] = mapped_column(unique=True)
    original_filename: Mapped[str]
    content_type: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str]
    # No CHECK yet: the document types are decided on Day 4.
    doc_type: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="processing")
    current_step: Mapped[str | None]
    page_count: Mapped[int | None]
    summary: Mapped[str | None]
    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id"), index=True)
    uploaded_by: Mapped[str]
    deleted_at: Mapped[datetime | None]


class Upload(TimestampsMixin, Base):
    """One row per upload attempt, duplicates and failures included."""

    __tablename__ = "uploads"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint(
            "status IN ('pending', 'uploaded', 'registered', 'duplicate', 'failed')",
            name="status",
        ),
    )

    original_filename: Mapped[str]
    content_type: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    incoming_key: Mapped[str]
    uploaded_by: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="pending")
    error: Mapped[str | None]
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"), index=True)
    workflow_id: Mapped[str | None]
    completed_at: Mapped[datetime | None]


class AuditLog(IdMixin, Base):
    """Who did what, and when. Append-only, so it has no `updated_at`."""

    __tablename__ = "audit_log"

    actor: Mapped[str]
    action: Mapped[str]
    entity_type: Mapped[str]
    entity_id: Mapped[uuid.UUID | None]
    details: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"))


# ---------------------------------------------------------------------------
# Tags, versions and links between documents (migration 0003)
# ---------------------------------------------------------------------------


class Tag(TimestampsMixin, Base):
    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(unique=True)


class DocumentTag(Base):
    """Which documents carry which tags. A pure link row: no id and no `updated_at`."""

    __tablename__ = "document_tags"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DocumentVersion(TimestampsMixin, Base):
    """A corrected re-upload, linked to the document it replaces."""

    __tablename__ = "document_versions"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    supersedes_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id"), index=True
    )
    note: Mapped[str | None]
    created_by: Mapped[str]


class DocumentLink(TimestampsMixin, Base):
    """Related documents, such as an invoice and its purchase order."""

    __tablename__ = "document_links"

    from_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    to_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    link_type: Mapped[str]


# ---------------------------------------------------------------------------
# Document content (migration 0004)
# ---------------------------------------------------------------------------


class Page(TimestampsMixin, Base):
    """One page of a document, or one sheet of a spreadsheet."""

    __tablename__ = "pages"
    __table_args__ = (
        CheckConstraint("page_no >= 1", name="page_no_positive"),
        CheckConstraint(
            "route IN ('spreadsheet', 'text_layer', 'ocr', 'handwriting')", name="route"
        ),
        CheckConstraint("status IN ('pending', 'done', 'failed')", name="status"),
        UniqueConstraint("document_id", "page_no"),
        # Lets blocks point at (page, document) together, so a block can't sit on a page
        # of another document.
        UniqueConstraint("id", "document_id"),
    )

    # Indexed by the unique (document_id, page_no), which leads with it.
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page_no: Mapped[int]
    route: Mapped[str | None]
    image_key: Mapped[str | None]
    width_px: Mapped[int | None]
    height_px: Mapped[int | None]
    status: Mapped[str]


class ExtractionRun(TimestampsMixin, Base):
    """One pass of the pipeline over a document. The `succeeded` one is the current run."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'superseded')", name="status"
        ),
        # At most one current run per document.
        Index(
            "uq_extraction_runs_current_run",
            "document_id",
            unique=True,
            postgresql_where=sql_text("status = 'succeeded'"),
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    pipeline_version: Mapped[str]
    models: Mapped[dict[str, Any]] = mapped_column(server_default=sql_text("'{}'::jsonb"))
    status: Mapped[str]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None]


class Block(TimestampsMixin, Base):
    """One piece of a page in reading order, with where it sits on the page."""

    __tablename__ = "blocks"
    __table_args__ = (
        ForeignKeyConstraint(["page_id", "document_id"], ["pages.id", "pages.document_id"]),
        Index("ix_blocks_page_id_document_id", "page_id", "document_id"),
        CheckConstraint(
            "block_type IN ('heading', 'paragraph', 'table', 'table_cell', 'stamp',"
            " 'signature', 'handwriting', 'figure')",
            name="block_type",
        ),
        CheckConstraint(bbox_check("bbox"), name="bbox_shape"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[uuid.UUID]
    block_no: Mapped[int]
    block_type: Mapped[str]
    # What the reader saw, never overwritten. Untrusted document text.
    text: Mapped[str | None]
    bbox: Mapped[list[float]]
    confidence: Mapped[float | None] = mapped_column(REAL)
    source: Mapped[str]
    parent_block_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("blocks.id"), index=True)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_runs.id"), index=True
    )
