"""Who can see what, in the database itself: the per-person rule, dormant behind a switch (PRD 7
phase 5, PR 9a, #606, Tushar, 15 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema and security.

* ``person_only``: one RESTRICTIVE policy FOR ALL TO ``kaagaz_app`` on ``documents``,
  ``folders``, ``trashed_folders`` and the 27 content tables (PRD 7 appendix A, with
  ``page_rotations``), so a statement that forgets the view still returns nothing hidden.
  0083's docstring said every policy is for PUBLIC; these are the first that name a role.
  ``kaagaz_worker`` is held by ``customer_only`` alone (with no person, its whole organisation),
  ``kaagaz_operator`` has no table right, and the owner skips both (no table has FORCE).
* The rule is written once. Each predicate is 0087's view text (0090's ``_rule``: the worker's
  whole-organisation case taken out, as no policy is for the worker), its alias made the table's
  name by a checked replace, inside ``EXISTS (SELECT 1 FROM kaagaz_access() a WHERE ...)``:
  owner-rights, so it is never inlined, runs once a statement and is rewound for each row, and
  the folders rule never calls back into itself.
* A content row is seen while its document is (``EXISTS`` on ``documents``, which runs the
  documents rule); ``document_links`` needs both documents, ``version_suggestions`` its document
  and a version of the candidate family, ``document_stories`` a version of its family. A row with
  no document (``model_usage``, ``organization_word_suggestions``) is the organisation's and stays
  seen. ``user_word_spots`` and ``document_stories`` keep ``own_user_only`` too.
* ``documents`` WITH CHECK also takes a new version of a family the person sees, in that family's
  folder (``kaagaz_family_visible``): a file given to them reaches the new version only by 0085's
  AFTER STATEMENT trigger, which has not run when the row is checked. A new file anywhere else
  must pass the rule itself, after 0085's BEFORE trigger set its stored values.
* The fast path (``kaagaz_sees_everything``, one InitPlan per documents or content reference):
  true when the switch is off for the organisation, or for an active person when either nothing
  is walled (``customers.walls_set``, 0089) and no file is private, held or locked, or they are
  Management and no file is locked. Then the per-row part never runs. "No file is" reads the
  partial index ``documents_kept``, live and exact (files in Trash keep their values). Folders and
  trashed folders have no fast path: their check is column and array arithmetic.
* The switch: ``kaagaz_person_rule`` (owner only: row level security on, no policy, no grant), a
  row per organisation that has the rule, a NULL ``for_customer_id`` for every organisation. No
  row means off, so this migration changes nothing on release day (``python -m kaagaz.cli
  person-rule``). ``reader_status.role``: the role each worker reported as, which turning the
  rule on checks (rows already there take the migrating login's name).
* Functions follow the house pattern: SECURITY DEFINER, an empty search path, every table named
  in full, nothing for PUBLIC, EXECUTE to the app and the worker role alike (0083's equality
  test).

Locks. ``documents_kept`` is built first, outside the transaction (as 0085: it commits what ran
before it), CONCURRENTLY unless a valid one is there, so documents takes writes while it is read;
it is empty on day one. Then env.py's lock_timeout is set again and the tables are locked ACCESS
EXCLUSIVE (CREATE POLICY's own lock) in 0004's order: documents first, then folders,
trashed_folders and the content tables by name. Catalog only otherwise; ``reader_status.role``
has a stable default (no rewrite). Run it in a quiet minute.

The downgrade (documents first) drops the policies, the functions, the switch, the column and the
index.

Revision ID: 0091
Revises: 0090
"""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from alembic import op
from sqlalchemy import text

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0091", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0087 = _load("0087_trashed_folders_view.py")
_0090 = _load("0090_access_api.py")
ROLES = _0090.ROLES
_literal = _0090._literal
APP_ROLE = "kaagaz_app"
POLICY = "person_only"


def _aliased(rule: str, alias: str, table: str, times: int) -> str:
    """``rule`` with alias ``alias`` (``d.``) made the table's name, exactly ``times`` times."""
    pattern = re.compile(rf"\b{alias}\.")
    if len(pattern.findall(rule)) != times:
        raise RuntimeError(f"the rule's text changed: {alias}.")
    return pattern.sub(f"public.{table}.", rule)


def _person(rule: str) -> str:
    return f"EXISTS (SELECT 1 FROM public.kaagaz_access() a WHERE {rule})"


# 0087's views' predicates (0090's _rule: the worker's case is false here, no policy is for it).
DOCUMENT_RULE = _0090._rule(_0087.VIEWS["visible_documents"])
FOLDER_RULE = _0090._rule(_0087.VIEWS["visible_folders"])
TRASHED_RULE = _0090._rule(_0087.VIEW)
DOCUMENT = _person(_aliased(DOCUMENT_RULE, "d", "documents", 12))
FOLDER = _person(_aliased(FOLDER_RULE, "f", "folders", 7))
TRASHED = _person(_aliased(TRASHED_RULE, "t", "trashed_folders", 7))

FAST = "(SELECT public.kaagaz_sees_everything())"
SWITCHED_OFF = "NOT (SELECT public.kaagaz_person_rule_on())"
_KEPT = "d.private_folder_id IS NOT NULL OR d.held_for IS NOT NULL OR d.locked_at IS NOT NULL"
KEPT_INDEX = "documents_kept"
KEPT_INDEX_SQL = (
    f"CREATE INDEX CONCURRENTLY {KEPT_INDEX} ON public.documents (customer_id)"
    " WHERE private_folder_id IS NOT NULL OR held_for IS NOT NULL OR locked_at IS NOT NULL"
)
KEPT_INDEX_COMMENT = (
    "The organisation's files kept to some people (private, held or locked; Trash too): the database"
    " rule's fast path asks whether there are none (kaagaz_sees_everything, PRD 7, #606)."
)


def _seen(table: str, column: str) -> str:
    """The content rule: the row's document is one the person can see (documents' own rule)."""
    return (
        f"EXISTS (SELECT 1 FROM public.documents d WHERE d.customer_id = public.{table}.customer_id"
        f" AND d.id = public.{table}.{column})"
    )


# The 27 content tables (PRD 7 appendix A; page_rotations, #600), each its rule after the fast path.
_ONE = (
    "corrections", "document_sections", "document_tags", "document_topics", "ensemble_readings",
    "ensemble_runs", "extracted_values", "invoice_lines", "invoices", "learning_removals", "page_blocks",
    "page_reads", "page_rotations", "pages", "pieces", "processing_runs", "reader_outputs", "source_pins",
    "table_cells", "user_word_spots", "value_keys", "word_changes",
)  # fmt: skip
_OPTIONAL = ("model_usage", "organization_word_suggestions")  # a row with no document: the organisation's
CONTENT = (
    {table: _seen(table, "document_id") for table in _ONE}
    | {table: f"public.{table}.document_id IS NULL OR " + _seen(table, "document_id") for table in _OPTIONAL}
    | {
        "document_links": _seen("document_links", "document_id")
        + " AND "
        + _seen("document_links", "linked_document_id"),
        "version_suggestions": _seen("version_suggestions", "document_id")
        + " AND EXISTS (SELECT 1 FROM public.documents c WHERE c.customer_id = public.version_suggestions.customer_id"
        " AND c.family_id = public.version_suggestions.candidate_family_id)",
        "document_stories": "EXISTS (SELECT 1 FROM public.documents d"
        " WHERE d.customer_id = public.document_stories.customer_id AND d.family_id = public.document_stories.family_id)",
    }
)
# Every covered table: (USING, WITH CHECK), documents first, then 0004's order.
POLICIES = {
    "documents": (
        f"{FAST} OR {DOCUMENT}",
        f"{FAST} OR {DOCUMENT} OR (public.documents.family_id <> public.documents.id"
        " AND public.kaagaz_family_visible(public.documents.family_id, public.documents.id,"
        " public.documents.folder_id))",
    ),
    "folders": (f"{SWITCHED_OFF} OR {FOLDER}",) * 2,
    "trashed_folders": (f"{SWITCHED_OFF} OR {TRASHED}",) * 2,
    **{table: (f"{FAST} OR ({rule})",) * 2 for table, rule in sorted(CONTENT.items())},
}
POLICY_COMMENT = (
    "PRD 7 (#606): the API role sees and writes only the rows of files and folders the current person"
    " may see, while the organisation's switch (kaagaz_person_rule) is on; the access views' rule."
)

SWITCH = """
    CREATE TABLE public.kaagaz_person_rule (
        for_customer_id uuid NULL REFERENCES public.customers (id) ON DELETE CASCADE,
        turned_on_at timestamptz NOT NULL DEFAULT now(),
        turned_on_by text NOT NULL,
        CONSTRAINT kaagaz_person_rule_one UNIQUE NULLS NOT DISTINCT (for_customer_id)
    )
    """
SWITCH_COMMENT = (
    "The organisations the per-person database rule is on for (PRD 7, #606): a row each, NULL for every"
    " organisation; none, off. Owner only: python -m kaagaz.cli person-rule on|off|status."
)
SWITCH_COLUMNS = {
    "for_customer_id": "customers.id the rule is on for; NULL: every organisation",
    "turned_on_at": "When it was turned on",
    "turned_on_by": "Who turned it on: the operating system user running the command, as given",
}
ROLE_COMMENT = "The database role the worker reported as (PRD 7, #606): person-rule on needs kaagaz_worker."

FUNCTIONS = {
    "kaagaz_person_rule_on()": """
    CREATE FUNCTION public.kaagaz_person_rule_on() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT EXISTS (SELECT 1 FROM public.kaagaz_person_rule r
                       WHERE r.for_customer_id IS NULL OR r.for_customer_id = public.kaagaz_current_customer())
    $$
    """,
    "kaagaz_sees_everything()": f"""
    CREATE FUNCTION public.kaagaz_sees_everything() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT NOT public.kaagaz_person_rule_on() OR coalesce(a.person IS NOT NULL AND (
            (NOT a.walled AND NOT EXISTS (SELECT 1 FROM public.documents d
                                          WHERE d.customer_id = public.kaagaz_current_customer() AND ({_KEPT})))
            OR (a.management AND NOT EXISTS (SELECT 1 FROM public.documents d
                                             WHERE d.customer_id = public.kaagaz_current_customer()
                                             AND d.locked_at IS NOT NULL))), false)
        FROM public.kaagaz_access() a
    $$
    """,
    "kaagaz_family_visible(uuid, uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_family_visible(p_family uuid, p_self uuid, p_folder uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT EXISTS (SELECT 1 FROM public.documents d, public.kaagaz_access() a
                       WHERE d.customer_id = public.kaagaz_current_customer() AND d.family_id = p_family
                       AND d.id <> p_self AND d.folder_id IS NOT DISTINCT FROM p_folder AND ({DOCUMENT_RULE}))
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_person_rule_on()": "Is the per-person database rule on for the current organisation (PRD 7, #606)?",
    "kaagaz_sees_everything()": "The database rule's fast path (PRD 7, #606): the switch is off, or the"
    " current person sees every file (nothing walled and nothing private, held or locked; or Management and"
    " nothing locked).",
    "kaagaz_family_visible(uuid, uuid, uuid)": "Does the current person see another version of this family in"
    " this folder (PRD 7, #606)? The documents rule's check of a new version.",
}

UPGRADE = [
    f"SET LOCAL lock_timeout = '{_0090.LOCK_TIMEOUT}'",  # the autocommit block ended env.py's
    f"COMMENT ON INDEX public.{KEPT_INDEX} IS {_literal(KEPT_INDEX_COMMENT)}",
    SWITCH,  # before any table lock: its key takes customers, the app's tree lock, which comes first
    f"COMMENT ON TABLE public.kaagaz_person_rule IS {_literal(SWITCH_COMMENT)}",
    *[
        f"COMMENT ON COLUMN public.kaagaz_person_rule.{column} IS {_literal(comment)}"
        for column, comment in SWITCH_COLUMNS.items()
    ],
    "ALTER TABLE public.kaagaz_person_rule ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON public.kaagaz_person_rule FROM PUBLIC",
    "ALTER TABLE public.reader_status ADD COLUMN role name NOT NULL DEFAULT current_user",
    f"COMMENT ON COLUMN public.reader_status.role IS {_literal(ROLE_COMMENT)}",
    *FUNCTIONS.values(),
    *[
        statement
        for name, comment in FUNCTION_COMMENTS.items()
        for statement in (
            f"COMMENT ON FUNCTION public.{name} IS {_literal(comment)}",
            f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{name} TO {ROLES}",
        )
    ],
    *[f"LOCK TABLE public.{table} IN ACCESS EXCLUSIVE MODE" for table in POLICIES],
    *[
        statement
        for table, (using, check) in POLICIES.items()
        for statement in (
            f"CREATE POLICY {POLICY} ON public.{table} AS RESTRICTIVE FOR ALL TO {APP_ROLE}"
            f" USING ({using}) WITH CHECK ({check})",
            f"COMMENT ON POLICY {POLICY} ON public.{table} IS {_literal(POLICY_COMMENT)}",
        )
    ],
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS public.kaagaz_person_rule",  # first: its key takes customers (the tree lock)
    *[f"LOCK TABLE public.{table} IN ACCESS EXCLUSIVE MODE" for table in POLICIES],
    *[f"DROP POLICY IF EXISTS {POLICY} ON public.{table}" for table in POLICIES],
    *[f"DROP FUNCTION IF EXISTS public.{name}" for name in reversed(FUNCTIONS)],
    "ALTER TABLE public.reader_status DROP COLUMN IF EXISTS role",
    f"DROP INDEX IF EXISTS public.{KEPT_INDEX}",
]

_VALID_INDEX = text(
    f"SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid = to_regclass('public.{KEPT_INDEX}')"
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        if not op.get_bind().execute(_VALID_INDEX).scalar():  # none yet, or a failed build's leftover
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{KEPT_INDEX}")
            op.execute(KEPT_INDEX_SQL)
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
