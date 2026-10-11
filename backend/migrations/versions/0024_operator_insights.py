"""What a platform operator may see of how the platform is used, how well it reads and
answers, and what each model costs (operator insights, council 11 Oct 2026, F1-F4,
PROVISIONAL; NEEDS VRUSHIT'S APPROVAL: four more read-only functions under the never-do 1
exception, the first cross-customer reads of ``processing_runs``, ``messages``' answer
status keys and ``feedback.rating``). Read-only: nothing here writes a row.

* ``kaagaz_op_daily`` (F1): per organisation and day, uploads, sign-ins, questions,
  corrections, runs, pages and spend. Only days with something.
* ``kaagaz_op_org_health`` (F2): per organisation, active people (a count, never who),
  answers and not-found answers of the last 30 days, the open review (exactly the admin's
  ``open_review_items``, db/admin.py ``_COUNTS``), this month's spend against its budget
  and the last day it was active (an upload, a question or a run, within 366 days).
* ``kaagaz_op_quality`` (F3): reading, answers and review per organisation, plus one
  platform row (``customer_id`` NULL, through GROUPING SETS). The platform row's outcome
  values AND the totals they are shares of (answers, feedback, values_total,
  corrections) count only the organisations whose own total reaches K: every number in
  it is then a sum of numbers their own rows already show, so subtracting the shown
  organisations from it reveals nothing of one below K. Its rates are "over the
  organisations with enough data"; the true platform volume is the sum of the
  organisations' rows (volumes are never NULL).
* ``kaagaz_op_model_board`` (F4): per model, runs, speed, page signals and spend. No
  organisation. Observational, not an A/B test.

Days are in ``p_zone`` (the API passes TIMEZONE), both ends included: the window is
[p_from 00:00, p_to + 1 day 00:00) in that zone. A backwards range, one of more than 366
days, a NULL day and an unknown zone are refused (check_violation). Outcome values are
NULL below K = 10 of their own total (a p95 below 20), decided here, so no route can
show them; volumes are never NULL. Never a person, document, conversation, message,
batch, folder or tag, nor any name, e-mail, text, question, answer, citation, feedback
reason, audit details value or extracted value: the functions have no column for one.
``audit_log.details ->> 'choice'`` (review.resolve) and ``messages.answer``'s status,
label, route and citation count are read inside the functions only.

What the code writes, verified 11 Oct 2026 (the names used below):

* audit actions: ``document.upload`` and ``document.version`` (api/routers/documents.py
  435, 525; a duplicate upload writes none, 448), ``auth.login`` (auth.py 135),
  ``auth.admin_login`` (auth.py 148, 167, 222), ``chat.ask`` (chat.py 164),
  ``review.resolve`` with ``details.choice`` first, second or typed (review.py 44, 131-135).
* run status and codes: a ready run's ``error_code`` is ``model_<code>`` when a model call
  failed and the document still ended ready (pipeline.py 648, 981: degraded); a failed
  run caused by the platform is ``internal_error`` (pipeline.py 835),
  ``retries_exhausted`` (jobs/worker.py 68, db/documents.py 762) or ``<backend>_unavailable``
  (pipeline.py 1590, scanning/gate.py 127). ``config ->> 'read_model'`` is the job's model
  on every Gemini run, a text-layer one too, and absent when keyless (pipeline.py 840,
  1281); ``config -> 'seconds_reading'`` a number of seconds when the reading finished
  (pipeline.py 1414).
* pages: ``read_by`` is the model id, ``rapidocr``, ``text_layer`` or ``cells``
  (pipeline.py 328, reading/ocr.py 106); a page the model handed to the local reader is
  ``rapidocr`` with the issue ``read_locally`` or ``reader_refused`` (reading/ocr.py 178), so
  F4 counts such a page under the model of its document's newest ready run (else
  ``read_by``). Issue codes ``readers_disagree``, ``unpinned``, ``reader_refused``,
  ``read_locally``: 0021 ISSUE_CODES.
* ledger kinds: scan, upgrade, card, invoice, map, embed, chat, memory, search, story
  (ai/usage.py 32); a cached scan page is a ``cached`` row costing 0 (gemini_ocr.py 435).
* answers: ``status`` answered, not_found or unchecked; ``label``; ``route`` fixed_query or
  search; ``citations`` a list (answering/service.py 174-252).

The functions are plpgsql (STABLE, one RETURN QUERY each, no dynamic SQL), not sql as
0020's: a sql function cannot raise the range refusal. As 0020's otherwise: SECURITY
DEFINER with an empty search path and schema-qualified names, revoked from PUBLIC and
runnable by ``kaagaz_operator`` ONLY, and answering only an enabled operator whose two-step
secret is confirmed (no row otherwise). Each organisation's rows are read through its own
indexes (``CROSS JOIN LATERAL`` per customer, as 0020's health).

The index ``messages_answers`` (customer_id, created_at) of assistant messages lets F2 and
F3 count an organisation's answers in a window without reading its whole chat history.
It is built CONCURRENTLY, as 0020's audit_log_feed and for the same reasons: the FIRST
step, in an autocommit block (0023 is then the recorded revision); a build that fails
leaves an INVALID index and the revision at 0023, and the next run drops it first.

The downgrade drops the functions, then the index (a plain DROP INDEX, in the
transaction, held at most the lock wait); nothing else changes.

Revision ID: 0024
Revises: 0023 (model choice and AI spend)
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

OPERATOR_ROLE = "kaagaz_operator"
INDEX = "messages_answers"
LOCK_TIMEOUT = "10s"  # as env.py's
K = 10  # PROVISIONAL: an outcome value needs at least this many of its total
P95_MIN = 20  # PROVISIONAL: a p95 needs at least this many
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_DOCUMENT_KINDS = "'scan', 'upgrade', 'card', 'invoice', 'map', 'embed'"
_QUESTION_KINDS = "'chat', 'memory', 'search'"
_PLATFORM_FAILURE = (
    "(r.error_code IN ('internal_error', 'retries_exhausted') OR r.error_code ~ '_unavailable$')"
)
_COUNTED_RUN = "r.status NOT IN ('running', 'superseded')"  # a run that ended on its own

# The start of each body: columns win over the RETURNS TABLE names (every name is
# qualified anyway); no row for anyone but an enabled, confirmed operator.
_BEGIN = f"""
    #variable_conflict use_column
    DECLARE v_lo timestamptz; v_hi timestamptz;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED}) THEN
            RETURN;
        END IF;
"""
_ZONE = """
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names z WHERE z.name = p_zone) THEN
            RAISE EXCEPTION 'unknown time zone' USING ERRCODE = 'check_violation';
        END IF;
"""
# [p_from 00:00, p_to + 1 00:00) in the zone, at most 366 days.
_RANGE = f"""
        IF p_from IS NULL OR p_to IS NULL OR p_from > p_to OR p_to - p_from >= 366 THEN
            RAISE EXCEPTION 'a range runs forwards over at most 366 days' USING ERRCODE = 'check_violation';
        END IF;
        {_ZONE}
        v_lo := p_from::timestamp AT TIME ZONE p_zone;
        v_hi := (p_to + 1)::timestamp AT TIME ZONE p_zone;
"""
_HEAD = "LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$"


def _sums(alias: str, columns: str) -> str:
    """``coalesce(sum(alias.c), 0)::bigint AS c`` for each column: a count summed (to the
    platform row, or over the day's parts) stays a whole number, 0 when there is none."""
    return ",\n               ".join(
        f"coalesce(sum({alias}.{column}), 0)::bigint AS {column}" for column in columns.split()
    )


def _unless_few(total: str, columns: str, minimum: int = K) -> str:
    """Each column, or NULL when its ``total`` is under ``minimum``."""
    return ",\n               ".join(
        f"CASE WHEN t.{total} >= {minimum} THEN t.{column} END" for column in columns.split()
    )


def _sums_of_enough(total: str, columns: str) -> str:
    """F3's ``total`` and its outcome ``columns`` summed (as ``_sums``), the platform row
    (GROUPING 1) over the organisations whose own ``total`` reaches K only: its outcome is
    then a sum of values their rows show, never one of an organisation below K, and its
    total the base of exactly those. An organisation's own total stays its true volume
    (its outcomes, 0 below K here, are NULLed by ``_unless_few``)."""
    enough = f"FILTER (WHERE o.{total} >= {K})"
    return ",\n               ".join(
        [
            f"(CASE WHEN GROUPING(o.customer_id) = 1 THEN coalesce(sum(o.{total}) {enough}, 0)"
            f" ELSE coalesce(sum(o.{total}), 0) END)::bigint AS {total}",
            *(f"coalesce(sum(o.{column}) {enough}, 0)::bigint AS {column}" for column in columns.split()),
        ]
    )


_F3_VOLUMES = (
    "runs runs_failed runs_failed_platform runs_degraded documents_run pages_cells pages_text_layer"
    " pages_ocr pages_blank pages_ai pages_local pages_cached pages_unreadable"
)
# F3's outcome values by the total each is a share of (K is of that total), in column order.
_F3_OUTCOMES = {
    "answers": "answered not_found unchecked answers_verified answers_fixed_query citations",
    "feedback": "feedback_up",
    "values_total": "values_needs_review",
    "corrections": "corrections_second corrections_typed",
}

FUNCTIONS = {
    # F1: one row per organisation and day that has something. Four reads of the
    # organisation's own indexes (audit_log_listing, processing_runs_started,
    # model_usage_period), each grouped by day, then added up by day. Runs: those that
    # ended on their own (not running, not superseded), as F3's. A cost NULL in the ledger
    # is left out of the sums and marks the day's cost part unknown.
    "kaagaz_op_daily(uuid, date, date, text)": f"""
    CREATE FUNCTION public.kaagaz_op_daily(p_operator uuid, p_from date, p_to date, p_zone text)
    RETURNS TABLE (customer_id uuid, day date, uploads bigint, sign_ins bigint, questions bigint,
                   corrections bigint, runs bigint, runs_failed bigint, pages bigint, scan_pages bigint,
                   cached_pages bigint, cost_usd numeric, cost_scan_usd numeric, cost_documents_usd numeric,
                   cost_questions_usd numeric, cost_estimated boolean, cost_part_unknown boolean)
    {_HEAD}
    {_BEGIN}
        {_RANGE}
        RETURN QUERY
        SELECT c.id, x.day,
               {_sums("x", "uploads sign_ins questions corrections runs runs_failed pages scan_pages cached_pages")},
               sum(x.cost), sum(x.cost_scan), sum(x.cost_documents), sum(x.cost_questions),
               coalesce(bool_or(x.estimated), false), coalesce(bool_or(x.part_unknown), false)
        FROM public.customers c
        CROSS JOIN LATERAL (
            SELECT (a.created_at AT TIME ZONE p_zone)::date AS day,
                   count(*) FILTER (WHERE a.action IN ('document.upload', 'document.version')) AS uploads,
                   count(*) FILTER (WHERE a.action IN ('auth.login', 'auth.admin_login')) AS sign_ins,
                   count(*) FILTER (WHERE a.action = 'chat.ask') AS questions,
                   count(*) FILTER (WHERE a.action = 'review.resolve') AS corrections,
                   NULL::bigint AS runs, NULL::bigint AS runs_failed, NULL::bigint AS pages,
                   NULL::bigint AS scan_pages, NULL::bigint AS cached_pages,
                   NULL::numeric AS cost, NULL::numeric AS cost_scan, NULL::numeric AS cost_documents,
                   NULL::numeric AS cost_questions, NULL::boolean AS estimated, NULL::boolean AS part_unknown
            FROM public.audit_log a
            WHERE a.customer_id = c.id AND a.created_at >= v_lo AND a.created_at < v_hi
              AND a.action IN ('document.upload', 'document.version', 'auth.login', 'auth.admin_login',
                               'chat.ask', 'review.resolve')
            GROUP BY 1
            UNION ALL
            SELECT (r.started_at AT TIME ZONE p_zone)::date, NULL, NULL, NULL, NULL,
                   count(*), count(*) FILTER (WHERE r.status = 'failed'),
                   sum(r.pages_cells + r.pages_text_layer + r.pages_ocr + r.pages_blank),
                   NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
            FROM public.processing_runs r
            WHERE r.customer_id = c.id AND r.started_at >= v_lo AND r.started_at < v_hi AND {_COUNTED_RUN}
            GROUP BY 1
            UNION ALL
            SELECT (u.created_at AT TIME ZONE p_zone)::date, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                   sum(u.calls) FILTER (WHERE u.kind = 'scan' AND NOT u.cached),
                   sum(u.calls) FILTER (WHERE u.kind = 'scan' AND u.cached),
                   sum(u.cost_usd), sum(u.cost_usd) FILTER (WHERE u.kind = 'scan'),
                   sum(u.cost_usd) FILTER (WHERE u.kind IN ({_DOCUMENT_KINDS})),
                   sum(u.cost_usd) FILTER (WHERE u.kind IN ({_QUESTION_KINDS})),
                   bool_or(u.cost_estimated), bool_or(u.cost_usd IS NULL)
            FROM public.model_usage u
            WHERE u.customer_id = c.id AND u.created_at >= v_lo AND u.created_at < v_hi
            GROUP BY 1
        ) x
        GROUP BY c.id, x.day
        ORDER BY c.id, x.day;
    END
    $$
    """,
    # F2: one row per organisation, oldest first. People active (anything in its audit log,
    # a count of distinct actors, never who) in the 7 and 30 days to p_today and the 30
    # before; answers in the 30 days (messages_answers); the open review exactly as the
    # admin's (db/admin.py _COUNTS: needs-review values of current, ready versions out of
    # Trash), and its oldest value; the calendar month's spend to the end of p_today; the
    # last day (in p_zone) of an upload or new version, a question or a run that was not
    # superseded, looked for in the 366 days to p_today only (NULL if none).
    # ponytail: O(audit rows of 60 days + open review) per organisation on every call,
    # plus two newest-row probes (audit_log_listing, processing_runs_started).
    "kaagaz_op_org_health(uuid, date, text)": f"""
    CREATE FUNCTION public.kaagaz_op_org_health(p_operator uuid, p_today date, p_zone text)
    RETURNS TABLE (customer_id uuid, active_users_7d bigint, active_users_30d bigint,
                   active_users_prev_30d bigint, answers_30d bigint, not_found_30d bigint, open_review bigint,
                   oldest_open_review_at timestamptz, ai_budget_usd numeric, month_cost_usd numeric,
                   month_cost_estimated boolean, month_cost_part_unknown boolean, last_active_day date)
    {_HEAD}
    {_BEGIN}
        IF p_today IS NULL THEN
            RAISE EXCEPTION 'a day is needed' USING ERRCODE = 'check_violation';
        END IF;
        {_ZONE}
        v_lo := (p_today - 29)::timestamp AT TIME ZONE p_zone;
        v_hi := (p_today + 1)::timestamp AT TIME ZONE p_zone;
        RETURN QUERY
        SELECT c.id, p.users_7d, p.users_30d, p.users_prev, m.answers,
               CASE WHEN m.answers >= {K} THEN m.not_found END,
               v.items, v.oldest, c.ai_budget_usd::numeric, s.cost, coalesce(s.estimated, false),
               coalesce(s.part_unknown, false), (l.newest AT TIME ZONE p_zone)::date
        FROM public.customers c
        CROSS JOIN LATERAL (
            SELECT count(DISTINCT a.actor_user_id)
                       FILTER (WHERE a.created_at >= (p_today - 6)::timestamp AT TIME ZONE p_zone) AS users_7d,
                   count(DISTINCT a.actor_user_id) FILTER (WHERE a.created_at >= v_lo) AS users_30d,
                   count(DISTINCT a.actor_user_id) FILTER (WHERE a.created_at < v_lo) AS users_prev
            FROM public.audit_log a
            WHERE a.customer_id = c.id AND a.actor_user_id IS NOT NULL
              AND a.created_at >= (p_today - 59)::timestamp AT TIME ZONE p_zone AND a.created_at < v_hi
        ) p
        CROSS JOIN LATERAL (
            SELECT count(*) AS answers, count(*) FILTER (WHERE m.answer ->> 'status' = 'not_found') AS not_found
            FROM public.messages m
            WHERE m.customer_id = c.id AND m.role = 'assistant' AND m.created_at >= v_lo AND m.created_at < v_hi
        ) m
        CROSS JOIN LATERAL (
            SELECT count(*) AS items, min(ev.created_at) AS oldest
            FROM public.extracted_values ev
            JOIN public.documents d ON d.customer_id = ev.customer_id AND d.id = ev.document_id
            WHERE ev.customer_id = c.id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
              AND ev.label = 'needs_review'
        ) v
        CROSS JOIN LATERAL (
            SELECT sum(u.cost_usd) AS cost, bool_or(u.cost_estimated) AS estimated,
                   bool_or(u.cost_usd IS NULL) AS part_unknown
            FROM public.model_usage u
            WHERE u.customer_id = c.id AND u.created_at < v_hi
              AND u.created_at >= date_trunc('month', p_today::timestamp) AT TIME ZONE p_zone
        ) s
        CROSS JOIN LATERAL (
            SELECT max(x.newest) AS newest
            FROM (
                SELECT max(a.created_at) AS newest
                FROM public.audit_log a
                WHERE a.customer_id = c.id AND a.action IN ('document.upload', 'document.version', 'chat.ask')
                  AND a.created_at >= (p_today - 365)::timestamp AT TIME ZONE p_zone AND a.created_at < v_hi
                UNION ALL
                SELECT max(r.started_at)
                FROM public.processing_runs r
                WHERE r.customer_id = c.id AND r.status <> 'superseded'
                  AND r.started_at >= (p_today - 365)::timestamp AT TIME ZONE p_zone AND r.started_at < v_hi
            ) x
        ) l
        ORDER BY c.created_at, c.id;
    END
    $$
    """,
    # F3: one row per organisation and the platform row (GROUPING SETS: customer_id NULL).
    # Runs that ended on their own, started in the window; failed by the platform (the
    # codes above) and degraded (ready with an error code: no card). Upload to ready: the
    # document's first ready run (no ready run started before it), finished minus the
    # document's upload. Answers: assistant messages in the window, their status, label,
    # route and citation count only; feedback: the rating of those answers only (never its
    # reason); corrections: review.resolve, split by its choice. Values: as each run
    # labelled them when it read (a later fix does not change them). Outcome values NULL
    # below K of their total, p95 below 20; the platform row's outcome values and their
    # totals over the organisations at K or more only (no leak by subtraction, see the
    # docstring and _sums_of_enough). Documents deleted for good are not counted
    # (their runs, messages and values go with them; 0005, 0021).
    # ponytail: O(rows of the window) per organisation, plus one probe a ready run.
    "kaagaz_op_quality(uuid, date, date, text)": f"""
    CREATE FUNCTION public.kaagaz_op_quality(p_operator uuid, p_from date, p_to date, p_zone text)
    RETURNS TABLE (customer_id uuid, runs bigint, runs_failed bigint, runs_failed_platform bigint,
                   runs_degraded bigint, documents_run bigint, pages_cells bigint, pages_text_layer bigint,
                   pages_ocr bigint, pages_blank bigint, pages_ai bigint, pages_local bigint, pages_cached bigint,
                   pages_unreadable bigint, ready_documents bigint, ready_p50_seconds double precision,
                   ready_p95_seconds double precision, answers bigint, answered bigint, not_found bigint,
                   unchecked bigint, answers_verified bigint, answers_fixed_query bigint, citations bigint,
                   feedback bigint, feedback_up bigint, values_total bigint, values_needs_review bigint,
                   corrections bigint, corrections_second bigint, corrections_typed bigint)
    {_HEAD}
    {_BEGIN}
        {_RANGE}
        RETURN QUERY
        WITH per_org AS (
            SELECT c.id AS customer_id, r.*, m.*, f.*, k.*
            FROM public.customers c
            CROSS JOIN LATERAL (
                SELECT count(*) AS runs, count(*) FILTER (WHERE r.status = 'failed') AS runs_failed,
                       count(*) FILTER (WHERE r.status = 'failed' AND {_PLATFORM_FAILURE}) AS runs_failed_platform,
                       count(*) FILTER (WHERE r.status = 'ready' AND r.error_code IS NOT NULL) AS runs_degraded,
                       count(DISTINCT r.document_id) AS documents_run,
                       sum(r.pages_cells) AS pages_cells, sum(r.pages_text_layer) AS pages_text_layer,
                       sum(r.pages_ocr) AS pages_ocr, sum(r.pages_blank) AS pages_blank,
                       sum(r.pages_gemini) AS pages_ai, sum(r.pages_local) AS pages_local,
                       sum(r.pages_cached) AS pages_cached, sum(r.pages_unreadable) AS pages_unreadable,
                       sum(r.values_verified + r.values_needs_review) AS values_total,
                       sum(r.values_needs_review) AS values_needs_review
                FROM public.processing_runs r
                WHERE r.customer_id = c.id AND r.started_at >= v_lo AND r.started_at < v_hi AND {_COUNTED_RUN}
            ) r
            CROSS JOIN LATERAL (
                SELECT count(*) AS answers,
                       count(*) FILTER (WHERE m.answer ->> 'status' = 'answered') AS answered,
                       count(*) FILTER (WHERE m.answer ->> 'status' = 'not_found') AS not_found,
                       count(*) FILTER (WHERE m.answer ->> 'status' = 'unchecked') AS unchecked,
                       count(*) FILTER (WHERE m.answer ->> 'label' = 'verified') AS answers_verified,
                       count(*) FILTER (WHERE m.answer ->> 'route' = 'fixed_query') AS answers_fixed_query,
                       sum(CASE WHEN jsonb_typeof(m.answer -> 'citations') = 'array'
                                THEN jsonb_array_length(m.answer -> 'citations') END) AS citations
                FROM public.messages m
                WHERE m.customer_id = c.id AND m.role = 'assistant' AND m.created_at >= v_lo AND m.created_at < v_hi
            ) m
            CROSS JOIN LATERAL (
                SELECT count(*) AS feedback, count(*) FILTER (WHERE fb.rating = 'up') AS feedback_up
                FROM public.messages fm
                JOIN public.feedback fb ON fb.customer_id = fm.customer_id AND fb.message_id = fm.id
                WHERE fm.customer_id = c.id AND fm.role = 'assistant' AND fm.created_at >= v_lo
                  AND fm.created_at < v_hi
            ) f
            CROSS JOIN LATERAL (
                SELECT count(*) AS corrections,
                       count(*) FILTER (WHERE a.details ->> 'choice' = 'second') AS corrections_second,
                       count(*) FILTER (WHERE a.details ->> 'choice' = 'typed') AS corrections_typed
                FROM public.audit_log a
                WHERE a.customer_id = c.id AND a.action = 'review.resolve' AND a.created_at >= v_lo
                  AND a.created_at < v_hi
            ) k
        ),
        ready AS (
            SELECT c.id AS customer_id, x.seconds
            FROM public.customers c
            CROSS JOIN LATERAL (
                SELECT extract(epoch FROM r.finished_at - d.created_at)::double precision AS seconds
                FROM public.processing_runs r
                JOIN public.documents d ON d.customer_id = r.customer_id AND d.id = r.document_id
                WHERE r.customer_id = c.id AND r.status = 'ready' AND r.started_at >= v_lo
                  AND r.started_at < v_hi
                  AND NOT EXISTS (
                      SELECT 1 FROM public.processing_runs e
                      WHERE e.customer_id = r.customer_id AND e.document_id = r.document_id
                        AND e.status = 'ready' AND (e.started_at, e.id) < (r.started_at, r.id)
                  )
            ) x
        ),
        ready_by AS (
            SELECT y.customer_id, GROUPING(y.customer_id) AS platform, count(*) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY y.seconds) AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY y.seconds) AS p95
            FROM ready y
            GROUP BY GROUPING SETS ((y.customer_id), ())
        ),
        t AS (
            SELECT o.customer_id, GROUPING(o.customer_id) AS platform,
                   {_sums("o", _F3_VOLUMES)},
                   {", ".join(_sums_of_enough(total, columns) for total, columns in _F3_OUTCOMES.items())}
            FROM per_org o
            GROUP BY GROUPING SETS ((o.customer_id), ())
        )
        SELECT t.customer_id, t.runs, t.runs_failed, t.runs_failed_platform, t.runs_degraded, t.documents_run,
               t.pages_cells, t.pages_text_layer, t.pages_ocr, t.pages_blank, t.pages_ai, t.pages_local,
               t.pages_cached, t.pages_unreadable, coalesce(b.n, 0),
               CASE WHEN b.n >= {K} THEN b.p50 END, CASE WHEN b.n >= {P95_MIN} THEN b.p95 END,
               {", ".join(f"t.{total}, {_unless_few(total, columns)}" for total, columns in _F3_OUTCOMES.items())}
        FROM t
        LEFT JOIN ready_by b ON b.platform = t.platform AND b.customer_id IS NOT DISTINCT FROM t.customer_id
        ORDER BY t.platform DESC, t.customer_id;
    END
    $$
    """,
    # F4: one row per model, no organisation. Runs started in the window that ended on
    # their own, by the model they read with (config read_model, else rapidocr when they
    # read a scan, else left out); seconds a page from the run's reading time over its
    # model, local and cached pages, a number only. Spend by the ledger's model (chat and
    # embedding models get spend-only rows); scan_calls are every scan call, cached ones
    # too (cached_scan_calls their part), scan_cost_usd the scan calls not cached. Page signals: the scanned pages of documents
    # uploaded in the window, by who read them, a page handed to the local reader counted
    # under its document's model (above). Never the self-reported unsure words or read
    # quality. p50 NULL below K measured runs, p95 below 20; page signals NULL below K
    # documents behind them. Keyless: no ledger, one rapidocr row.
    # ponytail: O(runs + ledger rows + scanned pages of the window) on every call.
    "kaagaz_op_model_board(uuid, date, date, text)": f"""
    CREATE FUNCTION public.kaagaz_op_model_board(p_operator uuid, p_from date, p_to date, p_zone text)
    RETURNS TABLE (model text, documents bigint, runs bigint, runs_failed bigint, runs_degraded bigint,
                   pages_ai bigint, pages_local bigint, pages_cached bigint, seconds_per_page_p50 double precision,
                   seconds_per_page_p95 double precision, calls bigint, scan_calls bigint,
                   cached_scan_calls bigint, input_tokens numeric, output_tokens numeric, cost_usd numeric,
                   scan_cost_usd numeric, cost_estimated boolean, cost_part_unknown boolean, ocr_pages bigint,
                   pages_disagree bigint, pages_refused bigint, pages_fell_back bigint, pages_unpinned bigint)
    {_HEAD}
    {_BEGIN}
        {_RANGE}
        RETURN QUERY
        WITH run AS (
            SELECT x.*
            FROM public.customers c
            CROSS JOIN LATERAL (
                SELECT coalesce(r.config ->> 'read_model', CASE WHEN r.pages_ocr > 0 THEN 'rapidocr' END) AS model,
                       r.document_id, r.status, r.error_code, r.pages_gemini, r.pages_local, r.pages_cached,
                       CASE WHEN jsonb_typeof(r.config -> 'seconds_reading') = 'number'
                            THEN (r.config ->> 'seconds_reading')::double precision
                                 / nullif(r.pages_gemini + r.pages_local + r.pages_cached, 0)
                       END AS per_page
                FROM public.processing_runs r
                WHERE r.customer_id = c.id AND r.started_at >= v_lo AND r.started_at < v_hi AND {_COUNTED_RUN}
            ) x
            WHERE x.model IS NOT NULL
        ),
        by_run AS (
            SELECT r.model, count(DISTINCT r.document_id) AS documents, count(*) AS runs,
                   count(*) FILTER (WHERE r.status = 'failed') AS runs_failed,
                   count(*) FILTER (WHERE r.status = 'ready' AND r.error_code IS NOT NULL) AS runs_degraded,
                   sum(r.pages_gemini) AS pages_ai, sum(r.pages_local) AS pages_local,
                   sum(r.pages_cached) AS pages_cached, count(r.per_page) AS measured,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY r.per_page) AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY r.per_page) AS p95
            FROM run r
            GROUP BY r.model
        ),
        by_spend AS (
            SELECT x.model, sum(x.calls) AS calls,
                   sum(x.calls) FILTER (WHERE x.kind = 'scan') AS scan_calls,
                   sum(x.calls) FILTER (WHERE x.kind = 'scan' AND x.cached) AS cached_scan_calls,
                   sum(x.input_tokens) AS input_tokens, sum(x.output_tokens) AS output_tokens,
                   sum(x.cost_usd) AS cost,
                   sum(x.cost_usd) FILTER (WHERE x.kind = 'scan' AND NOT x.cached) AS scan_cost,
                   bool_or(x.cost_estimated) AS estimated, bool_or(x.cost_usd IS NULL) AS part_unknown
            FROM public.customers c
            CROSS JOIN LATERAL (
                SELECT u.model, u.kind, u.cached, u.calls, u.input_tokens, u.output_tokens, u.cost_usd,
                       u.cost_estimated
                FROM public.model_usage u
                WHERE u.customer_id = c.id AND u.created_at >= v_lo AND u.created_at < v_hi
            ) x
            GROUP BY x.model
        ),
        by_page AS (
            SELECT x.model, count(DISTINCT x.document_id) AS documents, count(*) AS ocr_pages,
                   count(*) FILTER (WHERE 'readers_disagree' = ANY (x.read_issues)) AS disagree,
                   count(*) FILTER (WHERE 'reader_refused' = ANY (x.read_issues)) AS refused,
                   count(*) FILTER (WHERE 'read_locally' = ANY (x.read_issues)) AS fell_back,
                   count(*) FILTER (WHERE 'unpinned' = ANY (x.read_issues)) AS unpinned
            FROM public.customers c
            CROSS JOIN LATERAL (
                SELECT CASE WHEN p.read_issues && ARRAY['reader_refused', 'read_locally']::text[]
                            THEN coalesce(n.read_model, p.read_by) ELSE p.read_by END AS model,
                       p.document_id, p.read_issues
                FROM public.documents d
                LEFT JOIN LATERAL (
                    SELECT e.config ->> 'read_model' AS read_model
                    FROM public.processing_runs e
                    WHERE e.customer_id = d.customer_id AND e.document_id = d.id AND e.status = 'ready'
                    ORDER BY e.started_at DESC
                    LIMIT 1
                ) n ON true
                JOIN public.pages p ON p.customer_id = d.customer_id AND p.document_id = d.id
                WHERE d.customer_id = c.id AND d.created_at >= v_lo AND d.created_at < v_hi AND p.route = 'ocr'
            ) x
            WHERE x.model IS NOT NULL
            GROUP BY x.model
        ),
        models AS (
            SELECT r.model FROM by_run r
            UNION SELECT s.model FROM by_spend s
            UNION SELECT g.model FROM by_page g
        )
        SELECT m.model, coalesce(r.documents, 0), coalesce(r.runs, 0), coalesce(r.runs_failed, 0),
               coalesce(r.runs_degraded, 0), coalesce(r.pages_ai, 0), coalesce(r.pages_local, 0),
               coalesce(r.pages_cached, 0), CASE WHEN r.measured >= {K} THEN r.p50 END,
               CASE WHEN r.measured >= {P95_MIN} THEN r.p95 END,
               coalesce(s.calls, 0)::bigint, coalesce(s.scan_calls, 0)::bigint,
               coalesce(s.cached_scan_calls, 0)::bigint, coalesce(s.input_tokens, 0), coalesce(s.output_tokens, 0),
               s.cost, s.scan_cost, coalesce(s.estimated, false), coalesce(s.part_unknown, false),
               coalesce(g.ocr_pages, 0), CASE WHEN g.documents >= {K} THEN g.disagree END,
               CASE WHEN g.documents >= {K} THEN g.refused END, CASE WHEN g.documents >= {K} THEN g.fell_back END,
               CASE WHEN g.documents >= {K} THEN g.unpinned END
        FROM models m
        LEFT JOIN by_run r ON r.model = m.model
        LEFT JOIN by_spend s ON s.model = m.model
        LEFT JOIN by_page g ON g.model = m.model
        ORDER BY m.model;
    END
    $$
    """,
}

FUNCTION_COMMENTS = {
    "kaagaz_op_daily": "Operator insights: per organisation and day, uploads, sign-ins, questions,"
    " corrections, runs, pages and AI spend. Counts and sums only, never a person or a document.",
    "kaagaz_op_org_health": "Operator insights: per organisation, active people (a count), answers and"
    " not-found answers of the last 30 days, the open review, this month's spend against its budget"
    " and the last day it was active.",
    "kaagaz_op_quality": "Operator insights: reading, answers and review per organisation and for the"
    " platform. Outcome values are NULL below 10 of their total, a p95 below 20.",
    "kaagaz_op_model_board": "Operator insights: per model, runs, speed, page signals and spend, no"
    " organisation. Observational, not an A/B test; small samples are NULL.",
}
INDEX_COMMENT = (
    "Operator insights: one organisation's answers in a time window, read without its whole chat history."
)


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
            f"COMMENT ON FUNCTION public.{signature} IS {_literal(FUNCTION_COMMENTS[signature.split('(')[0]])}",
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
        )
    ],
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in reversed(FUNCTIONS)],
    f"DROP INDEX IF EXISTS public.{INDEX}",
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX}")  # a failed build's leftover
        op.execute(
            f"CREATE INDEX CONCURRENTLY {INDEX} ON public.messages (customer_id, created_at)"
            " WHERE role = 'assistant'"
        )
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
