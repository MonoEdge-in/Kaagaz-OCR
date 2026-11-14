"""Who can see what: the two access views (PRD 7 section 10, "the query lock", #595, Tushar,
14 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema and security.

* ``visible_documents`` and ``visible_folders``: every column of their table, only the rows the
  current person may see by PRD 7 section 4 (documents: R0, then any of R1 to R5; folders: R1,
  R4, R5, and an Admin sees every folder's name). The worker role with no person sees its whole
  organisation; any other role with no person, and a person who is unknown, switched off or
  another organisation's, sees nothing. ``security_invoker``: the tables are read as the role
  that reads the view, so the organisation rule (and PR 9's per-person rule, 0087) still holds
  inside it; no table has FORCE ROW LEVEL SECURITY, so an owner's view would skip both. No
  ``security_barrier``: the view folds into each statement, so a list keeps its index and its
  LIMIT (#595 explains the choice).
* The person's access is worked out per statement, from the tables, by three owner-rights
  functions that read only ``kaagaz.customer_id`` and ``kaagaz.user_id``, so the app can claim no
  more than the person it names (decision 1 on #595). ``kaagaz_access()``: one row (the person,
  whether one is named, Management, Admin, their departments, the ones they head, the private
  folders they are named on, the organisation's Department only departments); every array is
  empty, never NULL. ``kaagaz_shared_with_me()`` and ``kaagaz_lock_open_to_me()``: the ids of the
  file versions given to them and the locked ones they may open, each read once a statement as
  a hashed set. Owner's rights, so PR 9's rule on ``folders`` or the access tables never calls
  back into them; so each names its customer itself.
* Each view's columns are its table's when it is made: a migration that adds or drops a column
  of ``documents`` or ``folders`` re-creates the view (tests/test_migration_0086_db.py checks).
* Catalog only: no table is locked, read or written. The app and the worker role get the same
  rights (0083's equality test): SELECT on the views, EXECUTE on the functions; PUBLIC none.

The downgrade drops the views, the functions and their row type.

Revision ID: 0086
Revises: 0085
"""

from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None

ROLES = "kaagaz_app, kaagaz_worker"


def _literal(words: str) -> str:
    return "'" + words.replace("'", "''") + "'"


_PERSON = "NULLIF(current_setting('kaagaz.user_id', true), '')"

ACCESS_SET = """
    CREATE TYPE public.kaagaz_access_set AS (
        person uuid, asked boolean, management boolean, admin boolean,
        departments uuid[], heads uuid[], named uuid[], closed uuid[]
    )
    """

FUNCTIONS = {
    "kaagaz_access": f"""
    CREATE FUNCTION public.kaagaz_access() RETURNS public.kaagaz_access_set
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT u.id, {_PERSON} IS NOT NULL, coalesce(u.management, false), coalesce(u.role = 'admin', false),
            ARRAY(SELECT m.department_id FROM public.department_members m
                  WHERE m.customer_id = c.id AND m.user_id = u.id),
            ARRAY(SELECT m.department_id FROM public.department_members m
                  WHERE m.customer_id = c.id AND m.user_id = u.id AND m.is_head),
            ARRAY(SELECT p.folder_id FROM public.private_folder_members p
                  WHERE p.customer_id = c.id AND p.user_id = u.id),
            ARRAY(SELECT f.id FROM public.folders f
                  WHERE f.customer_id = c.id AND f.parent_id IS NULL AND f.department_only
                  UNION ALL SELECT t.id FROM public.trashed_folders t
                  WHERE t.customer_id = c.id AND t.parent_id IS NULL AND t.department_only)
        FROM (SELECT public.kaagaz_current_customer() AS id) c
        LEFT JOIN public.users u ON u.customer_id = c.id AND u.id = {_PERSON}::uuid AND NOT u.disabled
    $$
    """,
    "kaagaz_shared_with_me": f"""
    CREATE FUNCTION public.kaagaz_shared_with_me() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' ROWS 10 AS $$
        SELECT s.document_id FROM public.document_shares s
        WHERE s.customer_id = public.kaagaz_current_customer() AND s.user_id = {_PERSON}::uuid
    $$
    """,
    "kaagaz_lock_open_to_me": f"""
    CREATE FUNCTION public.kaagaz_lock_open_to_me() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' ROWS 10 AS $$
        SELECT g.document_id FROM public.lock_grants g
        WHERE g.customer_id = public.kaagaz_current_customer() AND g.user_id = {_PERSON}::uuid
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_access": "The current person's access, for the access views (PRD 7): who they are (NULL: no"
    " active user of this customer), whether one is named, Management, Admin, departments, heads, named"
    " private folders, the organisation's Department only departments. Arrays are empty, never NULL.",
    "kaagaz_shared_with_me": "The file versions given to the current person (PRD 7, R2).",
    "kaagaz_lock_open_to_me": "The locked file versions the current person may open (PRD 7, R0).",
}

# The worker with no person: its whole organisation. Otherwise a known person only.
_ANYONE = "(a.person IS NULL AND NOT a.asked AND current_user = 'kaagaz_worker')"
# R5, then R4 (named, or head of its department), for alias {t}: written cheapest first, as an
# OR's parts run in order and stop at the first true one.
_PLACE = (
    "({t}.private_folder_id IS NULL AND {held}({t}.department_id IS NULL"
    " OR {t}.department_id <> ALL (a.closed) OR {t}.department_id = ANY (a.departments)))"
    " OR {t}.private_folder_id = ANY (a.named)"
    " OR ({t}.private_folder_id IS NOT NULL AND {t}.department_id = ANY (a.heads))"
)
VIEWS = {
    "visible_documents": (
        "CREATE VIEW public.visible_documents WITH (security_invoker = true) AS"
        " SELECT d.* FROM public.documents d, public.kaagaz_access() a"
        f" WHERE {_ANYONE} OR (a.person IS NOT NULL"
        " AND (d.locked_at IS NULL OR d.id IN (SELECT public.kaagaz_lock_open_to_me()))"  # R0
        " AND (a.management OR "  # R1
        + _PLACE.format(t="d", held="d.held_for IS NULL AND ")
        + " OR d.held_for = a.person"  # R3
        " OR d.id IN (SELECT public.kaagaz_shared_with_me())))"  # R2
    ),
    "visible_folders": (
        "CREATE VIEW public.visible_folders WITH (security_invoker = true) AS"
        " SELECT f.* FROM public.folders f, public.kaagaz_access() a"
        f" WHERE {_ANYONE} OR (a.person IS NOT NULL AND (a.management OR a.admin OR "
        + _PLACE.format(t="f", held="")
        + "))"
    ),
}
VIEW_COMMENTS = {
    "visible_documents": "The documents the current person may see (PRD 7 section 4: R0, then any of R1"
    " to R5); the worker role with no person, its whole organisation. Read it, not documents, for a person.",
    "visible_folders": "The folders the current person may see (PRD 7: R1, R4, R5; an Admin, every"
    " folder's name); the worker role with no person, its whole organisation.",
}

UPGRADE = [
    ACCESS_SET,
    *FUNCTIONS.values(),
    *[
        statement
        for name, comment in FUNCTION_COMMENTS.items()
        for statement in (
            f"COMMENT ON FUNCTION public.{name}() IS {_literal(comment)}",
            f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{name}() TO {ROLES}",
        )
    ],
    *VIEWS.values(),
    *[
        statement
        for name, comment in VIEW_COMMENTS.items()
        for statement in (
            f"COMMENT ON VIEW public.{name} IS {_literal(comment)}",
            f"GRANT SELECT ON public.{name} TO {ROLES}",
        )
    ],
]

DOWNGRADE = [
    *[f"DROP VIEW IF EXISTS public.{name}" for name in VIEWS],
    *[f"DROP FUNCTION IF EXISTS public.{name}()" for name in FUNCTIONS],
    "DROP TYPE IF EXISTS public.kaagaz_access_set",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
