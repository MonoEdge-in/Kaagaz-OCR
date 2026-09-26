"""Document content: pages, extraction_runs, blocks.

Revision ID: 0004_pages_runs_blocks
Revises: 0003_tags_versions_links
Create Date: 2026-09-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_pages_runs_blocks"
down_revision: str | None = "0003_tags_versions_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Four numbers, none missing, from 0 up, with x0 <= x1 and y0 <= y1.
BBOX_SHAPE = (
    "array_ndims(bbox) = 1 AND array_lower(bbox, 1) = 1"
    " AND cardinality(bbox) = 4 AND array_position(bbox, NULL) IS NULL"
    " AND bbox[1] >= 0 AND bbox[2] >= 0"
    " AND bbox[1] <= bbox[3] AND bbox[2] <= bbox[4]"
)


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
    # The unique (document_id, page_no) also indexes the document_id foreign key.
    op.create_table(
        "pages",
        *common_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("image_key", sa.Text(), nullable=True),
        sa.Column("width_px", sa.Integer(), nullable=True),
        sa.Column("height_px", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pages"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_pages_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "page_no", name="uq_pages_document_id_page_no"),
        # The target of blocks' composite foreign key.
        sa.UniqueConstraint("id", "document_id", name="uq_pages_id_document_id"),
        sa.CheckConstraint("page_no >= 1", name=op.f("ck_pages_page_no_positive")),
        sa.CheckConstraint(
            "route IN ('spreadsheet', 'text_layer', 'ocr', 'handwriting')",
            name=op.f("ck_pages_route"),
        ),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name=op.f("ck_pages_status")),
    )

    op.create_table(
        "extraction_runs",
        *common_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column(
            "models", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_runs"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_extraction_runs_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'superseded')",
            name=op.f("ck_extraction_runs_status"),
        ),
    )
    op.create_index("ix_extraction_runs_document_id", "extraction_runs", ["document_id"])
    # At most one current run per document.
    op.create_index(
        "uq_extraction_runs_current_run",
        "extraction_runs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )

    op.create_table(
        "blocks",
        *common_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("block_no", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("bbox", postgresql.ARRAY(sa.REAL()), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("parent_block_id", sa.Uuid(), nullable=True),
        sa.Column("extraction_run_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_blocks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_blocks_document_id_documents",
            ondelete="CASCADE",
        ),
        # (page, document) together, so a block can't sit on another document's page.
        sa.ForeignKeyConstraint(
            ["page_id", "document_id"],
            ["pages.id", "pages.document_id"],
            name="fk_blocks_page_id_document_id_pages",
        ),
        sa.ForeignKeyConstraint(
            ["parent_block_id"], ["blocks.id"], name="fk_blocks_parent_block_id_blocks"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            name="fk_blocks_extraction_run_id_extraction_runs",
        ),
        sa.CheckConstraint(
            "block_type IN ('heading', 'paragraph', 'table', 'table_cell', 'stamp',"
            " 'signature', 'handwriting', 'figure')",
            name=op.f("ck_blocks_block_type"),
        ),
        sa.CheckConstraint(BBOX_SHAPE, name=op.f("ck_blocks_bbox_shape")),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_blocks_confidence_range")
        ),
    )
    op.create_index("ix_blocks_document_id", "blocks", ["document_id"])
    op.create_index("ix_blocks_page_id_document_id", "blocks", ["page_id", "document_id"])
    op.create_index("ix_blocks_parent_block_id", "blocks", ["parent_block_id"])
    op.create_index("ix_blocks_extraction_run_id", "blocks", ["extraction_run_id"])


def downgrade() -> None:
    op.drop_index("ix_blocks_extraction_run_id", table_name="blocks")
    op.drop_index("ix_blocks_parent_block_id", table_name="blocks")
    op.drop_index("ix_blocks_page_id_document_id", table_name="blocks")
    op.drop_index("ix_blocks_document_id", table_name="blocks")
    op.drop_table("blocks")
    op.drop_index("uq_extraction_runs_current_run", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_document_id", table_name="extraction_runs")
    op.drop_table("extraction_runs")
    op.drop_table("pages")
