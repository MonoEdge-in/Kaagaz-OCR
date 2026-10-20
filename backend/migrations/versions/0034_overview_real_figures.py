"""Real figures for the operator's Overview (Tushar, 16 Oct 2026: the "last 30 days" tiles
were "really unrealistic"; NEEDS VRUSHIT'S APPROVAL: one operator function replaced, under
the never-do 1 exception as 0024's and 0026's). No table, column, index, grant or policy
changes.

``kaagaz_op_daily`` keeps every column it had and gains, per organisation and day, from
the ledger (``model_usage``, read through ``model_usage_period``):

* ``pages_read``: pages a model read for the first time, the non-cached calls of kinds scan
  and upgrade (the reader makes one model call a page, gemini_ocr.py; a repair retry is a
  second call, so this can read slightly high). ``pages_reread``: Careful's and Ensemble's
  extra reads of those pages (careful, ensemble_scan), kept apart so the two never mix.
  ``pages_read_costed``: the first reads whose cost OpenRouter reported.
* ``cost_real_usd`` (every kind), ``cost_scan_real_usd`` (scan, upgrade, careful,
  ensemble_scan) and ``cost_answer_real_usd`` (chat, ensemble_chat, as 0033): only the cost
  OpenRouter reported. A NULL or an estimated (list price) cost is never added; its calls
  are counted in ``cost_unknown_calls``, ``scan_unknown_calls`` and ``answer_unknown_calls``.

Why the ledger and not ``processing_runs``: the old ``pages`` adds the routes of runs that
ended on their own (not superseded), so a document whose finished run was superseded (a
second delivery, a lost lease) counts no page. The Overview showed 1 page and 1 run for 27
files; which runs were left out there is not checked yet (no database here: inferred). The
ledger has a row for every billed page read whatever happened to the run, and it is what
the cost is summed from, so a cost per page divides like by like. ``pages`` and
the run counts are kept, unchanged, for the CSV and the Quality tab.

Deploy: the new columns come after the old ones, so the old API (which reads by name) keeps
working between ``alembic upgrade`` and the restart. The downgrade restores 0026's
function exactly.

Revision ID: 0034
Revises: 0033 (the operator's real cost)
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

# Copied from 0026 (a migration can't import another).
OPERATOR_ROLE = "kaagaz_operator"
SIGNATURE = "kaagaz_op_daily(uuid, date, date, text)"
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_READ_HEAD = "LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$"
_COUNTED_RUN = "r.status NOT IN ('running', 'superseded')"  # a run that ended on its own
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
_RANGE = f"""
        IF p_from IS NULL OR p_to IS NULL OR p_from > p_to OR p_to - p_from >= 366 THEN
            RAISE EXCEPTION 'a range runs forwards over at most 366 days' USING ERRCODE = 'check_violation';
        END IF;
        {_ZONE}
        v_lo := p_from::timestamp AT TIME ZONE p_zone;
        v_hi := (p_to + 1)::timestamp AT TIME ZONE p_zone;
"""
DOCUMENT_KINDS = "'scan', 'upgrade', 'card', 'invoice', 'map', 'embed', 'careful', 'ensemble_scan'"
QUESTION_KINDS = "'chat', 'memory', 'search', 'ensemble_chat'"
FIRST_READ_KINDS = "'scan', 'upgrade'"
REREAD_KINDS = "'careful', 'ensemble_scan'"
SCAN_KINDS = f"{FIRST_READ_KINDS}, {REREAD_KINDS}"
ANSWER_KINDS = "'chat', 'ensemble_chat'"
CHARGED = "u.cost_usd IS NOT NULL AND NOT u.cost_estimated"  # what OpenRouter reported
COMMENT = (
    "Operator insights: per organisation and day, uploads, sign-ins, questions,"
    " corrections, runs, pages and AI spend. Counts and sums only, never a person or a document."
)
NEW_COMMENT = (
    "Operator insights: per organisation and day, uploads, sign-ins, questions, corrections, runs,"
    " pages, the pages models read and read again from the ledger, and AI spend, with the cost"
    " OpenRouter reported apart from calls whose cost is not known. Counts and sums only, never a"
    " person or a document."
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sums(alias: str, columns: str) -> str:
    return ",\n               ".join(
        f"coalesce(sum({alias}.{column}), 0)::bigint AS {column}" for column in columns.split()
    )


def _daily(real: bool) -> str:
    """``kaagaz_op_daily``: 0026's exactly when not ``real``; with the real figures after
    0026's columns when ``real``."""
    returns = (
        ",\n                   pages_read bigint, pages_reread bigint, pages_read_costed bigint,"
        " cost_real_usd numeric,\n                   cost_scan_real_usd numeric, cost_answer_real_usd numeric,"
        " cost_unknown_calls bigint,\n                   scan_unknown_calls bigint, answer_unknown_calls bigint"
        if real
        else ""
    )
    outer = (
        f",\n               {_sums('x', 'pages_read pages_reread pages_read_costed')},"
        "\n               sum(x.cost_real), sum(x.cost_scan_real), sum(x.cost_answer_real),"
        f"\n               {_sums('x', 'cost_unknown_calls scan_unknown_calls answer_unknown_calls')}"
        if real
        else ""
    )
    audit = (
        ",\n                   NULL::bigint AS pages_read, NULL::bigint AS pages_reread,"
        " NULL::bigint AS pages_read_costed,\n                   NULL::numeric AS cost_real,"
        " NULL::numeric AS cost_scan_real, NULL::numeric AS cost_answer_real,"
        "\n                   NULL::bigint AS cost_unknown_calls, NULL::bigint AS scan_unknown_calls,"
        " NULL::bigint AS answer_unknown_calls"
        if real
        else ""
    )
    runs = ", NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL" if real else ""
    ledger = (
        f",\n                   sum(u.calls) FILTER (WHERE u.kind IN ({FIRST_READ_KINDS}) AND NOT u.cached),"
        f"\n                   sum(u.calls) FILTER (WHERE u.kind IN ({REREAD_KINDS}) AND NOT u.cached),"
        f"\n                   sum(u.calls) FILTER (WHERE u.kind IN ({FIRST_READ_KINDS}) AND NOT u.cached"
        f" AND {CHARGED}),"
        f"\n                   sum(u.cost_usd) FILTER (WHERE {CHARGED}),"
        f"\n                   sum(u.cost_usd) FILTER (WHERE u.kind IN ({SCAN_KINDS}) AND {CHARGED}),"
        f"\n                   sum(u.cost_usd) FILTER (WHERE u.kind IN ({ANSWER_KINDS}) AND {CHARGED}),"
        f"\n                   sum(u.calls) FILTER (WHERE NOT u.cached AND NOT ({CHARGED})),"
        f"\n                   sum(u.calls) FILTER (WHERE u.kind IN ({SCAN_KINDS}) AND NOT u.cached"
        f" AND NOT ({CHARGED})),"
        f"\n                   sum(u.calls) FILTER (WHERE u.kind IN ({ANSWER_KINDS}) AND NOT ({CHARGED}))"
        if real
        else ""
    )
    create = "CREATE FUNCTION" if real else "CREATE OR REPLACE FUNCTION"
    return f"""
    {create} public.kaagaz_op_daily(p_operator uuid, p_from date, p_to date, p_zone text)
    RETURNS TABLE (customer_id uuid, day date, uploads bigint, sign_ins bigint, questions bigint,
                   corrections bigint, runs bigint, runs_failed bigint, pages bigint, scan_pages bigint,
                   cached_pages bigint, cost_usd numeric, cost_scan_usd numeric, cost_documents_usd numeric,
                   cost_questions_usd numeric, cost_estimated boolean, cost_part_unknown boolean{returns})
    {_READ_HEAD}
    {_BEGIN}
        {_RANGE}
        RETURN QUERY
        SELECT c.id, x.day,
               {_sums("x", "uploads sign_ins questions corrections runs runs_failed pages scan_pages cached_pages")},
               sum(x.cost), sum(x.cost_scan), sum(x.cost_documents), sum(x.cost_questions),
               coalesce(bool_or(x.estimated), false), coalesce(bool_or(x.part_unknown), false){outer}
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
                   NULL::numeric AS cost_questions, NULL::boolean AS estimated, NULL::boolean AS part_unknown{audit}
            FROM public.audit_log a
            WHERE a.customer_id = c.id AND a.created_at >= v_lo AND a.created_at < v_hi
              AND a.action IN ('document.upload', 'document.version', 'auth.login', 'auth.admin_login',
                               'chat.ask', 'review.resolve')
            GROUP BY 1
            UNION ALL
            SELECT (r.started_at AT TIME ZONE p_zone)::date, NULL, NULL, NULL, NULL,
                   count(*), count(*) FILTER (WHERE r.status = 'failed'),
                   sum(r.pages_cells + r.pages_text_layer + r.pages_ocr + r.pages_blank),
                   NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL{runs}
            FROM public.processing_runs r
            WHERE r.customer_id = c.id AND r.started_at >= v_lo AND r.started_at < v_hi AND {_COUNTED_RUN}
            GROUP BY 1
            UNION ALL
            SELECT (u.created_at AT TIME ZONE p_zone)::date, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                   sum(u.calls) FILTER (WHERE u.kind = 'scan' AND NOT u.cached),
                   sum(u.calls) FILTER (WHERE u.kind = 'scan' AND u.cached),
                   sum(u.cost_usd), sum(u.cost_usd) FILTER (WHERE u.kind = 'scan'),
                   sum(u.cost_usd) FILTER (WHERE u.kind IN ({DOCUMENT_KINDS})),
                   sum(u.cost_usd) FILTER (WHERE u.kind IN ({QUESTION_KINDS})),
                   bool_or(u.cost_estimated), bool_or(u.cost_usd IS NULL){ledger}
            FROM public.model_usage u
            WHERE u.customer_id = c.id AND u.created_at >= v_lo AND u.created_at < v_hi
            GROUP BY 1
        ) x
        GROUP BY c.id, x.day
        ORDER BY c.id, x.day;
    END
    $$
    """


def _replace(real: bool) -> list[str]:
    return [
        f"DROP FUNCTION public.{SIGNATURE}",
        _daily(real),
        f"COMMENT ON FUNCTION public.{SIGNATURE} IS {_literal(NEW_COMMENT if real else COMMENT)}",
        f"REVOKE ALL ON FUNCTION public.{SIGNATURE} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{SIGNATURE} TO {OPERATOR_ROLE}",
    ]


def upgrade() -> None:
    for statement in _replace(True):
        op.execute(statement)


def downgrade() -> None:
    for statement in _replace(False):
        op.execute(statement)
