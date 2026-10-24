"""Land facts (PRD 6 package D1, the schema plan in #245, decision 3 of the PRD 6 build spec):
owners, shares and places are values, and the company vocabulary is ``value_keys``.

**Values only.** Land facts are ``extracted_values`` rows with ``land_*`` fields under the
record keys ``land:{p}`` (one property) and ``land:{p}:party:{o}`` (one party of it). The
field CHECK and the record key's length already allow them, so ``extracted_values`` gets no
DDL, only new comments on ``record_key`` and ``field`` naming them. No typed copy: a
reviewer's fix updates the value, and a copy would keep the misread text.

**``value_keys``**: one row per person, place or property value, the spelling that value is
found by: ``latin`` (``latin_spelling``, casefolded, spaces collapsed; for a property
``links.normalise_identifier``) and ``sound`` (its sound keys joined by spaces; empty for a
property). It follows 0021's and 0036's rules: ``customer_id`` on every row, the
``customer_only`` policy (USING and WITH CHECK), (customer_id, id) foreign keys ON DELETE
CASCADE to the value and its document, and the least grants the app needs: SELECT, INSERT
and UPDATE of ``latin`` and ``sound`` (a key written again), never DELETE (a key goes with
its value: a save replacing the values, Delete forever, a purge). A plain table, never a
view: a view runs with its owner's rights and would offer another company's names. That
``document_id`` is the value's own document is the writer's rule (it copies it from the
value in the same statement): a check here would need a new key on ``extracted_values``.
B-tree indexes only: a GIN index is not used under row level security (0011).

**``kaagaz_op_daily``** (0034) counts the new usage kind ``land`` (the re-read's and the
land windows' calls, which 0023's CHECK already allows) as document spend: its body is read
back from the catalogue and ``'card', 'invoice', 'map'`` becomes ``'card', 'invoice',
'land', 'map'``, exactly once or the migration stops; its grants and comment are kept
(CREATE OR REPLACE). No table, column, index, grant or policy of 0034 changes.

The downgrade drops ``value_keys`` (derived: ``python -m kaagaz.cli land-facts --keys-only``
writes it again), restores 0034's function body and 0007's comments, and leaves the land
values in place: they are ordinary values to the older code.

Revision ID: 0049
Revises: 0048 (the AI save fix)
"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
DAILY = "public.kaagaz_op_daily(uuid, date, date, text)"
KINDS_0034 = "'card', 'invoice', 'map'"
KINDS_0049 = "'card', 'invoice', 'land', 'map'"


def _literal(comment: str) -> str:
    """A SQL string literal; each colon escaped, so text() never reads ":party" as a bind parameter."""
    return "'" + comment.replace("'", "''").replace(":", "\\:") + "'"


TABLE_COMMENT = (
    "The company vocabulary: one row per person, place or property value (land facts, PRD 6),"
    " the spellings it is found by. Derived from extracted_values and written again with it;"
    " every read joins documents through LIVE_CURRENT, so Trash and older versions need no write."
)
COLUMNS = {
    "customer_id": "→ customers.id, the customer the row belongs to (row level security filters on it).",
    "value_id": "→ extracted_values.id (same customer), the value this key spells. Deleted with it.",
    "document_id": "→ documents.id (same customer), the value's own document. Deleted with it.",
    "kind": "person (an owner, buyer or seller), place (a village, taluka, district or city) or"
    " property (a survey or khata number).",
    "place_level": "For a place, village, taluka, district or city; NULL for a person or a property.",
    "latin": "The value in Latin letters, casefolded, spaces collapsed (latin_spelling), 1 to 200"
    " characters; for a property its identifier without spaces, lower case, ASCII digits.",
    "sound": "The sound keys of the Latin spelling joined by spaces (Krishana and Krishna share"
    " one), at most 400 characters; empty for a property.",
    "created_at": "When the key was written.",
}
RECORD_KEY_0007 = "Groups one record's values, such as card, document, a sheet row or an invoice line."
FIELD_0007 = "Field name in lower snake case, such as total_amount or invoice_date."
RECORD_KEY_0049 = (
    "Groups one record's values, such as card, document, a sheet row, an invoice line, land:{p}"
    " (one property of a land record, in reading order) or land:{p}:party:{o} (one owner, buyer"
    " or seller of it)."
)
FIELD_0049 = (
    "Field name in lower snake case, such as total_amount or invoice_date. Land facts use"
    " land_survey_number, land_khata_number, land_village, land_taluka, land_district, land_city,"
    " land_area and land_unowned_share (only when printed) on land:{p}, and one of land_owner_name,"
    " land_buyer_name or land_seller_name with land_share (as printed) on land:{p}:party:{o}."
)

# The kinds list in 0034's body, replaced exactly once (the module docstring).
_REWRITE_DAILY = """
DO $body$
DECLARE
    v_def text := pg_get_functiondef('{daily}'::regprocedure);
BEGIN
    IF coalesce(array_length(string_to_array(v_def, $k${old}$k$), 1), 0) <> 2 THEN
        RAISE EXCEPTION 'kaagaz_op_daily does not list its document kinds exactly once';
    END IF;
    EXECUTE replace(v_def, $k${old}$k$, $k${new}$k$);
END
$body$
"""

UPGRADE = [
    """
    CREATE TABLE value_keys (
        customer_id uuid NOT NULL,
        value_id uuid NOT NULL,
        document_id uuid NOT NULL,
        kind text NOT NULL CHECK (kind IN ('person', 'place', 'property')),
        place_level text NULL CHECK (place_level IN ('village', 'taluka', 'district', 'city')),
        latin text NOT NULL CHECK (length(latin) BETWEEN 1 AND 200),
        sound text NOT NULL CHECK (length(sound) <= 400),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, value_id),
        CHECK ((kind = 'place') = (place_level IS NOT NULL)),
        FOREIGN KEY (customer_id, value_id) REFERENCES extracted_values (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX value_keys_latin ON value_keys (customer_id, kind, latin)",
    "CREATE INDEX value_keys_sound ON value_keys (customer_id, kind, sound)",
    "CREATE INDEX value_keys_document ON value_keys (customer_id, document_id)",
    f"GRANT SELECT, INSERT ON value_keys TO {APP_ROLE}",
    f"GRANT UPDATE (latin, sound) ON value_keys TO {APP_ROLE}",
    "ALTER TABLE value_keys ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON value_keys
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    f"COMMENT ON TABLE value_keys IS {_literal(TABLE_COMMENT)}",
    *[f"COMMENT ON COLUMN value_keys.{column} IS {_literal(comment)}" for column, comment in COLUMNS.items()],
    f"COMMENT ON COLUMN extracted_values.record_key IS {_literal(RECORD_KEY_0049)}",
    f"COMMENT ON COLUMN extracted_values.field IS {_literal(FIELD_0049)}",
    _REWRITE_DAILY.format(daily=DAILY, old=KINDS_0034, new=KINDS_0049),
]

DOWNGRADE = [
    _REWRITE_DAILY.format(daily=DAILY, old=KINDS_0049, new=KINDS_0034),
    f"COMMENT ON COLUMN extracted_values.field IS {_literal(FIELD_0007)}",
    f"COMMENT ON COLUMN extracted_values.record_key IS {_literal(RECORD_KEY_0007)}",
    "DROP TABLE value_keys",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
