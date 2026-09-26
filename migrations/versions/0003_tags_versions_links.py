"""Tags, versions and links between documents.

Revision ID: 0003_tags_versions_links
Revises: 0002_files_and_management
Create Date: 2026-09-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0003_tags_versions_links"
down_revision: str | None = "0002_files_and_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def common_columns() -> list[sa.Column[Any]]:
    """id, created_at and updated_at, as every table here but document_tags has them."""
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
        "tags",
        *common_columns(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )

    # A pure link row. The key leads with document_id, so it indexes that foreign key.
    op.create_table(
        "document_tags",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("document_id", "tag_id", name="pk_document_tags"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_tags_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name="fk_document_tags_tag_id_tags", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_document_tags_tag_id", "document_tags", ["tag_id"])

    op.create_table(
        "document_versions",
        *common_columns(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_document_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_document_versions_document_id_documents"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_document_id"],
            ["documents.id"],
            name="fk_document_versions_supersedes_document_id_documents",
        ),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index(
        "ix_document_versions_supersedes_document_id",
        "document_versions",
        ["supersedes_document_id"],
    )

    op.create_table(
        "document_links",
        *common_columns(),
        sa.Column("from_document_id", sa.Uuid(), nullable=False),
        sa.Column("to_document_id", sa.Uuid(), nullable=False),
        sa.Column("link_type", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_links"),
        sa.ForeignKeyConstraint(
            ["from_document_id"],
            ["documents.id"],
            name="fk_document_links_from_document_id_documents",
        ),
        sa.ForeignKeyConstraint(
            ["to_document_id"], ["documents.id"], name="fk_document_links_to_document_id_documents"
        ),
    )
    op.create_index("ix_document_links_from_document_id", "document_links", ["from_document_id"])
    op.create_index("ix_document_links_to_document_id", "document_links", ["to_document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_links_to_document_id", table_name="document_links")
    op.drop_index("ix_document_links_from_document_id", table_name="document_links")
    op.drop_table("document_links")
    op.drop_index("ix_document_versions_supersedes_document_id", table_name="document_versions")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_document_tags_tag_id", table_name="document_tags")
    op.drop_table("document_tags")
    op.drop_table("tags")
