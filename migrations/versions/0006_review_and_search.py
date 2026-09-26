"""Review and search: review_tasks, chunks, with the HNSW and full-text indexes.

Revision ID: 0006_review_and_search
Revises: 0005_extraction_tables
Create Date: 2026-09-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0006_review_and_search"
down_revision: str | None = "0005_extraction_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def common_columns() -> list[sa.Column[Any]]:
    """id, created_at and updated_at, as every table here has them."""
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
        "review_tasks",
        *common_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("current_value", sa.Text(), nullable=True),
        sa.Column("corrected_value", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_review_tasks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_review_tasks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "table_name IN ('invoices', 'invoice_lines', 'extracted_fields')",
            name=op.f("ck_review_tasks_table_name"),
        ),
        sa.CheckConstraint("status IN ('open', 'resolved')", name=op.f("ck_review_tasks_status")),
    )
    op.create_index("ix_review_tasks_document_id", "review_tasks", ["document_id"])

    # The unique (document_id, chunk_no) also indexes the document_id foreign key.
    op.create_table(
        "chunks",
        *common_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=False),
        sa.Column("page_to", sa.Integer(), nullable=False),
        sa.Column("section_path", sa.Text(), nullable=True),
        sa.Column("header", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(header, '') || ' ' || text)", persisted=True
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "chunk_no", name="uq_chunks_document_id_chunk_no"),
        sa.CheckConstraint("page_from >= 1", name=op.f("ck_chunks_page_from_positive")),
        sa.CheckConstraint("page_to >= page_from", name=op.f("ck_chunks_page_range")),
    )
    # pgvector's defaults (m=16, ef_construction=64), cosine distance.
    op.create_index(
        "ix_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.drop_index("ix_chunks_embedding", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_review_tasks_document_id", table_name="review_tasks")
    op.drop_table("review_tasks")
