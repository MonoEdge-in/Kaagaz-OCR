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
    f"CREATE INDEX CONCURRENTLY {KEPT_INDEX} ON public.documents (customer_id, locked_at)"
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

# --- the reads the rule would narrow (PRD 7 PR 9a, #606) -------------------------------------------
# Each reads the tables as the owner, for kaagaz_current_customer() only (the owner skips the
# organisation rule too), and answers what the person may know: ids they can see, numbers.
_ME = "public.kaagaz_current_customer()"
_A_PERSON = """
        IF (public.kaagaz_access()).person IS NULL THEN
            RAISE EXCEPTION 'only an active person of this organisation' USING ERRCODE = 'insufficient_privilege';
        END IF;"""
# db/documents.py's REFUSED_ALONE (#554): refused by its scan, its family's only version.
_REFUSED_ALONE = (
    "d.status = 'rejected' AND NOT EXISTS (SELECT 1 FROM public.documents o"
    " WHERE o.customer_id = d.customer_id AND o.family_id = d.family_id AND o.id <> d.id)"
)
_SEES = "EXISTS (SELECT 1 FROM public.visible_{view} v WHERE v.customer_id = {me} AND v.id = {id})"
READS = {
    # F4: a copy already in, by fingerprint and, when given, size (a folder upload's "Add new files
    # only": the browser's hash of a file of another size is not it) (PRD 7 section 5): none, visible
    # or locked_open (with its id), or hidden (no id). A refused copy alone is not in (#554). The
    # size comes back as given, never the document's.
    "kaagaz_duplicate(text[], bigint[])": f"""
    CREATE FUNCTION public.kaagaz_duplicate(p_sha256 text[], p_size bigint[] DEFAULT NULL)
    RETURNS TABLE (sha256 text, size_bytes bigint, answer text, document_id uuid)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        {_A_PERSON}
        RETURN QUERY SELECT h.sha256, h.size,
            CASE WHEN d.id IS NULL THEN 'none' WHEN NOT s.seen THEN 'hidden'
                 WHEN d.locked_at IS NOT NULL THEN 'locked_open' ELSE 'visible' END,
            CASE WHEN s.seen THEN d.id END
        FROM (SELECT DISTINCT x.sha256, x.size FROM unnest(p_sha256, p_size) AS x (sha256, size)) h
        LEFT JOIN public.documents d ON d.customer_id = {_ME} AND d.sha256 = h.sha256
            AND (h.size IS NULL OR d.size_bytes = h.size) AND NOT ({_REFUSED_ALONE})
        CROSS JOIN LATERAL (SELECT {_SEES.format(view="documents", me=_ME, id="d.id")} AS seen) s
        ORDER BY h.sha256, h.size;
    END
    $$
    """,
    # F5: which of these ids are still in the table, seen or not ("gone for good" is not "hidden").
    # Any session of the organisation: the truth fails closed (a hidden row is never "gone"), and it
    # runs once a feed row, so it reads no person.
    "kaagaz_existing(text, uuid[])": f"""
    CREATE FUNCTION public.kaagaz_existing(p_kind text, p_ids uuid[]) RETURNS SETOF uuid
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' ROWS 10 AS $$
    BEGIN
        IF p_kind = 'document' THEN
            RETURN QUERY SELECT d.id FROM public.documents d WHERE d.customer_id = {_ME} AND d.id = ANY (p_ids);
        ELSIF p_kind = 'folder' THEN
            RETURN QUERY SELECT f.id FROM public.folders f WHERE f.customer_id = {_ME} AND f.id = ANY (p_ids);
        ELSE
            RAISE EXCEPTION 'a document or a folder' USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    # F6: what the person can't see inside these trashed folders, each walked down
    # whole (100 levels, kaagaz.db.folders.MAX_DEPTH): numbers only.
    "kaagaz_hidden_inside(uuid[])": f"""
    CREATE FUNCTION public.kaagaz_hidden_inside(p_trashed uuid[])
    RETURNS TABLE (hidden_files bigint, hidden_folders bigint, any_files boolean)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        {_A_PERSON}
        RETURN QUERY WITH RECURSIVE trashed (id, trash_id, depth) AS (
            SELECT t.id, t.trash_id, 1 FROM public.trashed_folders t
            WHERE t.customer_id = {_ME} AND t.id = ANY (p_trashed)
            UNION ALL SELECT t.id, t.trash_id, trashed.depth + 1 FROM trashed
            JOIN public.trashed_folders t ON t.customer_id = {_ME} AND t.parent_id = trashed.id
            AND t.trash_id = trashed.trash_id WHERE trashed.depth < 101
        ), files AS (
            SELECT d.id FROM trashed JOIN public.documents d
            ON d.customer_id = {_ME} AND d.trashed_folder_id = trashed.id AND d.trash_id = trashed.trash_id
        )
        SELECT (SELECT count(*) FROM files x WHERE NOT {_SEES.format(view="documents", me=_ME, id="x.id")}),
            (SELECT count(*) FROM trashed x WHERE NOT {_SEES.format(view="trashed_folders", me=_ME, id="x.id")}),
            EXISTS (SELECT 1 FROM files);
    END
    $$
    """,
    # The topmost folder of p_folder's delete above it (itself when none is), on the whole table:
    # a restore brings it back, so a department there makes the restore an Admin's (F1) even when
    # the person can't see it. Its id and kind only.
    "kaagaz_trash_top(uuid)": f"""
    CREATE FUNCTION public.kaagaz_trash_top(p_folder uuid) RETURNS TABLE (id uuid, kind text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH RECURSIVE up (id, parent_id, trash_id, kind, depth) AS (
            SELECT t.id, t.parent_id, t.trash_id, t.kind, 1 FROM public.trashed_folders t
            WHERE t.customer_id = {_ME} AND t.id = p_folder
            UNION ALL SELECT t.id, t.parent_id, t.trash_id, t.kind, up.depth + 1 FROM up
            JOIN public.trashed_folders t ON t.customer_id = {_ME} AND t.id = up.parent_id
            AND t.trash_id = up.trash_id WHERE up.depth < 101
        )
        SELECT up.id, up.kind FROM up ORDER BY up.depth DESC LIMIT 1
    $$
    """,
    # kaagaz.db.folders._MOVE_CHECK, on the whole tree (a folder the person can't see would end a
    # walk early): the depth of p_parent, whether p_folder is on its way up (a cycle), and the
    # height of p_folder's subtree, so the 100-level limit and the cycle check hold for a member too.
    # NULL depth or height: no such folder. Numbers only.
    "kaagaz_folder_depth(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_folder_depth(p_parent uuid, p_folder uuid)
    RETURNS TABLE (depth integer, cycle boolean, height integer)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH RECURSIVE up (id, parent_id, depth) AS (
            SELECT f.id, f.parent_id, 1 FROM public.folders f WHERE f.customer_id = {_ME} AND f.id = p_parent
            UNION ALL SELECT f.id, f.parent_id, up.depth + 1 FROM up
            JOIN public.folders f ON f.customer_id = {_ME} AND f.id = up.parent_id WHERE up.depth < 101
        ), down (id, depth) AS (
            SELECT f.id, 1 FROM public.folders f WHERE f.customer_id = {_ME} AND f.id = p_folder
            UNION ALL SELECT f.id, down.depth + 1 FROM down
            JOIN public.folders f ON f.customer_id = {_ME} AND f.parent_id = down.id WHERE down.depth < 101
        )
        SELECT (SELECT max(up.depth) FROM up), EXISTS (SELECT 1 FROM up WHERE up.id = p_folder),
            (SELECT max(down.depth) FROM down)
    $$
    """,
    # F7 to F11: the organisation's numbers the caps and Home's key count, whole. Any session of the
    # organisation (the worker's too, which reads them for the same caps); numbers only.
    "kaagaz_storage_used()": f"""
    CREATE FUNCTION public.kaagaz_storage_used() RETURNS bigint
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT coalesce(sum(d.size_bytes), 0)::bigint FROM public.documents d WHERE d.customer_id = {_ME}
    $$
    """,
    "kaagaz_spent(timestamp with time zone, timestamp with time zone)": f"""
    CREATE FUNCTION public.kaagaz_spent(p_start timestamptz, p_end timestamptz)
    RETURNS TABLE (spent numeric, unknown boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT coalesce(sum(u.cost_usd), 0), coalesce(bool_or(u.cost_usd IS NULL), false) FROM public.model_usage u
        WHERE u.customer_id = {_ME} AND u.kind <> 'accuracy_check' AND u.created_at >= p_start AND u.created_at < p_end
    $$
    """,
    "kaagaz_ensemble_runs_this_hour()": f"""
    CREATE FUNCTION public.kaagaz_ensemble_runs_this_hour() RETURNS bigint
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT count(*) FROM public.ensemble_runs r
        WHERE r.customer_id = {_ME} AND r.created_at > now() - interval '1 hour'
    $$
    """,
    "kaagaz_word_suggestions_count()": f"""
    CREATE FUNCTION public.kaagaz_word_suggestions_count() RETURNS bigint
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT count(*) FROM public.organization_word_suggestions s
        WHERE s.customer_id = {_ME} AND s.status IN ('pending', 'approved')
    $$
    """,
    # answering/ready.py's _STAMP, as it was: hashed into a key, never shown.
    "kaagaz_ready_stamp()": f"""
    CREATE FUNCTION public.kaagaz_ready_stamp() RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT (SELECT count(*) || '/' || count(*) FILTER (WHERE d.deleted_at IS NULL) || '/'
                || coalesce(max(d.updated_at)::text, '') FROM public.documents d WHERE d.customer_id = {_ME}
                AND d.status NOT IN ('received', 'clean'))
            || '|' || (SELECT count(*) || '/' || coalesce(max(v.updated_at)::text, '')
                       FROM public.extracted_values v WHERE v.customer_id = {_ME})
            || '|' || (SELECT count(*) || '/' || coalesce(max(greatest(w.created_at, w.undone_at))::text, '')
                       FROM public.word_changes w WHERE w.customer_id = {_ME} AND w.before_text <> w.after_text)
            || '|' || (SELECT count(*) FILTER (WHERE p.embedding IS NULL) || '/'
                       || coalesce(string_agg(DISTINCT p.embedding_model, ','), '')
                       FROM public.pieces p WHERE p.customer_id = {_ME})
            || '|' || (SELECT c.access_version FROM public.customers c WHERE c.id = {_ME})
    $$
    """,
}
READ_COMMENTS = {
    "kaagaz_duplicate(text[], bigint[])": "A copy already in, by fingerprint (PRD 7 section 5, #606): none, visible,"
    " locked_open (with its id) or hidden (no id); a refused copy alone is not in. An active person.",
    "kaagaz_existing(text, uuid[])": "Which of these documents or folders still exist, seen or"
    " not (PRD 7, #606): gone for good is not hidden. Ids only, of the ids given.",
    "kaagaz_hidden_inside(uuid[])": "How many files and folders the current person can't see inside"
    " these trashed folders, and whether any file is (PRD 7, #606). An active person; numbers only.",
    "kaagaz_folder_depth(uuid, uuid)": "A folder's depth, whether a move makes a cycle, and a subtree's height,"
    " on the whole tree, for the 100-level limit (PRD 7, #606). Numbers only.",
    "kaagaz_trash_top(uuid)": "The topmost folder of a trashed folder's delete, its id and kind (PRD 7, #606).",
    "kaagaz_storage_used()": "The bytes the organisation's documents hold (#516, PRD 7 #606).",
    "kaagaz_spent(timestamp with time zone, timestamp with time zone)": "The organisation's model spend in a"
    " window, and whether part of it is unknown (PRD 7, #606).",
    "kaagaz_ensemble_runs_this_hour()": "The organisation's Ensemble runs in the last hour (PRD 7, #606).",
    "kaagaz_word_suggestions_count()": "The organisation's pending and approved word suggestions (PRD 7, #606).",
    "kaagaz_ready_stamp()": "Home's ready-answer key, organisation-wide, hashed and never shown (PRD 7, #602,"
    " #606).",
}
# F12 and F13: an Admin's names and numbers, whole (decision 20): today's statements, read as the
# owner for kaagaz_current_customer() only. An Admin, or the worker role with no person named
# (it sees the whole organisation anyway; the tests read these as it): the role a session SET ROLE
# to, which an owner-rights function doesn't change and kaagaz_app can't take. Anyone else (the
# API role with no person; a person who is not an Admin, switched off or unknown) gets
# insufficient_privilege.
_AN_ADMIN = """
    #variable_conflict use_column
    BEGIN
        IF NOT coalesce((SELECT x.admin OR (NOT x.asked AND current_setting('role') = 'kaagaz_worker')
                         FROM public.kaagaz_access() x), false) THEN
            RAISE EXCEPTION 'only an Admin' USING ERRCODE = 'insufficient_privilege';
        END IF;"""
_ADMIN_HEAD = "LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$"
ADMIN = {
    # admin._DOCUMENTS_BY_STATUS (as an object, status to count), _COUNTS and _KEPT.
    "kaagaz_admin_stats()": f"""
    CREATE FUNCTION public.kaagaz_admin_stats()
    RETURNS TABLE (documents_by_status jsonb, questions_total bigint, answered bigint, not_found bigint,
                   verified_answers bigint, needs_review_answers bigint, feedback_up bigint, feedback_down bigint,
                   open_review_items bigint, older_versions bigint, in_trash bigint)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY SELECT
            (SELECT coalesce(jsonb_object_agg(s.status, s.n), '{{}}'::jsonb) FROM (
                SELECT d.status, count(*) AS n FROM public.documents d
                WHERE d.customer_id = {_ME} AND d.deleted_at IS NULL AND d.is_current GROUP BY d.status) s),
            m.questions_total, m.answered, m.not_found, m.verified_answers, m.needs_review_answers,
            f.feedback_up, f.feedback_down, r.open_review_items, k.older_versions, k.in_trash
        FROM (SELECT count(*) FILTER (WHERE g.answer ->> 'status' IS DISTINCT FROM 'clarify') AS questions_total,
                     count(*) FILTER (WHERE g.answer ->> 'status' = 'answered') AS answered,
                     count(*) FILTER (WHERE g.answer ->> 'status' = 'not_found') AS not_found,
                     count(*) FILTER (WHERE g.answer ->> 'label' = 'verified') AS verified_answers,
                     count(*) FILTER (WHERE g.answer ->> 'label' = 'needs_review') AS needs_review_answers
              FROM public.messages g WHERE g.customer_id = {_ME} AND g.role = 'assistant') m,
             (SELECT count(*) FILTER (WHERE b.rating = 'up') AS feedback_up,
                     count(*) FILTER (WHERE b.rating = 'down') AS feedback_down
              FROM public.feedback b WHERE b.customer_id = {_ME}) f,
             (SELECT count(*) AS open_review_items FROM public.extracted_values v
              JOIN public.documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
              WHERE v.customer_id = {_ME} AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
              AND v.label = 'needs_review') r,
             (SELECT count(*) FILTER (WHERE d.deleted_at IS NULL AND NOT d.is_current) AS older_versions,
                     count(DISTINCT d.family_id) FILTER (WHERE d.deleted_at IS NOT NULL) AS in_trash
              FROM public.documents d WHERE d.customer_id = {_ME}) k;
    END
    $$
    """,
    # admin._MEMBERS (#386): every user with their numbers in [p_lo, p_hi); NULL, no bound.
    "kaagaz_admin_member_numbers(timestamp with time zone, timestamp with time zone)": f"""
    CREATE FUNCTION public.kaagaz_admin_member_numbers(p_lo timestamptz, p_hi timestamptz)
    RETURNS TABLE (id uuid, display_name text, email text, role text, disabled boolean,
                   last_active_at timestamptz, documents_uploaded bigint, pages_read numeric,
                   questions_asked bigint, values_verified bigint)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY WITH w AS (SELECT coalesce(p_lo, '-infinity'::timestamptz) AS lo,
                                       coalesce(p_hi, 'infinity'::timestamptz) AS hi)
        SELECT u.id, u.display_name, u.email, u.role, u.disabled, GREATEST(u.last_seen_at, u.last_login_at),
            coalesce(d.documents, 0), coalesce(d.pages, 0), coalesce(q.questions, 0), coalesce(v.verified, 0)
        FROM public.users u
        LEFT JOIN (SELECT d.uploaded_by, count(*) AS documents, sum(p.pages) AS pages FROM public.documents d
                   CROSS JOIN w CROSS JOIN LATERAL (SELECT count(*) AS pages FROM public.pages p
                       WHERE p.customer_id = d.customer_id AND p.document_id = d.id AND p.route IS NOT NULL) p
                   WHERE d.customer_id = {_ME} AND d.uploaded_by IS NOT NULL AND d.created_at >= w.lo
                   AND d.created_at < w.hi GROUP BY d.uploaded_by) d ON d.uploaded_by = u.id
        LEFT JOIN (SELECT c.user_id, count(*) AS questions FROM public.conversations c
                   JOIN public.messages m ON m.customer_id = c.customer_id AND m.conversation_id = c.id CROSS JOIN w
                   WHERE c.customer_id = {_ME} AND m.role = 'assistant'
                   AND m.answer ->> 'status' IS DISTINCT FROM 'clarify'
                   AND m.created_at >= w.lo AND m.created_at < w.hi GROUP BY c.user_id) q ON q.user_id = u.id
        LEFT JOIN (SELECT k.corrected_by, count(*) AS verified FROM public.corrections k CROSS JOIN w
                   WHERE k.customer_id = {_ME} AND k.created_at >= w.lo AND k.created_at < w.hi
                   GROUP BY k.corrected_by) v ON v.corrected_by = u.id
        WHERE u.customer_id = {_ME} ORDER BY lower(u.display_name), u.id;
    END
    $$
    """,
    # admin._FAILED and _FAILED_COUNT (PRD 3 Z1): the page, and the total on every row; one row with
    # no id (the total alone) when the page is empty.
    "kaagaz_admin_failed(integer, integer)": f"""
    CREATE FUNCTION public.kaagaz_admin_failed(p_limit integer, p_offset integer)
    RETURNS TABLE (id uuid, display_name text, detected_type text, status text, failure_code text,
                   page_count integer, attempts integer, max_attempts integer, last_run_at timestamptz,
                   total bigint)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY SELECT p.id, p.display_name, p.detected_type, p.status, p.failure_code, p.page_count,
            p.attempts, p.max_attempts, p.last_run_at, t.total
        FROM (SELECT count(*) AS total FROM public.documents d WHERE d.customer_id = {_ME}
              AND d.deleted_at IS NULL AND d.is_current AND d.status IN ('failed', 'needs_reader')) t
        LEFT JOIN LATERAL (
            SELECT d.id, d.display_name, d.detected_type, d.status, d.failure_code, d.page_count, d.attempts,
                d.max_attempts, r.started_at AS last_run_at, d.created_at
            FROM public.documents d
            LEFT JOIN LATERAL (SELECT pr.started_at FROM public.processing_runs pr
                               WHERE pr.customer_id = {_ME} AND pr.document_id = d.id
                               ORDER BY pr.started_at DESC LIMIT 1) r ON true
            WHERE d.customer_id = {_ME} AND d.deleted_at IS NULL AND d.is_current
            AND d.status IN ('failed', 'needs_reader')
            ORDER BY d.created_at DESC, d.id DESC LIMIT p_limit OFFSET p_offset
        ) p ON true
        ORDER BY p.created_at DESC, p.id DESC;
    END
    $$
    """,
    # runs._PAGES_BY_ROUTE and _READER_PAGES: the pages of the runs started in [p_start, p_end).
    "kaagaz_admin_reader_pages(timestamp with time zone, timestamp with time zone)": f"""
    CREATE FUNCTION public.kaagaz_admin_reader_pages(p_start timestamptz, p_end timestamptz)
    RETURNS TABLE (pages_cells bigint, pages_text_layer bigint, pages_ocr bigint, pages_blank bigint,
                   pages_gemini bigint, pages_local bigint, pages_cached bigint, pages_read_again bigint,
                   input_tokens bigint, output_tokens bigint)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY SELECT coalesce(sum(r.pages_cells), 0)::bigint, coalesce(sum(r.pages_text_layer), 0)::bigint,
            coalesce(sum(r.pages_ocr), 0)::bigint, coalesce(sum(r.pages_blank), 0)::bigint,
            coalesce(sum(r.pages_gemini), 0)::bigint, coalesce(sum(r.pages_local), 0)::bigint,
            coalesce(sum(r.pages_cached), 0)::bigint, coalesce(sum(r.pages_read_again), 0)::bigint,
            coalesce(sum(r.input_tokens), 0)::bigint, coalesce(sum(r.output_tokens), 0)::bigint
        FROM public.processing_runs r
        WHERE r.customer_id = {_ME} AND r.started_at >= p_start AND r.started_at < p_end;
    END
    $$
    """,
    # usage._REPORT (SP2): the period's spend grouped six ways, names looked up after the grouping.
    "kaagaz_admin_usage(timestamp with time zone, timestamp with time zone, text, uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_usage(p_start timestamptz, p_end timestamptz, p_tz text, p_user uuid)
    RETURNS TABLE (dimension text, user_id uuid, model text, kind text, day date, document_id uuid,
                   of_document boolean, calls bigint, cached_calls bigint, input_tokens numeric,
                   output_tokens numeric, reasoning_tokens numeric, cost_usd numeric, cost_estimated boolean,
                   cost_part_unknown boolean, user_name text, model_name text, document_name text)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY WITH r AS (
            SELECT u.user_id, u.model, u.kind, CAST(timezone(p_tz, u.created_at) AS date) AS day, u.document_id,
                (u.document_id IS NOT NULL OR (u.conversation_id IS NULL AND u.kind IN
                 ('scan', 'upgrade', 'card', 'invoice', 'land', 'map', 'careful', 'ensemble_scan', 'ulc_scan')))
                AS of_document,
                u.cached, u.calls, u.input_tokens, u.output_tokens, u.reasoning_tokens, u.cost_usd, u.cost_estimated
            FROM public.model_usage u
            WHERE u.customer_id = {_ME} AND u.kind <> 'accuracy_check'
            AND u.created_at >= p_start AND u.created_at < p_end AND (p_user IS NULL OR u.user_id = p_user)
        ), g AS (
            SELECT CASE WHEN GROUPING(r.user_id) = 0 THEN 'user' WHEN GROUPING(r.model) = 0 THEN 'model'
                WHEN GROUPING(r.kind) = 0 THEN 'kind' WHEN GROUPING(r.day) = 0 THEN 'day'
                WHEN GROUPING(r.document_id) = 0 THEN 'document' ELSE 'total' END AS dimension,
                r.user_id, r.model, r.kind, r.day, r.document_id, r.of_document,
                sum(r.calls) AS calls, coalesce(sum(r.calls) FILTER (WHERE r.cached), 0) AS cached_calls,
                sum(r.input_tokens) AS input_tokens, sum(r.output_tokens) AS output_tokens,
                sum(r.reasoning_tokens) AS reasoning_tokens, sum(r.cost_usd) AS cost_usd,
                bool_or(r.cost_estimated) AS cost_estimated, bool_or(r.cost_usd IS NULL) AS cost_part_unknown
            FROM r
            GROUP BY GROUPING SETS ((), (r.user_id), (r.model), (r.kind), (r.day), (r.document_id, r.of_document))
        )
        SELECT g.dimension, g.user_id, g.model, g.kind, g.day, g.document_id, g.of_document, g.calls,
            g.cached_calls, g.input_tokens, g.output_tokens, g.reasoning_tokens, g.cost_usd, g.cost_estimated,
            g.cost_part_unknown, x.display_name, m.name, d.display_name
        FROM g
        LEFT JOIN public.users x ON x.customer_id = {_ME} AND x.id = g.user_id
        LEFT JOIN public.model_catalog m ON m.id = g.model
        LEFT JOIN public.documents d ON d.customer_id = {_ME} AND d.id = g.document_id
        ORDER BY g.dimension, g.day, g.cost_usd DESC NULLS LAST, g.calls DESC,
            g.user_id, g.model, g.kind, g.document_id;
    END
    $$
    """,
    # listing._USAGE_ROWS (SP2): the spend CSV, one row per day, user, model and kind.
    "kaagaz_admin_usage_rows(timestamp with time zone, timestamp with time zone, text, uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_usage_rows(p_start timestamptz, p_end timestamptz, p_tz text, p_user uuid)
    RETURNS TABLE (day date, user_name text, model text, kind text, calls bigint, tokens numeric,
                   cost_usd numeric, cost_estimated boolean, cost_part_unknown boolean)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY SELECT (u.created_at AT TIME ZONE p_tz)::date, max(us.display_name), u.model, u.kind,
            sum(u.calls), sum(u.input_tokens + u.output_tokens), sum(u.cost_usd), bool_or(u.cost_estimated),
            bool_or(u.cost_usd IS NULL)
        FROM public.model_usage u
        LEFT JOIN public.users us ON us.customer_id = u.customer_id AND us.id = u.user_id
        WHERE u.customer_id = {_ME} AND u.kind <> 'accuracy_check'
        AND u.created_at >= p_start AND u.created_at < p_end AND (p_user IS NULL OR u.user_id = p_user)
        GROUP BY 1, u.user_id, u.model, u.kind ORDER BY 1, 2 NULLS FIRST, u.model, u.kind;
    END
    $$
    """,
    # folders._SUMMARY (#537): what the folder holds at every depth, its own row counted in folders
    # (0: no such folder).
    "kaagaz_admin_folder_summary(uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_folder_summary(p_folder uuid)
    RETURNS TABLE (folders bigint, files bigint, size_bytes numeric)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY WITH RECURSIVE down (id, depth) AS (
            SELECT f.id, 1 FROM public.folders f WHERE f.customer_id = {_ME} AND f.id = p_folder
            UNION ALL SELECT f.id, down.depth + 1 FROM down
            JOIN public.folders f ON f.customer_id = {_ME} AND f.parent_id = down.id WHERE down.depth < 101
        ), live AS (
            SELECT d.family_id, d.size_bytes FROM public.documents d WHERE d.customer_id = {_ME}
            AND d.deleted_at IS NULL AND d.folder_id IN (SELECT down.id FROM down)
        )
        SELECT (SELECT count(*) FROM down), (SELECT count(DISTINCT live.family_id) FROM live),
            (SELECT coalesce(sum(live.size_bytes), 0) FROM live);
    END
    $$
    """,
    # F13: the names of documents (any version) for the Admin's log, where its join found none.
    "kaagaz_admin_document_names(uuid[])": f"""
    CREATE FUNCTION public.kaagaz_admin_document_names(p_ids uuid[])
    RETURNS TABLE (id uuid, display_name text, version_no integer)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY SELECT d.id, d.display_name, d.version_no FROM public.documents d
        WHERE d.customer_id = {_ME} AND d.id = ANY (p_ids);
    END
    $$
    """,
    # access._GIVEN_TO (#601): the live files given to a person, one per family, by their true names.
    "kaagaz_admin_given_to(uuid)": f"""
    CREATE FUNCTION public.kaagaz_admin_given_to(p_user uuid)
    RETURNS TABLE (id uuid, display_name text, folder_id uuid, given_at timestamptz, given_by uuid)
    {_ADMIN_HEAD}{_AN_ADMIN}
        RETURN QUERY SELECT DISTINCT ON (c.family_id) c.id, c.display_name, c.folder_id, s.given_at, s.given_by
        FROM public.document_shares s
        JOIN public.documents v ON v.customer_id = s.customer_id AND v.id = s.document_id
        JOIN public.documents c ON c.customer_id = v.customer_id AND c.family_id = v.family_id AND c.is_current
        WHERE s.customer_id = {_ME} AND s.user_id = p_user AND c.deleted_at IS NULL
        ORDER BY c.family_id, s.given_at;
    END
    $$
    """,
}
_NUMBERS = " An Admin (or the worker role); names and numbers only (PRD 7 decision 20, #606)."
ADMIN_COMMENTS = {
    "kaagaz_admin_stats()": "The admin screen's numbers, whole: documents by status, questions, feedback,"
    " review items, older versions and Trash." + _NUMBERS,
    "kaagaz_admin_member_numbers(timestamp with time zone, timestamp with time zone)": "Each member's numbers"
    " in a window (#386)." + _NUMBERS,
    "kaagaz_admin_failed(integer, integer)": "A page of the failed or unreadable files and their total (PRD 3"
    " Z1); one row with no id when the page is empty." + _NUMBERS,
    "kaagaz_admin_reader_pages(timestamp with time zone, timestamp with time zone)": "The pages read in a"
    " window by route and by reader, and the tokens billed (PRD 2 R6, PRD 5 U9)." + _NUMBERS,
    "kaagaz_admin_usage(timestamp with time zone, timestamp with time zone, text, uuid)": "The Usage screen's"
    " spend in a window, grouped six ways (SP2)." + _NUMBERS,
    "kaagaz_admin_usage_rows(timestamp with time zone, timestamp with time zone, text, uuid)": "The spend CSV:"
    " one row per day, user, model and kind (SP2)." + _NUMBERS,
    "kaagaz_admin_folder_summary(uuid)": "What a folder holds at every depth, its own row counted in folders"
    " (#537)." + _NUMBERS,
    "kaagaz_admin_document_names(uuid[])": "The names of these documents, any version, for the Admin's log."
    + _NUMBERS,
    "kaagaz_admin_given_to(uuid)": "The live files given to a person, one per family, by their true names"
    " (#601)." + _NUMBERS,
}
FUNCTIONS |= READS | ADMIN
FUNCTION_COMMENTS |= READ_COMMENTS | ADMIN_COMMENTS

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
    # Each lock waits 2 seconds at most, so a busy table stops the migration before it queues every
    # request behind it: it is run by hand, at a quiet time.
    "SET LOCAL lock_timeout = '2s'",
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
