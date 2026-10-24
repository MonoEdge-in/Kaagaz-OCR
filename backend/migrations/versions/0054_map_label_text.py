"""A map value's printed label, as text (PRD 6 package D2's map values, #274): the land reading
located a measurement's label ("North", "A-B") and kept only its spot (``label_page_no``,
``label_region``, 0004), so an answer could say "30 m" but never which side it measures.

* ``extracted_values.label_text``: the reader's text where the label was located, plain text
  (``clean_text``), at most 80 characters; written by the land reading for a map's values
  only (``land_dimension``, ``land_boundary_*``, ``land_length``, ``land_width``,
  ``land_road_width``, ``land_scale``), NULL for every other value and for rows read before
  0054. Never the model's own wording.

One nullable column with no default (no table rewrite, no row written). The app role's
INSERT and UPDATE on ``extracted_values`` are table grants (0001), so they cover it; no grant
changes. The downgrade drops the column (the labels are lost; a read again writes them).

Revision ID: 0054
Revises: 0053 (speed controls)
"""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

MAX_LABEL_TEXT = 80
COMMENT = (
    "The printed label of a map value (land_dimension, land_boundary_*, land_length, land_width,"
    " land_road_width, land_scale) as the reader read it where the label was located, plain text,"
    " at most 80 characters, such as North or A-B. NULL for other values and for values read before it."
)

UPGRADE = [
    "ALTER TABLE extracted_values ADD COLUMN label_text text NULL"
    f" CONSTRAINT extracted_values_label_text_length CHECK (length(label_text) <= {MAX_LABEL_TEXT})",
    "COMMENT ON COLUMN extracted_values.label_text IS '" + COMMENT.replace("'", "''") + "'",
]

DOWNGRADE = ["ALTER TABLE extracted_values DROP COLUMN IF EXISTS label_text"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
