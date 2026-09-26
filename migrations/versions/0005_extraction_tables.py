"""Extraction: invoices, invoice_lines, extracted_fields, field_sources.

Revision ID: 0005_extraction_tables
Revises: 0004_pages_runs_blocks
Create Date: 2026-09-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_extraction_tables"
down_revision: str | None = "0004_pages_runs_blocks"
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


def document_and_run(table: str) -> list[Any]:
    """The document_id and extraction_run_id columns with their foreign keys."""
    return [
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_run_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=f"fk_{table}_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            name=f"fk_{table}_extraction_run_id_extraction_runs",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "invoices",
        *common_columns(),
        *document_and_run("invoices"),
        sa.Column("invoice_no", sa.Text(), nullable=True),
        sa.Column("vendor_name", sa.Text(), nullable=True),
        sa.Column("vendor_gstin", sa.Text(), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=True),
        sa.Column("tax_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("total", sa.Numeric(14, 2), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_invoices"),
    )
    op.create_index("ix_invoices_document_id", "invoices", ["document_id"])
    op.create_index("ix_invoices_extraction_run_id", "invoices", ["extraction_run_id"])

    # The unique (invoice_id, line_no) also indexes the invoice_id foreign key.
    op.create_table(
        "invoice_lines",
        *common_columns(),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hsn_code", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=True),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_lines"),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_lines_invoice_id_invoices",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("invoice_id", "line_no", name="uq_invoice_lines_invoice_id_line_no"),
    )

    op.create_table(
        "extracted_fields",
        *common_columns(),
        *document_and_run("extracted_fields"),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_extracted_fields"),
    )
    op.create_index("ix_extracted_fields_document_id", "extracted_fields", ["document_id"])
    op.create_index(
        "ix_extracted_fields_extraction_run_id", "extracted_fields", ["extraction_run_id"]
    )

    op.create_table(
        "field_sources",
        *common_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("block_id", sa.Uuid(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("bbox", postgresql.ARRAY(sa.REAL()), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_field_sources"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_field_sources_document_id_documents",
            ondelete="CASCADE",
        ),
        # The page must exist in the same document.
        sa.ForeignKeyConstraint(
            ["document_id", "page_no"],
            ["pages.document_id", "pages.page_no"],
            name="fk_field_sources_document_id_page_no_pages",
        ),
        sa.ForeignKeyConstraint(
            ["block_id"], ["blocks.id"], name="fk_field_sources_block_id_blocks"
        ),
        sa.CheckConstraint(
            "table_name IN ('invoices', 'invoice_lines', 'extracted_fields')",
            name=op.f("ck_field_sources_table_name"),
        ),
        sa.CheckConstraint(BBOX_SHAPE, name=op.f("ck_field_sources_bbox_shape")),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_field_sources_confidence_range")
        ),
    )
    # Leads with document_id, so it indexes both document foreign keys.
    op.create_index(
        "ix_field_sources_document_id_page_no", "field_sources", ["document_id", "page_no"]
    )
    op.create_index("ix_field_sources_block_id", "field_sources", ["block_id"])


def downgrade() -> None:
    op.drop_index("ix_field_sources_block_id", table_name="field_sources")
    op.drop_index("ix_field_sources_document_id_page_no", table_name="field_sources")
    op.drop_table("field_sources")
    op.drop_index("ix_extracted_fields_extraction_run_id", table_name="extracted_fields")
    op.drop_index("ix_extracted_fields_document_id", table_name="extracted_fields")
    op.drop_table("extracted_fields")
    op.drop_table("invoice_lines")
    op.drop_index("ix_invoices_extraction_run_id", table_name="invoices")
    op.drop_index("ix_invoices_document_id", table_name="invoices")
    op.drop_table("invoices")
