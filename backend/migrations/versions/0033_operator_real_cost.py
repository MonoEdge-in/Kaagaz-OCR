"""The real cost of scanning and answering per model, for the operator (Tushar, 16 Oct
2026: "I only want to show REAL pricing to the super admin, for scanning and answering
both"; NEEDS VRUSHIT'S APPROVAL: one operator function replaced, under the never-do 1
exception as 0023's and 0026's). No table, column, index, grant or policy changes.

``kaagaz_op_models`` keeps every column it had and gains, per model, from every
organisation's ledger of the last LEDGER_DAYS days (numbers only, never a name):

* scanning, kinds scan, upgrade, careful and ensemble_scan: ``scan_pages`` (the pages the
  model read), ``scan_pages_costed`` (those whose cost OpenRouter reported),
  ``scan_cost_usd`` (that reported cost, summed) and ``scan_cost_unknown_calls``.
* answering, kinds chat and ensemble_chat: ``answer_questions``, ``answer_questions_costed``,
  ``answer_cost_usd`` and ``answer_cost_unknown_calls``.

How it counts. Pages: the ledger has no page column, but the reader makes one model call a
page (gemini_ocr.py; 0024's F1 ``scan_pages`` counts them the same way), so a page is a
call that was not ``cached`` (a page from the page cache made no call and cost nothing).
A repair retry is a second call for the same page, so pages can read slightly high.
processing_runs is not joined: its page counts carry no model for Careful's and
Ensemble's reads. Questions: one usage scope a question (api/routers/chat.py), which
writes one ledger row per model and kind, so a row of kind chat or ensemble_chat is one
question that model worked on. Cost: only ``cost_usd`` as OpenRouter reported it. A row
with no cost, or one the insert estimated from the list price (``cost_estimated``), is
left out of the cost and of the costed count, and its calls are counted as unknown: never
a guess.

Index use: one grouped read, each organisation's rows through ``model_usage_period``
(customer_id, created_at), as 0024's functions (CROSS JOIN LATERAL per customer).

Deploy: the new columns come after the old ones, so the old API (which reads by name)
keeps working between ``alembic upgrade`` and the restart. The downgrade restores 0026's
function exactly.

Revision ID: 0033
Revises: 0032 (word review)
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

# Copied from 0026 (a migration can't import another).
OPERATOR_ROLE = "kaagaz_operator"
LEDGER_DAYS = 30  # kaagaz.db.models.LEDGER_DAYS
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
SIGNATURE = "kaagaz_op_models(uuid)"
SCAN_KINDS = "'scan', 'upgrade', 'careful', 'ensemble_scan'"
ANSWER_KINDS = "'chat', 'ensemble_chat'"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


NEW = f"""
    CREATE FUNCTION public.kaagaz_op_models(p_operator uuid)
    RETURNS TABLE (id text, name text, price_in_usd_per_m numeric, price_out_usd_per_m numeric,
                   providers text[], efforts text[], expires_on date, eligible boolean, seen_at timestamptz,
                   verified_prompt_version text, verified_at timestamptz,
                   tokens_per_page_in integer, tokens_per_page_out integer, enabled_at timestamptz,
                   ledger_tokens_in numeric, ledger_tokens_out numeric, chat_eligible boolean,
                   chat_verified_prompt text, chat_verified_at timestamptz,
                   scan_pages bigint, scan_pages_costed bigint, scan_cost_usd numeric,
                   scan_cost_unknown_calls bigint, answer_questions bigint, answer_questions_costed bigint,
                   answer_cost_usd numeric, answer_cost_unknown_calls bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH r AS (
            SELECT x.model, x.kind, x.calls, x.input_tokens, x.output_tokens, x.cost_usd,
                   x.kind IN ({SCAN_KINDS}) AS scan, x.cost_usd IS NOT NULL AND NOT x.cost_estimated AS charged
            FROM public.customers c
            CROSS JOIN LATERAL (
                SELECT u.model, u.kind, u.calls, u.input_tokens, u.output_tokens, u.cost_usd, u.cost_estimated
                FROM public.model_usage u
                WHERE u.customer_id = c.id AND u.created_at >= now() - make_interval(days => {LEDGER_DAYS})
                  AND NOT u.cached AND u.kind IN ({SCAN_KINDS}, {ANSWER_KINDS})
            ) x
        ),
        s AS (
            SELECT r.model,
                   sum(r.input_tokens) FILTER (WHERE r.kind = 'scan')
                       / sum(r.calls) FILTER (WHERE r.kind = 'scan') AS page_in,
                   sum(r.output_tokens) FILTER (WHERE r.kind = 'scan')
                       / sum(r.calls) FILTER (WHERE r.kind = 'scan') AS page_out,
                   sum(r.calls) FILTER (WHERE r.scan) AS scan_pages,
                   sum(r.calls) FILTER (WHERE r.scan AND r.charged) AS scan_pages_costed,
                   sum(r.cost_usd) FILTER (WHERE r.scan AND r.charged) AS scan_cost,
                   sum(r.calls) FILTER (WHERE r.scan AND NOT r.charged) AS scan_unknown,
                   count(*) FILTER (WHERE NOT r.scan) AS questions,
                   count(*) FILTER (WHERE NOT r.scan AND r.charged) AS questions_costed,
                   sum(r.cost_usd) FILTER (WHERE NOT r.scan AND r.charged) AS answer_cost,
                   sum(r.calls) FILTER (WHERE NOT r.scan AND NOT r.charged) AS answer_unknown
            FROM r
            GROUP BY r.model
        )
        SELECT m.id, m.name, m.price_in_usd_per_m, m.price_out_usd_per_m, m.providers, m.efforts, m.expires_on,
               m.eligible, m.seen_at, m.verified_prompt_version, m.verified_at,
               m.tokens_per_page_in, m.tokens_per_page_out, m.enabled_at, s.page_in, s.page_out,
               m.chat_eligible, m.chat_verified_prompt, m.chat_verified_at,
               coalesce(s.scan_pages, 0)::bigint, coalesce(s.scan_pages_costed, 0)::bigint, s.scan_cost,
               coalesce(s.scan_unknown, 0)::bigint, coalesce(s.questions, 0)::bigint,
               coalesce(s.questions_costed, 0)::bigint, s.answer_cost, coalesce(s.answer_unknown, 0)::bigint
        FROM public.model_catalog m
        LEFT JOIN s ON s.model = m.id
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY m.name, m.id
    $$
"""
NEW_COMMENT = (
    "Operator: the model catalogue with prices, providers, scan and answer verification, whether each"
    " model is enabled on the platform, the average tokens of a scanned page, and the real cost"
    " OpenRouter reported for its scanned pages and answered questions, over the last 30 days of the"
    " ledger (calls with no reported cost counted apart). Platform data and numbers only."
)

# 0026's, restored by the downgrade (copied exactly).
OLD = f"""
    CREATE FUNCTION public.kaagaz_op_models(p_operator uuid)
    RETURNS TABLE (id text, name text, price_in_usd_per_m numeric, price_out_usd_per_m numeric,
                   providers text[], efforts text[], expires_on date, eligible boolean, seen_at timestamptz,
                   verified_prompt_version text, verified_at timestamptz,
                   tokens_per_page_in integer, tokens_per_page_out integer, enabled_at timestamptz,
                   ledger_tokens_in numeric, ledger_tokens_out numeric, chat_eligible boolean,
                   chat_verified_prompt text, chat_verified_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH s AS (
            SELECT u.model, sum(u.input_tokens) / sum(u.calls) AS page_in,
                   sum(u.output_tokens) / sum(u.calls) AS page_out
            FROM public.model_usage u
            WHERE u.kind = 'scan' AND NOT u.cached AND u.created_at >= now() - make_interval(days => {LEDGER_DAYS})
            GROUP BY u.model
        )
        SELECT m.id, m.name, m.price_in_usd_per_m, m.price_out_usd_per_m, m.providers, m.efforts, m.expires_on,
               m.eligible, m.seen_at, m.verified_prompt_version, m.verified_at,
               m.tokens_per_page_in, m.tokens_per_page_out, m.enabled_at, s.page_in, s.page_out,
               m.chat_eligible, m.chat_verified_prompt, m.chat_verified_at
        FROM public.model_catalog m
        LEFT JOIN s ON s.model = m.id
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY m.name, m.id
    $$
"""
OLD_COMMENT = (
    "Operator: the model catalogue with prices, providers, scan and answer"
    " verification, whether each model is enabled on the platform, and the average tokens of a scanned"
    " page over the last 30 days of the ledger. Platform data and numbers only."
)


def _replace(create: str, comment: str) -> list[str]:
    return [
        f"DROP FUNCTION public.{SIGNATURE}",
        create,
        f"COMMENT ON FUNCTION public.{SIGNATURE} IS {_literal(comment)}",
        f"REVOKE ALL ON FUNCTION public.{SIGNATURE} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{SIGNATURE} TO {OPERATOR_ROLE}",
    ]


def upgrade() -> None:
    for statement in _replace(NEW, NEW_COMMENT):
        op.execute(statement)


def downgrade() -> None:
    for statement in _replace(OLD, OLD_COMMENT):
        op.execute(statement)
