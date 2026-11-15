"""Locked files: a lock is real and safe (PRD 7 section 6, PR 12, #605, Tushar, 15 Nov 2026).
NEEDS VRUSHIT'S APPROVAL: schema and security.

PROVISIONAL number: 0093 follows 0090b until PR 9a (0091) and PR 9b (0092) merge; then it is
renumbered and ``down_revision`` points at 0092 (a one-line change).

* ``documents_locked_state``: a version in the state ``locked`` is locked, and a locked version is
  never ``ready`` or ``needs_reader`` (only received, clean, locked, failed or rejected), so no
  statement that reads "ready" files (search, Ask, totals, counts, spotting, the sweeps) can ever
  return one, whatever the code does. ``documents_locked_cleared``: a locked version holds no card,
  summary, type, date, languages or text fingerprint. Both added NOT VALID and validated outside
  the transaction (0085's way): no version is locked before this release.
* ``processing_runs`` gains the status ``locked``: a locked version's prepare-only run (its scan
  and page pictures, never a reading). It counts as no reading anywhere.
* ``users.locked_open_failures`` and ``users.locked_open_until`` (PR 13's block of wrong passwords
  when opening locked files), so one approval covers both PRs. Unused until PR 13; the app's
  table-wide UPDATE right on ``users`` (0001) covers them.
* Owner-rights functions, the house pattern: SECURITY DEFINER, an empty search path, every table
  named in full, every read filtered on ``kaagaz_current_customer()`` (an owner skips row level
  security), nothing for PUBLIC, the caller checked inside (an Admin of this organisation: a
  transaction with no person, the worker's, is refused). EXECUTE to the app and the worker role
  alike (0083's equality test), but for ``kaagaz_clear_derived``, which only ``kaagaz_lock`` runs.

  * ``kaagaz_lock(documents, open_to)``: all or nothing. The tree lock (the organisation's row),
    then every version of each family (families in id order, versions in
    ``kaagaz.db.trash.lock_family``'s order), then each file checked again under them: there,
    out of Trash, not locked yet, its current version of a kind with page pictures (LOCKABLE)
    and, once read, with every page pictured. Only when every file passes is anything written: on
    every version, everything derived cleared (appendix B), the lock set, the state ``locked``
    (``received`` when a page has no picture yet: the worker prepares it), its reading pause and
    try counts cleared, its running run ended (``superseded``, ``locked``: the run fence stops
    every late write), and the lock granted to ``open_to``. One row per file refused (and none
    written), or per version locked.
  * ``kaagaz_unlock(documents)``: all or nothing, the same locks. The lock and its grants go from
    every version; the current version is read again, billed to the Admin who unlocks
    (``read_requested_by``: the reading pause still holds it); older versions end ``failed``,
    ``unlocked``, each read again on request.
  * ``kaagaz_lock_grant(document, user)`` and ``kaagaz_lock_revoke(document, user)``: one person on
    every version, under the tree then the family lock; a grant only while the file is locked and
    only to a person of this organisation who is switched on.
  * ``kaagaz_clear_derived(version)``: appendix B for one version (rows deleted, the card's columns
    and every page's reading cleared); the original, the page pictures and sizes, the name, kind,
    page count, tags, stories, runs, usage, marks, page turns and learning removals are kept.
    ``learned_corrections`` made on the version go too (the PRD's appendix B left them out), and
    one remembered before its file was recorded that this version's corrections made. A rejected
    version keeps its code.
* Triggers, owner's rights, run by no role:

  * ``kaagaz_lock_grants_follow``: a membership, a name on a private folder or a file given taken
    away takes that person's lock grants with it, on the files of that department or private
    folder (but those still given to them directly), or on every version of the file taken back.
    Fails closed: the Admin ticks again (PRD 7 section 6).
  * ``kaagaz_refuse_locked_content``: an insert of a locked version's content into any content
    table fails (``kaagaz_locked``), and a link to one or a suggestion of its family. It takes the version FOR SHARE, which a lock's FOR NO KEY
    UPDATE waits for and which waits for a lock: either the insert sees the lock and fails, or
    the lock comes after it and deletes what it wrote. The backstop for a write path nobody has
    listed yet.

Cost: catalog only, but for the two CHECKs validated (``documents`` and ``processing_runs`` read
once each, SHARE UPDATE EXCLUSIVE: writes go on) and the CREATE TRIGGERs (each table locked a
moment). ``documents`` is altered first (0004's order); ``users`` last, catalog only.

The downgrade is refused while any file is locked ("unlock every file first"); then it drops the
triggers, the functions, the columns and the CHECKs and puts back 0004's run status CHECK (refused
while a run is ``locked``).

Revision ID: 0093
Revises: 0090b
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0093"
down_revision = "0090b"  # PROVISIONAL: 0092 once PR 9a and 9b merge
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0093", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0090 = _load("0090_access_api.py")
_literal = _0090._literal
ROLES = _0090.ROLES
LOCK_TIMEOUT = "10s"  # as env.py's

# PRD 7 section 6: the kinds Kaagaz draws as page pictures (kaagaz.db.locks.LOCKABLE).
LOCKABLE = ("pdf", "png", "jpeg", "tiff", "heic", "docx", "dotx", "doc", "pptx", "ppt")
_KINDS = ", ".join(f"'{kind}'" for kind in LOCKABLE)
# Word and PowerPoint: their PDF rendition is stored (kaagaz.reading.office.PDF_KINDS).
RENDERED = ("docx", "dotx", "doc", "pptx", "ppt")
_RENDERED = ", ".join(f"'{kind}'" for kind in RENDERED)
MAX_OPEN_TO = 50

LOCK_STATE = (
    "(status <> 'locked' OR locked_at IS NOT NULL)"
    " AND (locked_at IS NULL OR status IN ('received', 'clean', 'locked', 'failed', 'rejected'))"
)
LOCKED_CLEARED = (
    "locked_at IS NULL OR (doc_type IS NULL AND summary IS NULL AND card IS NULL AND document_date IS NULL"
    " AND cardinality(languages) = 0 AND text_minhash IS NULL AND text_bands IS NULL)"
)
RUN_STATUSES = "'running', 'ready', 'failed', 'needs_reader', 'rejected', 'superseded'"  # 0004's
CHECKS = {"documents_locked_state": "documents", "documents_locked_cleared": "documents"}
RUN_CHECK = "processing_runs_status_check"
# In autocommit, so the validations after it read each table without holding it from writes; a
# repeat (a validation failed) adds nothing twice.
ADD_CHECKS = f"""
    DO $$
    BEGIN
        SET LOCAL lock_timeout = '{LOCK_TIMEOUT}';
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'documents_locked_state') THEN
            ALTER TABLE public.documents
                ADD CONSTRAINT documents_locked_state CHECK ({LOCK_STATE}) NOT VALID,
                ADD CONSTRAINT documents_locked_cleared CHECK ({LOCKED_CLEARED}) NOT VALID;
        END IF;
        ALTER TABLE public.processing_runs DROP CONSTRAINT {RUN_CHECK},
            ADD CONSTRAINT {RUN_CHECK} CHECK (status IN ({RUN_STATUSES}, 'locked')) NOT VALID;
    END
    $$
    """
VALIDATED = {**CHECKS, RUN_CHECK: "processing_runs"}

COMMENTS = {
    "processing_runs.status": "running, ready, failed, needs_reader, rejected, superseded (a newer try took"
    " over), or locked (a locked version's scan and page pictures: never read, PRD 7).",
    "users.locked_open_failures": "Wrong passwords in a row when opening locked files (PRD 7, PR 13); 5 block"
    " opening them for 15 minutes.",
    "users.locked_open_until": "Opening locked files is refused until this time (PRD 7, PR 13). NULL = not"
    " blocked.",
}
CONSTRAINT_COMMENTS = {
    "documents_locked_state": "A version in the state locked is locked, and a locked version is never ready or"
    " needs_reader, so nothing that reads ready files reads it (PRD 7).",
    "documents_locked_cleared": "A locked version keeps no card, summary, type, date, languages or text"
    " fingerprint (PRD 7, appendix B).",
}

_ADMIN_ONLY = _0090._ADMIN_ONLY
_TREE_LOCK = "PERFORM 1 FROM public.customers c WHERE c.id = v_customer FOR NO KEY UPDATE;"
# Every version of the families of ``{ids}`` (uuid[]), families in id order, then each family in
# kaagaz.db.trash.lock_family's order: its first version first, then by id.
_FAMILIES_LOCK = """
        PERFORM 1 FROM public.documents d
        WHERE d.customer_id = v_customer AND d.family_id IN (
            SELECT x.family_id FROM public.documents x WHERE x.customer_id = v_customer AND x.id = ANY ({ids}))
        ORDER BY d.family_id, (d.id <> d.family_id), d.id FOR NO KEY UPDATE;"""
# Each asked file once, with its family (none: not found), whether in Trash and locked.
_ASKED = """
        WITH asked AS (SELECT DISTINCT a.id FROM unnest({ids}) AS a (id))
        SELECT a.id, x.family_id, x.deleted_at IS NOT NULL AS in_trash, x.locked_at IS NOT NULL AS locked
        FROM asked a LEFT JOIN public.documents x ON x.customer_id = v_customer AND x.id = a.id"""
# Every page of version ``{v}`` has its picture (none read yet: no).
_PICTURED = """
        coalesce({v}.page_count > 0 AND {v}.page_count = (SELECT count(*) FROM public.pages p
            WHERE p.customer_id = {v}.customer_id AND p.document_id = {v}.id AND p.has_image), false)"""

CLEAR_DERIVED = f"""
    CREATE FUNCTION public.kaagaz_clear_derived(p_version uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_family uuid;
    BEGIN
        {_ADMIN_ONLY}
        SELECT d.family_id INTO v_family FROM public.documents d WHERE d.customer_id = v_customer AND d.id = p_version;
        DELETE FROM public.reader_outputs x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.value_keys x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.extracted_values x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.pieces x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        -- A correction remembered before its file was recorded (source_document_id NULL): the one
        -- this version's corrections made (same field, same new value), before they are deleted.
        DELETE FROM public.learned_corrections x USING public.corrections k
            WHERE x.customer_id = v_customer AND x.source_document_id IS NULL
            AND k.customer_id = v_customer AND k.document_id = p_version
            AND x.field = k.field AND x.corrected_value = k.new_value;
        DELETE FROM public.corrections x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.invoice_lines x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.invoices x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.document_links x WHERE x.customer_id = v_customer
            AND (x.document_id = p_version OR x.linked_document_id = p_version);
        DELETE FROM public.page_reads x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.document_sections x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.document_topics x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.page_blocks x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.table_cells x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.ensemble_readings x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.ensemble_runs x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.word_changes x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.version_suggestions x WHERE x.customer_id = v_customer
            AND (x.document_id = p_version OR x.candidate_family_id = v_family);
        DELETE FROM public.organization_word_suggestions x
            WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.user_word_spots x WHERE x.customer_id = v_customer AND x.document_id = p_version;
        DELETE FROM public.learned_corrections x
            WHERE x.customer_id = v_customer AND x.source_document_id = p_version;
        UPDATE public.documents d SET doc_type = NULL, languages = '{{}}', summary = NULL, document_date = NULL,
            card = NULL, sensitivity = CASE WHEN d.sensitivity_set_by_person THEN d.sensitivity ELSE 'internal' END,
            text_minhash = NULL, text_bands = NULL, autofile = NULL, progress = NULL,
            failure_code = CASE WHEN d.status = 'rejected' THEN d.failure_code END
        WHERE d.customer_id = v_customer AND d.id = p_version;
        UPDATE public.pages p SET route = NULL, read_by = NULL, read_quality = 'good', read_issues = '{{}}',
            unsure_words = 0, illegible_words = 0, unpinned_lines = 0, accepted_by = NULL, accepted_at = NULL,
            read_again_requested_at = NULL, read_again_regions = NULL, read_thinking = NULL,
            read_resolution = NULL, read_pass = NULL, read_output_tokens = NULL
        WHERE p.customer_id = v_customer AND p.document_id = p_version;
    END
    $$
    """

# Each asked file's outcome, read under the locks: refused (not_found, in_trash, kind: its current
# version is of a kind with no page pictures; no_pictures: read, and a page has none),
# already_locked, or locked.
_OUTCOMES = f"""
        SELECT a.id, a.family_id, CASE
            WHEN a.family_id IS NULL THEN 'not_found'
            WHEN a.in_trash THEN 'in_trash'
            WHEN a.locked THEN 'already_locked'
            WHEN coalesce(c.detected_type NOT IN ({_KINDS}), true) THEN 'kind'
            WHEN c.status IN ('ready', 'needs_reader', 'failed', 'rejected') AND NOT {_PICTURED.format(v="c")}
                THEN 'no_pictures'
            ELSE 'locked' END AS outcome
        FROM ({_ASKED.format(ids="p_documents")}) a
        LEFT JOIN public.documents c ON c.customer_id = v_customer AND c.family_id = a.family_id AND c.is_current"""

LOCK = f"""
    CREATE FUNCTION public.kaagaz_lock(p_documents uuid[], p_open_to uuid[])
    RETURNS TABLE (document_id uuid, outcome text, version_id uuid, prepare boolean, rendition boolean)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_me uuid := (public.kaagaz_access()).person;
        v_already uuid[];
        v_version record;
    BEGIN
        {_ADMIN_ONLY}
        IF cardinality(p_open_to) > {MAX_OPEN_TO} OR EXISTS (
            SELECT 1 FROM unnest(p_open_to) AS p (id) WHERE NOT EXISTS (
                SELECT 1 FROM public.users u WHERE u.customer_id = v_customer AND u.id = p.id AND NOT u.disabled))
        THEN
            RAISE EXCEPTION 'only people of this organisation who are switched on' USING ERRCODE = 'check_violation';
        END IF;
        {_TREE_LOCK}
        {_FAMILIES_LOCK.format(ids="p_documents")}
        IF EXISTS (SELECT 1 FROM ({_OUTCOMES}) k WHERE k.outcome NOT IN ('locked', 'already_locked')) THEN
            RETURN QUERY SELECT k.id, k.outcome, NULL::uuid, false, false FROM ({_OUTCOMES}) k ORDER BY k.id;
            RETURN;
        END IF;
        SELECT array_agg(k.id ORDER BY k.id) INTO v_already FROM ({_OUTCOMES}) k WHERE k.outcome = 'already_locked';
        FOR v_version IN
            SELECT DISTINCT ON (d.id) k.id AS asked, d.id, d.status, d.detected_type,
                   {_PICTURED.format(v="d")} AS pictured
            FROM ({_OUTCOMES}) k
            JOIN public.documents d ON d.customer_id = v_customer AND d.family_id = k.family_id
            WHERE k.outcome = 'locked'
            ORDER BY d.id, k.id
        LOOP
            PERFORM public.kaagaz_clear_derived(v_version.id);
            UPDATE public.documents d SET locked_at = now(), locked_by = v_me,
                status = CASE WHEN d.status = 'rejected' THEN 'rejected'
                              WHEN v_version.pictured THEN 'locked' ELSE 'received' END,
                reading_held = NULL, reading_admitted = NULL, attempts = 0, max_attempts = NULL
            WHERE d.customer_id = v_customer AND d.id = v_version.id;
            UPDATE public.processing_runs r SET status = 'superseded', error_code = 'locked',
                finished_at = greatest(now(), r.started_at)
            WHERE r.customer_id = v_customer AND r.document_id = v_version.id AND r.status = 'running';
            INSERT INTO public.lock_grants (customer_id, document_id, user_id, given_by)
            SELECT v_customer, v_version.id, p.id, v_me FROM (SELECT DISTINCT q.id FROM unnest(p_open_to) AS q (id)) p
            ON CONFLICT DO NOTHING;
            document_id := v_version.asked;
            outcome := 'locked';
            version_id := v_version.id;
            prepare := v_version.status <> 'rejected' AND NOT v_version.pictured;
            rendition := v_version.detected_type IN ({_RENDERED});
            RETURN NEXT;
        END LOOP;
        RETURN QUERY SELECT a.id, 'already_locked', NULL::uuid, false, false
        FROM unnest(coalesce(v_already, '{{}}')) AS a (id);
    END
    $$
    """

UNLOCK = f"""
    CREATE FUNCTION public.kaagaz_unlock(p_documents uuid[])
    RETURNS TABLE (document_id uuid, outcome text, version_id uuid, read boolean)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_me uuid := (public.kaagaz_access()).person;
    BEGIN
        {_ADMIN_ONLY}
        {_TREE_LOCK}
        {_FAMILIES_LOCK.format(ids="p_documents")}
        IF EXISTS (SELECT 1 FROM ({_ASKED.format(ids="p_documents")}) a
                   WHERE a.family_id IS NULL OR a.in_trash OR NOT a.locked) THEN
            RETURN QUERY SELECT a.id, CASE WHEN a.family_id IS NULL THEN 'not_found' WHEN a.in_trash THEN 'in_trash'
                                          WHEN NOT a.locked THEN 'not_locked' ELSE 'unlocked' END, NULL::uuid, false
            FROM ({_ASKED.format(ids="p_documents")}) a ORDER BY a.id;
            RETURN;
        END IF;
        DELETE FROM public.lock_grants g USING public.documents d
        WHERE d.customer_id = v_customer
        AND d.family_id IN (SELECT x.family_id FROM public.documents x WHERE x.customer_id = v_customer
                            AND x.id = ANY (p_documents))
        AND g.customer_id = d.customer_id AND g.document_id = d.id;
        UPDATE public.processing_runs r SET status = 'superseded', error_code = 'unlocked',
            finished_at = greatest(now(), r.started_at)
        FROM public.documents d
        WHERE d.customer_id = v_customer
        AND d.family_id IN (SELECT x.family_id FROM public.documents x WHERE x.customer_id = v_customer
                            AND x.id = ANY (p_documents))
        AND r.customer_id = d.customer_id AND r.document_id = d.id AND r.status = 'running';
        RETURN QUERY
        WITH asked AS (
            SELECT DISTINCT ON (x.family_id) x.family_id, a.id FROM unnest(p_documents) AS a (id)
            JOIN public.documents x ON x.customer_id = v_customer AND x.id = a.id
            ORDER BY x.family_id, a.id
        ), unlocked AS (
            UPDATE public.documents d SET locked_at = NULL, locked_by = NULL,
                status = CASE WHEN d.status = 'rejected' THEN 'rejected' WHEN d.is_current THEN 'received'
                              ELSE 'failed' END,
                failure_code = CASE WHEN d.status = 'rejected' THEN d.failure_code WHEN d.is_current THEN NULL
                                    ELSE 'unlocked' END,
                read_requested_by = CASE WHEN d.is_current THEN v_me ELSE d.read_requested_by END,
                attempts = 0, max_attempts = NULL, progress = NULL, reading_held = NULL, reading_admitted = NULL
            FROM asked a
            WHERE d.customer_id = v_customer AND d.family_id = a.family_id
            RETURNING a.id AS asked, d.id, d.is_current AND d.status = 'received' AS read
        )
        SELECT u.asked, 'unlocked', u.id, u.read FROM unlocked u ORDER BY u.asked, u.id;
    END
    $$
    """

GRANT = f"""
    CREATE FUNCTION public.kaagaz_lock_grant(p_document uuid, p_user uuid) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_added integer;
    BEGIN
        {_ADMIN_ONLY}
        IF NOT EXISTS (SELECT 1 FROM public.users u WHERE u.customer_id = v_customer AND u.id = p_user
                       AND NOT u.disabled) THEN
            RETURN 'disabled';
        END IF;
        {_TREE_LOCK}
        {_FAMILIES_LOCK.format(ids="ARRAY[p_document]")}
        IF NOT EXISTS (SELECT 1 FROM public.documents x WHERE x.customer_id = v_customer AND x.id = p_document) THEN
            RETURN 'not_found';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.documents x WHERE x.customer_id = v_customer AND x.id = p_document
                       AND x.locked_at IS NOT NULL) THEN
            RETURN 'not_locked';
        END IF;
        INSERT INTO public.lock_grants (customer_id, document_id, user_id, given_by)
        SELECT v_customer, d.id, p_user, (public.kaagaz_access()).person FROM public.documents d
        JOIN public.documents x ON x.customer_id = d.customer_id AND x.family_id = d.family_id
        WHERE x.customer_id = v_customer AND x.id = p_document
        ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_added = ROW_COUNT;
        RETURN CASE WHEN v_added > 0 THEN 'granted' ELSE 'already' END;
    END
    $$
    """

REVOKE = f"""
    CREATE FUNCTION public.kaagaz_lock_revoke(p_document uuid, p_user uuid) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_taken integer;
    BEGIN
        {_ADMIN_ONLY}
        {_TREE_LOCK}
        {_FAMILIES_LOCK.format(ids="ARRAY[p_document]")}
        DELETE FROM public.lock_grants g USING public.documents x, public.documents d
        WHERE x.customer_id = v_customer AND x.id = p_document
        AND d.customer_id = x.customer_id AND d.family_id = x.family_id
        AND g.customer_id = d.customer_id AND g.document_id = d.id AND g.user_id = p_user;
        GET DIAGNOSTICS v_taken = ROW_COUNT;
        RETURN v_taken;
    END
    $$
    """

FOLLOW = """
    CREATE FUNCTION public.kaagaz_lock_grants_follow() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        IF TG_TABLE_NAME = 'document_shares' THEN
            DELETE FROM public.lock_grants g USING old_rows o, public.documents d, public.documents v
            WHERE d.customer_id = o.customer_id AND d.id = o.document_id
            AND v.customer_id = d.customer_id AND v.family_id = d.family_id
            AND g.customer_id = v.customer_id AND g.document_id = v.id AND g.user_id = o.user_id;
        ELSIF TG_TABLE_NAME = 'department_members' THEN
            DELETE FROM public.lock_grants g USING old_rows o, public.documents d
            WHERE g.customer_id = o.customer_id AND g.user_id = o.user_id
            AND d.customer_id = g.customer_id AND d.id = g.document_id AND d.department_id = o.department_id
            AND NOT EXISTS (SELECT 1 FROM public.document_shares s WHERE s.customer_id = g.customer_id
                            AND s.document_id = g.document_id AND s.user_id = g.user_id);
        ELSE
            DELETE FROM public.lock_grants g USING old_rows o, public.documents d
            WHERE g.customer_id = o.customer_id AND g.user_id = o.user_id
            AND d.customer_id = g.customer_id AND d.id = g.document_id AND d.private_folder_id = o.folder_id
            AND NOT EXISTS (SELECT 1 FROM public.document_shares s WHERE s.customer_id = g.customer_id
                            AND s.document_id = g.document_id AND s.user_id = g.user_id);
        END IF;
        RETURN NULL;
    END
    $$
    """

REFUSE = """
    CREATE FUNCTION public.kaagaz_refuse_locked_content() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_locked boolean;
    BEGIN
        -- Every version written to, locked FOR SHARE whether locked or not: a lock under way is
        -- waited for and then seen (the row is read again once it commits).
        SELECT bool_or(x.locked) INTO v_locked FROM (
            SELECT d.locked_at IS NOT NULL AS locked FROM public.documents d
            WHERE (d.customer_id, d.id) IN (SELECT n.customer_id, n.document_id FROM new_rows n)
            FOR SHARE OF d
        ) x;
        -- A link's other file, and every version of a suggestion's family, read with no row lock:
        -- two saves that link to each other hold their own families, so waiting on the other's
        -- would deadlock them. A row written as a lock commits can stay: a link is shown only
        -- between ready files, and kaagaz.db.versions.link refuses a locked family under its locks.
        IF TG_TABLE_NAME = 'document_links' THEN
            v_locked := coalesce(v_locked, false) OR EXISTS (
                SELECT 1 FROM public.documents d
                WHERE (d.customer_id, d.id) IN (SELECT n.customer_id, n.linked_document_id FROM new_rows n)
                AND d.locked_at IS NOT NULL);
        ELSIF TG_TABLE_NAME = 'version_suggestions' THEN
            v_locked := coalesce(v_locked, false) OR EXISTS (
                SELECT 1 FROM public.documents d
                WHERE (d.customer_id, d.family_id) IN (SELECT n.customer_id, n.candidate_family_id FROM new_rows n)
                AND d.locked_at IS NOT NULL);
        END IF;
        IF v_locked THEN
            RAISE EXCEPTION 'kaagaz_locked: a locked document''s content is never written'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END
    $$
    """
# Every table whose rows hold a version's content or a link to it (appendix B's rows deleted).
CONTENT_TABLES = (
    "reader_outputs",
    "pieces",
    "extracted_values",
    "value_keys",
    "corrections",
    "invoices",
    "invoice_lines",
    "document_links",
    "page_reads",
    "document_sections",
    "document_topics",
    "page_blocks",
    "table_cells",
    "ensemble_runs",
    "ensemble_readings",
    "word_changes",
    "version_suggestions",
    "organization_word_suggestions",
    "user_word_spots",
)
PLACE_TABLES = ("department_members", "private_folder_members", "document_shares")
TRIGGERS = {
    **{
        f"{table}_locked_refused": (
            table,
            f"AFTER INSERT ON {table} REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT"
            " EXECUTE FUNCTION kaagaz_refuse_locked_content()",
        )
        for table in CONTENT_TABLES
    },
    **{
        f"{table}_lock_grants_follow": (
            table,
            f"AFTER DELETE ON {table} REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT"
            " EXECUTE FUNCTION kaagaz_lock_grants_follow()",
        )
        for table in PLACE_TABLES
    },
}

FUNCTIONS = {
    "kaagaz_clear_derived(uuid)": CLEAR_DERIVED,
    "kaagaz_lock(uuid[], uuid[])": LOCK,
    "kaagaz_unlock(uuid[])": UNLOCK,
    "kaagaz_lock_grant(uuid, uuid)": GRANT,
    "kaagaz_lock_revoke(uuid, uuid)": REVOKE,
    "kaagaz_lock_grants_follow()": FOLLOW,
    "kaagaz_refuse_locked_content()": REFUSE,
}
GRANTED = ("kaagaz_lock(uuid[], uuid[])", "kaagaz_unlock(uuid[])", "kaagaz_lock_grant(uuid, uuid)",
           "kaagaz_lock_revoke(uuid, uuid)")  # fmt: skip
FUNCTION_COMMENTS = {
    "kaagaz_clear_derived(uuid)": "Everything Kaagaz worked out from one version deleted or cleared (PRD 7"
    " appendix B, and the corrections made on it); the original, pictures, name and person's own rows kept."
    " Run by kaagaz_lock only.",
    "kaagaz_lock(uuid[], uuid[])": "Lock files, all or nothing (PRD 7 section 6): every version cleared and"
    " locked under the tree then family locks, its running run ended, the lock granted to the people given."
    " One row per file refused, or per version locked. Admins.",
    "kaagaz_unlock(uuid[])": "Unlock files, all or nothing (PRD 7): the lock and its grants go from every"
    " version, the current one is read again billed to the Admin, older ones end failed (unlocked). Admins.",
    "kaagaz_lock_grant(uuid, uuid)": "Allow a person (switched on) to open a locked file, on every version"
    " (PRD 7): granted, already, not_locked, not_found or disabled. Admins.",
    "kaagaz_lock_revoke(uuid, uuid)": "Take a person's lock grant from every version of a file (PRD 7): how"
    " many rows went. Admins.",
    "kaagaz_lock_grants_follow()": "Trigger (PRD 7): a membership, a name on a private folder or a file given"
    " taken away takes that person's lock grants on its files with it (but files still given to them).",
    "kaagaz_refuse_locked_content()": "Trigger (PRD 7): an insert of a locked version's content fails; the"
    " version is taken FOR SHARE, so a lock under way is waited for.",
}

UPGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",  # the autocommit block ended env.py's
    *[
        f"COMMENT ON CONSTRAINT {name} ON {table} IS {_literal(CONSTRAINT_COMMENTS[name])}"
        for name, table in CHECKS.items()
    ],
    *FUNCTIONS.values(),
    *[
        statement
        for name, comment in FUNCTION_COMMENTS.items()
        for statement in (
            f"COMMENT ON FUNCTION public.{name} IS {_literal(comment)}",
            f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC",
        )
    ],
    *[f"GRANT EXECUTE ON FUNCTION public.{name} TO {ROLES}" for name in GRANTED],
    *[f"CREATE TRIGGER {name} {body}" for name, (_, body) in TRIGGERS.items()],
    # The busy table last, catalog only.
    "ALTER TABLE users ADD COLUMN locked_open_failures smallint NOT NULL DEFAULT 0"
    " CHECK (locked_open_failures >= 0), ADD COLUMN locked_open_until timestamptz NULL",
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
]

OLD_RUN_COMMENT = "running, ready, failed, needs_reader, rejected or superseded (a newer try took over)."
DOWNGRADE = [
    "LOCK TABLE documents IN ACCESS EXCLUSIVE MODE",  # first, 0004's order: no lock can come after
    # A locked file would be left locked with nothing to open or unlock it: refused.
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM public.documents WHERE locked_at IS NOT NULL) THEN
            RAISE EXCEPTION 'a file is locked; unlock every file first, then downgrade'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    *[f"DROP TRIGGER IF EXISTS {name} ON {table}" for name, (table, _) in TRIGGERS.items()],
    *[f"DROP FUNCTION IF EXISTS public.{name}" for name in reversed(FUNCTIONS)],
    *[f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}" for name, table in CHECKS.items()],
    f"ALTER TABLE processing_runs DROP CONSTRAINT {RUN_CHECK},"
    f" ADD CONSTRAINT {RUN_CHECK} CHECK (status IN ({RUN_STATUSES}))",
    f"COMMENT ON COLUMN processing_runs.status IS {_literal(OLD_RUN_COMMENT)}",
    "ALTER TABLE users DROP COLUMN IF EXISTS locked_open_failures, DROP COLUMN IF EXISTS locked_open_until",
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(ADD_CHECKS)
        for constraint, table in VALIDATED.items():
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}")
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
