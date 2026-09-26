"""Files and management: folders, documents, uploads, audit_log.

Revision ID: 0002_files_and_management
Revises: 0001_initial
Create Date: 2026-09-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_files_and_management"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def common_columns() -> list[sa.Column[Any]]:
    """id, created_at and updated_at, as every table here but audit_log has them."""
    return [
        sa.Column("id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "folders",
        *common_columns(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_folders"),
        sa.ForeignKeyConstraint(["parent_id"], ["folders.id"], name="fk_folders_parent_id_folders"),
    )
    op.create_index("ix_folders_parent_id", "folders", ["parent_id"])

    op.create_table(
        "documents",
        *common_columns(),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="processing", nullable=False),
        sa.Column("current_step", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("sha256", name="uq_documents_sha256"),
        sa.ForeignKeyConstraint(
            ["folder_id"], ["folders.id"], name="fk_documents_folder_id_folders"
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_documents_sha256_hex")),
        sa.CheckConstraint(
            "status IN ('processing', 'needs_review', 'published', 'failed')",
            name=op.f("ck_documents_status"),
        ),
        sa.CheckConstraint("page_count >= 0", name=op.f("ck_documents_page_count_not_negative")),
    )
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])

    op.create_table(
        "uploads",
        *common_columns(),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("incoming_key", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_id", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_uploads"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_uploads_document_id_documents"
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_uploads_size_positive")),
        sa.CheckConstraint(
            "status IN ('pending', 'uploaded', 'registered', 'duplicate', 'failed')",
            name=op.f("ck_uploads_status"),
        ),
    )
    op.create_index("ix_uploads_document_id", "uploads", ["document_id"])

    # Append-only, so no updated_at. Details hold IDs and statuses, never document values.
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_index("ix_uploads_document_id", table_name="uploads")
    op.drop_table("uploads")
    op.drop_index("ix_documents_folder_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_folders_parent_id", table_name="folders")
    op.drop_table("folders")
