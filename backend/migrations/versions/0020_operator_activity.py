"""What a platform operator may see of the whole platform: an activity feed and health
counts (super admin PR 5, council 6 Oct 2026, PROVISIONAL; change plan
Outside-Folder/docs/plans/2026-10-06-super-admin.md, awaiting Vrushit's approval: more
functions under the never-do 1 exception). Read-only: nothing here writes a row.

* ``kaagaz_op_activity``: every organisation's ``audit_log`` and ``platform_audit``,
  newest first, at most 100 rows a page, after a keyset cursor (time, source, id: the
  ids of the two logs overlap, so the source breaks a tie of time). Filters: one
  organisation, an action prefix. A row holds ONLY the action, the time, the
  organisation (id, name) and the target TYPE, and who acted as a kind: ``user`` (one of
  the organisation's own people, never which one or their role), ``operator`` (never
  which one; an operator's change shows in both logs, the organisation's copy found by
  its ``{"by": "operator"}`` mark, read inside the function only) or ``system`` (no
  person or operator recorded: the command line, the worker's purge, a failed sign-in).
  NEVER ``audit_log.details``, ``target_id``, an actor's id or e-mail, nor anything of a
  document (security, binding). A NULL limit is 50 (the API's default page).
* ``kaagaz_op_health``: one row per organisation with COUNTS only: every version out of
  Trash by status, the failed ones by ``failure_code`` (never ``admin_sql.failed_documents``,
  which returns file names), how many are being read and since when the oldest one.
* ``kaagaz_op_purges``: how many organisations were purged (0018) and swept (0019),
  and when last.

Each is SECURITY DEFINER with an empty search path and schema-qualified names, revoked
from PUBLIC and runnable by ``kaagaz_operator`` ONLY, and answers only an enabled
operator whose two-step secret is confirmed (no row otherwise), as 0016's list.

The index ``audit_log_feed`` (created_at DESC, id DESC) lets the feed read the newest
rows across every organisation without sorting the whole log (architect, binding). It is
built CONCURRENTLY, so audit writes are never blocked while it builds. That cannot run in
a transaction, so it is the FIRST step, in an autocommit block: alembic commits the
migrations before this one (0019 is then the recorded revision), builds the index, and
opens a new transaction for the functions. A build that fails leaves an INVALID index
and the revision at 0019; the next run drops it first (``IF EXISTS``) and builds again.
The 10-second lock wait env.py sets ends with that commit, so it is set again after the
block (the build itself waits, without blocking anyone, for transactions already open).

The downgrade drops the functions and the index (a plain DROP INDEX, in the
transaction, held at most the lock wait); nothing else changes.

Revision ID: 0020
Revises: 0019 (purge re-sweep)
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

OPERATOR_ROLE = "kaagaz_operator"
INDEX = "audit_log_feed"
LOCK_TIMEOUT = "10s"  # as env.py's
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_LIMIT = "least(greatest(coalesce(p_limit, 50), 1), 100)"  # NULL: 50


def _page(alias: str, source: str) -> str:
    """One log's rows on this page: an action prefix, then strictly after the cursor
    (time, source, id), newest first. The time bound alone is an index condition."""
    return f"""
          AND (p_action_prefix IS NULL OR starts_with({alias}.action, p_action_prefix))
          AND {alias}.created_at <= coalesce(p_before_time, 'infinity'::timestamptz)
          AND (p_before_time IS NULL OR {alias}.created_at < p_before_time
               OR ('{source}'::text, {alias}.id) < (p_before_source, p_before_id))
        ORDER BY {alias}.created_at DESC, {alias}.id DESC
        LIMIT {_LIMIT}
"""


_ORGANISATION = (
    "SELECT 'organisation'::text AS source, a.id, a.created_at, a.action, a.customer_id, a.target_type,"
    " CASE WHEN a.details ->> 'by' = 'operator' THEN 'operator'"
    " WHEN a.actor_user_id IS NULL THEN 'system' ELSE 'user' END AS actor"
    " FROM public.audit_log a"
)

FUNCTIONS = {
    # The feed. Three page-sized reads, merged: every organisation's log (the new index),
    # one organisation's log (0001's audit_log_listing) and the platform log. A filter on
    # the parameters alone is decided once per call, so only the right audit_log read runs.
    # ponytail: platform_audit has no index but its key; it holds operator actions only.
    # ponytail: an action prefix without an organisation walks audit_log_feed newest first
    # and filters, so a rare prefix reads the whole log; an (action, created_at) index if so.
    "kaagaz_op_activity(uuid, timestamptz, text, bigint, uuid, text, integer)": f"""
    CREATE FUNCTION public.kaagaz_op_activity(
        p_operator uuid, p_before_time timestamptz, p_before_source text, p_before_id bigint,
        p_customer uuid, p_action_prefix text, p_limit integer
    )
    RETURNS TABLE (source text, entry_id bigint, created_at timestamptz, action text,
                   customer_id uuid, customer_name text, target_type text, actor text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH feed AS (
            ({_ORGANISATION} WHERE p_customer IS NULL {_page("a", "organisation")})
            UNION ALL
            ({_ORGANISATION} WHERE a.customer_id = p_customer {_page("a", "organisation")})
            UNION ALL
            (SELECT 'platform'::text, p.id, p.created_at, p.action, p.subject_customer_id, p.target_type,
                    CASE WHEN p.operator_id IS NULL THEN 'system' ELSE 'operator' END
             FROM public.platform_audit p
             WHERE (p_customer IS NULL OR p.subject_customer_id = p_customer) {_page("p", "platform")})
        )
        SELECT f.source, f.id, f.created_at, f.action, f.customer_id, c.name, f.target_type, f.actor
        FROM feed f
        LEFT JOIN public.customers c ON c.id = f.customer_id
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY f.created_at DESC, f.source DESC, f.id DESC
        LIMIT {_LIMIT}
    $$
    """,
    # Counts per organisation, oldest organisation first. Every version out of Trash (a
    # newer version being read or failed is not current yet). Failed: failed, rejected and
    # cannot-be-read ones by their code. Being read: received or clean. One read of each
    # organisation's documents: grouped by status and, apart, by code (grouping sets).
    # ponytail: O(all documents) on every call, through documents_listing per organisation.
    "kaagaz_op_health(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_health(p_operator uuid)
    RETURNS TABLE (customer_id uuid, name text, status text, delete_after timestamptz,
                   documents_by_status jsonb, failed_by_code jsonb, in_progress bigint,
                   oldest_in_progress_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT c.id, c.name, c.status, c.delete_after, h.by_status, h.by_code, h.in_progress, h.oldest
        FROM public.customers c
        CROSS JOIN LATERAL (
            SELECT coalesce(jsonb_object_agg(x.status, x.n) FILTER (WHERE x.per_status), '{{}}'::jsonb)
                       AS by_status,
                   coalesce(jsonb_object_agg(x.code, x.failed) FILTER (WHERE NOT x.per_status AND x.failed > 0),
                            '{{}}'::jsonb) AS by_code,
                   coalesce(sum(x.reading) FILTER (WHERE x.per_status), 0)::bigint AS in_progress,
                   min(x.oldest) AS oldest
            FROM (
                SELECT d.status, coalesce(d.failure_code, 'unknown') AS code,
                       GROUPING(d.status) = 0 AS per_status, count(*) AS n,
                       count(*) FILTER (WHERE d.status IN ('failed', 'rejected', 'needs_reader')) AS failed,
                       count(*) FILTER (WHERE d.status IN ('received', 'clean')) AS reading,
                       min(d.created_at) FILTER (WHERE d.status IN ('received', 'clean')) AS oldest
                FROM public.documents d
                WHERE d.customer_id = c.id AND d.deleted_at IS NULL
                GROUP BY GROUPING SETS ((d.status), (coalesce(d.failure_code, 'unknown')))
            ) x
        ) h
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY c.created_at, c.id
    $$
    """,
    # Organisations purged (0018) and swept again a day later (0019): how many, when last.
    "kaagaz_op_purges(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_purges(p_operator uuid)
    RETURNS TABLE (purged bigint, last_purged_at timestamptz, swept bigint, last_swept_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT count(*) FILTER (WHERE p.action = 'org.purged'),
               max(p.created_at) FILTER (WHERE p.action = 'org.purged'),
               count(*) FILTER (WHERE p.action = 'org.swept'),
               max(p.created_at) FILTER (WHERE p.action = 'org.swept')
        FROM public.platform_audit p
        WHERE p.action IN ('org.purged', 'org.swept')
        HAVING EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
}

COMMENTS = {
    "kaagaz_op_activity": (
        "Operator feed of both audit logs, newest first, at most 100 rows: action, time, organisation,"
        " target type and actor kind only. Never details, target ids, people or document names."
    ),
    "kaagaz_op_health": (
        "Operator health per organisation: document counts by status, failed ones by failure code,"
        " how many are being read and the oldest. Counts only, never a document name."
    ),
    "kaagaz_op_purges": "Operator health: organisations purged and swept again, how many and when last.",
}
INDEX_COMMENT = "The operator activity feed across every organisation: newest rows first, read page by page."


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


UPGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    f"COMMENT ON INDEX public.{INDEX} IS {_literal(INDEX_COMMENT)}",
    *[
        statement
        for signature, create in FUNCTIONS.items()
        for statement in (
            create,
            f"COMMENT ON FUNCTION public.{signature} IS {_literal(COMMENTS[signature.split('(')[0]])}",
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
        )
    ],
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS],
    f"DROP INDEX IF EXISTS public.{INDEX}",
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX}")  # a failed build's leftover
        op.execute(f"CREATE INDEX CONCURRENTLY {INDEX} ON public.audit_log (created_at DESC, id DESC)")
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
