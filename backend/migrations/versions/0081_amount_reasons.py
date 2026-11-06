"""The amount safety layer's label reasons (#524, Tushar, 6 Nov 2026). NEEDS VRUSHIT'S APPROVAL:
schema.

An amount the rules doubt goes to review with its own reason, shown in plain words in the review
queue (kaagaz.extraction.labels.label_amounts):

* ``extracted_values.label_reason``: 0021's reasons, then ``mixed_digits`` (digits of two
  scripts, ९४0000/-), ``amount_commas`` (commas no grouping fits, 1,15,0000), ``amount_mark`` (a
  mark inside the digits, or "/-" read as a 1, 115000001-) and ``amount_outlier`` (more than ten
  times the page's other total). Nothing else changes; the CHECK keeps 0001's name.

The CHECK is dropped and added again: PostgreSQL reads extracted_values once to validate it,
with no rewrite. The downgrade refuses while a value carries one of these reasons, as 0004's and
0021's do: no older reason tells the truth about it.

Numbered 0081 because other branches hold 0076 to 0080; it follows this branch's head, 0075.
Whichever of them merges first, this file's ``down_revision`` becomes the last of them, so the
line keeps one head (tests/test_migration_heads.py).

Revision ID: 0081
Revises: 0075 (the storage limit's merge)
"""

from alembic import op

revision = "0081"
down_revision = "0075"
branch_labels = None
depends_on = None

# 0021's label reasons, in its order (kaagaz.db.review.EVERY_REASON), then #524's.
OLD_REASONS = (
    "direct_cell",
    "readers_agree",
    "readers_disagree",
    "single_reader",
    "low_confidence",
    "corrected",
    "learned_correction",
    "check_failed",
    "model_chosen",
    "ocr_script",
    "unsure_text",
    "box_unanchored",
    "local_reader",
)
NEW_REASONS = ("mixed_digits", "amount_commas", "amount_mark", "amount_outlier")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _check(reasons: tuple[str, ...]) -> str:
    return (
        "ALTER TABLE extracted_values DROP CONSTRAINT extracted_values_label_reason_check,"
        f" ADD CONSTRAINT extracted_values_label_reason_check CHECK (label_reason IN ({_quoted(reasons)}))"
    )


UPGRADE = [_check(OLD_REASONS + NEW_REASONS)]

DOWNGRADE = [
    f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM extracted_values WHERE label_reason IN ({_quoted(NEW_REASONS)})) THEN
            RAISE EXCEPTION 'values labelled with an amount reason exist; relabel them before downgrading'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    _check(OLD_REASONS),
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
