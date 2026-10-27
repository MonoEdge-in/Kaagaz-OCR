"""Each organisation's real numbers for the platform operator (#387, #377; NEEDS VRUSHIT'S
APPROVAL: one more read-only function under the never-do 1 exception, as 0024's readers).

``kaagaz_op_org_numbers``: per organisation, over [p_from 00:00, p_to + 1 day 00:00) in
``p_zone`` (at most 366 days, refused otherwise, as 0024's ``_RANGE``):

* ``documents``: files uploaded in the window, every version, those now in Trash too
  (``documents_in_trash``); a file deleted for good is forgotten, so it is not counted.
* ``pages_read`` and ``pages_by_route``: the pages of those files that were read (``pages.route``
  set), each page of each version once, by how it was read: ``ai`` (a scanned page read by an AI
  model, ``read_by`` a model id, which has a slash), ``local`` (read by the local reader,
  ``rapidocr``), ``ocr`` (a scanned page whose reader was not recorded), else the route itself
  (``text_layer``, ``cells``, ``blank``, and any route added later, counted under its own
  name). Not the runs' counters: a run that was superseded counted no page there (0034).
* ``failed_documents``: those files that ended ``failed``, ``rejected`` or ``needs_reader``, by
  their failure code (the status when there is none).
* ``active_users``: people who did anything the activity log records in the window (a count of
  distinct actors, never who), as 0024's F2.
* ``questions``: replies to questions in the window, a clarifying question back not counted
  (as the admin's ``questions_total``, db/admin.py).
* AI use from the ledger (``model_usage``): ``ai_calls``, ``ai_input_tokens`` and
  ``ai_output_tokens`` of the calls not served from the cache; ``ai_cost_usd`` the cost
  OpenRouter reported (as 0034's ``cost_real_usd``), and ``ai_cost_unknown_calls`` the calls
  whose cost is not known or only estimated (as 0034's ``cost_unknown_calls``).

Refused requests (429) are not here: the API writes them to its log only, no table holds them.

As 0024's readers: plpgsql STABLE, one RETURN QUERY, no dynamic SQL, SECURITY DEFINER with an
empty search path and schema-qualified names, revoked from PUBLIC and runnable by
``kaagaz_operator`` only, answering only an enabled operator whose two-step secret is confirmed
(no row otherwise). Reads are not audited (as 0024's). Counts and codes only: never a person,
document, name or text. Each organisation's rows are read through its own indexes
(``documents_listing``, the pages' primary key, ``audit_log_listing``, ``messages_answers``,
``model_usage_period``). ponytail: O(rows of the window) per organisation on every call.

No table, column, index, grant or policy changes. The downgrade drops the function.

Revision ID: 0055
Revises: 0054 (a map value's label text)
"""

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

OPERATOR_ROLE = "kaagaz_operator"
NAME = "public.kaagaz_op_org_numbers"
SIGNATURE = f"{NAME}(uuid, date, date, text)"
CHARGED = "u.cost_usd IS NOT NULL AND NOT u.cost_estimated"  # as 0034's: what OpenRouter reported
IN_WINDOW = "{0} >= v_lo AND {0} < v_hi"
COMMENT = (
    "Operator: per organisation, files uploaded, the pages of them read by how they were read, the"
    " failed ones by reason, active people (a count), questions and AI use. Counts and codes only."
)

ORG_NUMBERS = f"""
CREATE FUNCTION {NAME}(p_operator uuid, p_from date, p_to date, p_zone text)
RETURNS TABLE (customer_id uuid, documents bigint, documents_in_trash bigint, pages_read bigint,
               pages_by_route jsonb, failed_documents jsonb, active_users bigint, questions bigint,
               ai_calls bigint, ai_input_tokens bigint, ai_output_tokens bigint, ai_cost_usd numeric,
               ai_cost_unknown_calls bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    #variable_conflict use_column
    DECLARE v_lo timestamptz; v_hi timestamptz;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.operators o
                       WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL) THEN
            RETURN;
        END IF;
        IF p_from IS NULL OR p_to IS NULL OR p_from > p_to OR p_to - p_from >= 366 THEN
            RAISE EXCEPTION 'a range runs forwards over at most 366 days' USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names z WHERE z.name = p_zone) THEN
            RAISE EXCEPTION 'unknown time zone' USING ERRCODE = 'check_violation';
        END IF;
        v_lo := p_from::timestamp AT TIME ZONE p_zone;
        v_hi := (p_to + 1)::timestamp AT TIME ZONE p_zone;
        RETURN QUERY
        SELECT c.id, d.documents, d.in_trash, coalesce(g.pages, 0)::bigint, coalesce(g.by_route, '{{}}'::jsonb),
               coalesce(f.by_reason, '{{}}'::jsonb), a.people, q.questions, coalesce(s.calls, 0)::bigint,
               coalesce(s.input_tokens, 0)::bigint, coalesce(s.output_tokens, 0)::bigint, s.cost,
               coalesce(s.unknown, 0)::bigint
        FROM public.customers c
        CROSS JOIN LATERAL (
            SELECT count(*) AS documents, count(*) FILTER (WHERE x.deleted_at IS NOT NULL) AS in_trash
            FROM public.documents x
            WHERE x.customer_id = c.id AND {IN_WINDOW.format("x.created_at")}
        ) d
        CROSS JOIN LATERAL (
            SELECT sum(y.n) AS pages, jsonb_object_agg(y.way, y.n) AS by_route
            FROM (
                SELECT CASE WHEN p.route = 'ocr' AND p.read_by ~ '/' THEN 'ai'
                            WHEN p.route = 'ocr' AND p.read_by = 'rapidocr' THEN 'local'
                            ELSE p.route END AS way,
                       count(*) AS n
                FROM public.documents x
                JOIN public.pages p ON p.customer_id = x.customer_id AND p.document_id = x.id
                WHERE x.customer_id = c.id AND {IN_WINDOW.format("x.created_at")} AND p.route IS NOT NULL
                GROUP BY 1
            ) y
        ) g
        CROSS JOIN LATERAL (
            SELECT jsonb_object_agg(z.reason, z.n) AS by_reason
            FROM (
                SELECT coalesce(x.failure_code, x.status) AS reason, count(*) AS n
                FROM public.documents x
                WHERE x.customer_id = c.id AND {IN_WINDOW.format("x.created_at")}
                  AND x.status IN ('failed', 'rejected', 'needs_reader')
                GROUP BY 1
            ) z
        ) f
        CROSS JOIN LATERAL (
            SELECT count(DISTINCT e.actor_user_id) AS people
            FROM public.audit_log e
            WHERE e.customer_id = c.id AND e.actor_user_id IS NOT NULL AND {IN_WINDOW.format("e.created_at")}
        ) a
        CROSS JOIN LATERAL (
            SELECT count(*) AS questions
            FROM public.messages m
            WHERE m.customer_id = c.id AND m.role = 'assistant' AND m.answer ->> 'status' IS DISTINCT FROM 'clarify'
              AND {IN_WINDOW.format("m.created_at")}
        ) q
        CROSS JOIN LATERAL (
            SELECT sum(u.calls) FILTER (WHERE NOT u.cached) AS calls,
                   sum(u.input_tokens) FILTER (WHERE NOT u.cached) AS input_tokens,
                   sum(u.output_tokens) FILTER (WHERE NOT u.cached) AS output_tokens,
                   sum(u.cost_usd) FILTER (WHERE {CHARGED}) AS cost,
                   sum(u.calls) FILTER (WHERE NOT u.cached AND NOT ({CHARGED})) AS unknown
            FROM public.model_usage u
            WHERE u.customer_id = c.id AND {IN_WINDOW.format("u.created_at")}
        ) s
        ORDER BY c.created_at, c.id;
    END
$$
"""


def _literal(comment: str) -> str:
    return "'" + comment.replace("'", "''") + "'"


UPGRADE = [
    ORG_NUMBERS,
    f"COMMENT ON FUNCTION {SIGNATURE} IS {_literal(COMMENT)}",
    f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {OPERATOR_ROLE}",
]

DOWNGRADE = [f"DROP FUNCTION IF EXISTS {SIGNATURE}"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
