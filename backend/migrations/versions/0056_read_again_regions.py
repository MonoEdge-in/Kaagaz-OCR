"""The regions of a page asked to be read again (#385): "Read again" for a spot of a page reads
only that spot again, cropped and zoomed, and keeps the rest of the page as read.

* ``pages.read_again_regions``: the regions asked for with the page's
  ``read_again_requested_at`` (0021), as a JSON array of 1 to MAX_REGIONS boxes, each
  [x0, y0, x1, y1] in page fractions; NULL when the whole page is to be read again, or none.
  Cleared with the mark; a save replaces the page rows, so a page read again has none.

One nullable column with no default (no table rewrite, no row written). ``pages`` keeps its
``customer_id``, row level security policy (0001) and table grants (0001), which cover it; no
grant changes. The downgrade drops the column (a pending request reads its whole page again).

Revision ID: 0056
Revises: 0055 (org numbers)
"""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

MAX_REGIONS = 10  # kaagaz.api.routers.documents.MAX_READ_AGAIN_REGIONS
COMMENT = (
    "The regions of the page asked to be read again (#385), a JSON array of [x0, y0, x1, y1] in page"
    " fractions; NULL when the whole page is to be read again, or none is asked for."
)

UPGRADE = [
    "ALTER TABLE pages ADD COLUMN read_again_regions jsonb NULL"
    " CONSTRAINT pages_read_again_regions_shape CHECK (jsonb_typeof(read_again_regions) = 'array'"
    f" AND jsonb_array_length(read_again_regions) BETWEEN 1 AND {MAX_REGIONS})",
    "COMMENT ON COLUMN pages.read_again_regions IS '" + COMMENT.replace("'", "''") + "'",
]

DOWNGRADE = ["ALTER TABLE pages DROP COLUMN IF EXISTS read_again_regions"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
