"""What Kaagaz learns and the operator's new counts (PRD 6 package F3, decision 6).

* ``feedback_learning`` (customer_id, created_at) serves the marks ``learned_for`` reads.
* ``learning_removals``: each Remove on "What Kaagaz learned"; marks given before it teach
  no more. 0036's rules: ``customer_only``, SELECT and INSERT only, (customer_id, id) keys
  to the document (CASCADE) and the admin (SET NULL (removed_by)), every column commented.
* ``kaagaz_op_answer_weeks`` (issue #244): per organisation and ISO week that has ended (no
  sliding window): replies, answered, not found and clarify (a clarify is never answered or
  not found; a rating on one is not counted), rated and Useful. Nothing hidden comes out by
  subtraction: answered, not found, clarify and the rest of replies show together, only when
  each of the four is 0 or at least K = 10; Useful only when it and the rest of rated are.
  No person, document or text. As 0024's readers: STABLE, SECURITY DEFINER, empty search
  path, the operator check, ``kaagaz_operator`` only.

The downgrade refuses while any Remove is kept, then drops the function, table and index.

Revision ID: 0051
Revises: 0050 (feedback reasons and handled state)
"""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "10s"  # as env.py's
APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = "kaagaz_operator"
K = 10  # as 0024's: every part a count is split into is 0 or at least this many
REMOVALS_COMMENT = "A Remove on What Kaagaz learned: marks on the document given before it teach no more."
REMOVAL_COLUMNS = {
    "id": "The Remove.",
    "customer_id": "→ customers.id, the customer the row belongs to (row level security filters on it).",
    "document_id": "→ documents.id (same customer), the document Kaagaz no longer lifts. Deleted with it.",
    "removed_by": "→ users.id (same customer), the admin who removed it. NULL when that user is gone.",
    "removed_at": "When it was removed.",
}
WEEKS_NAME = "public.kaagaz_op_answer_weeks"
WEEKS = f"{WEEKS_NAME}(uuid, date, integer, text)"
WEEKS_COMMENT = (
    "Operator: per organisation and ended ISO week, replies, answered, not found, clarify, rated and"
    " Useful. Answered, not found and clarify show together, only when each of them and the rest of"
    " replies is 0 or at least 10; Useful only when it and the rest of rated are."
)


def _literal(comment: str) -> str:
    return "'" + comment.replace("'", "''") + "'"


def _safe(*parts: str) -> str:
    """True when every part is 0 or at least K, so no sum or difference of them is a small count."""
    return " AND ".join(f"({part} = 0 OR {part} >= {K})" for part in parts)


ANSWER_WEEKS = f"""
CREATE FUNCTION {WEEKS_NAME}(p_operator uuid, p_monday date, p_weeks integer, p_zone text)
RETURNS TABLE (customer_id uuid, week date, replies bigint, answered bigint, not_found bigint, clarify bigint,
               rated bigint, useful bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
    #variable_conflict use_column
    DECLARE v_lo timestamptz; v_hi timestamptz;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.operators o
                       WHERE o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL) THEN
            RETURN;
        END IF;
        IF p_monday IS NULL OR p_weeks IS NULL OR p_weeks NOT BETWEEN 1 AND 53
           OR extract(isodow FROM p_monday) <> 1 THEN
            RAISE EXCEPTION 'whole weeks from a Monday, 1 to 53' USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names z WHERE z.name = p_zone) THEN
            RAISE EXCEPTION 'unknown time zone' USING ERRCODE = 'check_violation';
        END IF;
        v_lo := p_monday::timestamp AT TIME ZONE p_zone;
        v_hi := (p_monday + 7 * p_weeks)::timestamp AT TIME ZONE p_zone;
        IF v_hi > now() THEN
            RAISE EXCEPTION 'only weeks that have ended' USING ERRCODE = 'check_violation';
        END IF;
        RETURN QUERY
        SELECT c.id, w.week, w.replies, CASE WHEN w.split THEN w.answered END,
               CASE WHEN w.split THEN w.not_found END, CASE WHEN w.split THEN w.clarify END, w.rated,
               CASE WHEN w.rated_split THEN w.useful END
        FROM public.customers c
        CROSS JOIN LATERAL (
          SELECT g.*, {_safe("g.answered", "g.not_found", "g.clarify", "g.replies - g.answered - g.not_found - g.clarify")}
                 AS split, {_safe("g.useful", "g.rated - g.useful")} AS rated_split
          FROM (
            SELECT date_trunc('week', m.created_at AT TIME ZONE p_zone)::date AS week, count(*) AS replies,
                   count(*) FILTER (WHERE m.answer ->> 'status' = 'answered') AS answered,
                   count(*) FILTER (WHERE m.answer ->> 'status' = 'not_found') AS not_found,
                   count(*) FILTER (WHERE m.answer ->> 'status' = 'clarify') AS clarify,
                   count(fb.id) FILTER (WHERE m.answer ->> 'status' IS DISTINCT FROM 'clarify') AS rated,
                   count(fb.id) FILTER (WHERE fb.rating = 'up' AND m.answer ->> 'status' IS DISTINCT FROM 'clarify')
                       AS useful
            FROM public.messages m
            LEFT JOIN public.feedback fb ON fb.customer_id = m.customer_id AND fb.message_id = m.id
            WHERE m.customer_id = c.id AND m.role = 'assistant' AND m.created_at >= v_lo AND m.created_at < v_hi
            GROUP BY 1
          ) g
        ) w
        ORDER BY c.id, w.week;
    END
$$
"""

UPGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    "CREATE INDEX feedback_learning ON feedback (customer_id, created_at DESC)",
    """
    CREATE TABLE learning_removals (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        document_id uuid NOT NULL,
        removed_by uuid NULL,
        removed_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, removed_by) REFERENCES users (customer_id, id) ON DELETE SET NULL (removed_by)
    )
    """,
    "CREATE INDEX learning_removals_document ON learning_removals (customer_id, document_id, removed_at DESC)",
    f"GRANT SELECT, INSERT ON learning_removals TO {APP_ROLE}",
    "ALTER TABLE learning_removals ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON learning_removals
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    f"COMMENT ON TABLE learning_removals IS {_literal(REMOVALS_COMMENT)}",
    *[
        f"COMMENT ON COLUMN learning_removals.{c} IS {_literal(comment)}"
        for c, comment in REMOVAL_COLUMNS.items()
    ],
    ANSWER_WEEKS,
    f"COMMENT ON FUNCTION {WEEKS} IS {_literal(WEEKS_COMMENT)}",
    f"REVOKE ALL ON FUNCTION {WEEKS} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION {WEEKS} TO {OPERATOR_ROLE}",
]

DOWNGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM learning_removals) THEN
            RAISE EXCEPTION 'an admin removed what Kaagaz learned; downgrading would lose it'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    f"DROP FUNCTION IF EXISTS {WEEKS}",
    "DROP TABLE IF EXISTS learning_removals",
    "DROP INDEX IF EXISTS feedback_learning",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
