"""The reading pause (#478, Tushar, 3 Nov 2026): a daily page limit per organisation, and a
monthly budget that pauses reading when it is reached.

* ``platform_ai_settings`` and ``customer_ai_settings``: ``daily_page_limit``, the pages AI may
  read for the organisation in one India day (IST), 1 to MAX_PAGES_A_DAY, nullable with a
  named CHECK. NULL inherits as the scan cost settings do (0064): an organisation's the
  platform's, the platform's none (no limit). Every new column is NULL on release day, so
  nothing is held. The app keeps SELECT only on both tables (0026).
* ``reading_days`` (new): one row per (organisation, IST day), the live counter: the pages AI
  read (every reader: Gemini and Mistral) and the dollars their calls reported, bumped as each
  page is read (``kaagaz.ai.usage``), never at the end of a document as ``model_usage`` is;
  ``alerted_at``, when the super admin was emailed that the organisation paused that day
  (once). ``customer_id`` on every row, the ``customer_only`` policy, the app's SELECT and
  INSERT and an UPDATE of the three counted columns only; it goes with its organisation.
* ``documents.reading_held`` (new): daily_limit or budget while the document waits in the
  database for its organisation's pause to end (not in the queue, so the server can still
  switch itself off at night); ``documents_reading_held`` finds them. The app's UPDATE on
  documents (kept) covers it. ``kaagaz_customer_ids_held`` (the app's, owner rights, ids only,
  as 0002's ``kaagaz_customer_ids_with_trash``): the organisations with a held document, so the
  release sweep looks at those alone.
* The operator's four AI functions (0065's) read, save and audit ``daily_page_limit``, refuse
  one outside 1 to MAX_PAGES_A_DAY (``pages_out_of_range``), and the defaults count the
  organisations inheriting it. The operator's platform audit holds it, the organisation's own
  audit row does not (0064's rule for the AI settings). Each is dropped and created again from 0065's text with each
  change made exactly once (``_changed``), with the same comment, grants, SECURITY DEFINER and
  empty search path.
* ``kaagaz_op_reading_today`` (new): for the console, one organisation's pages and dollars on
  one day (the caller's IST date). Numbers only.

The downgrade drops the two new functions, puts 0065's four functions back exactly, then drops the
index, the columns and the table. Lost on downgrade: the limits, the counters and the holds
(held documents stay received with no job; the stuck-document sweep queues them again).

Revision ID: 0066
Revises: 0065 (scan reader)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

# The lead re-chains the safety plan's migrations when merging: change this one line only.
PREVIOUS = "0065"

revision = "0066"
down_revision = PREVIOUS
branch_labels = None
depends_on = None


def _load_0065() -> ModuleType:
    path = Path(__file__).with_name("0065_scan_reader.py")
    spec = importlib.util.spec_from_file_location("migration_0065_for_0066", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0065 = _load_0065()
_changed = _0065._changed
_create = _0065._create
_comment = _0065._comment
_literal = _0065._literal
_ENABLED = _0065._ENABLED
APP_ROLE = _0065._0064.APP_ROLE

ORG_AI = _0065.ORG_AI
AI_DEFAULTS = _0065.AI_DEFAULTS
SET_ORG_AI_OLD = _0065.SET_ORG_AI
SET_AI_DEFAULTS_OLD = _0065.SET_AI_DEFAULTS
# The saves take the daily page limit after the Mistral model.
SET_ORG_AI = SET_ORG_AI_OLD.removesuffix(")") + ", integer)"
SET_AI_DEFAULTS = SET_AI_DEFAULTS_OLD.removesuffix(")") + ", integer)"
READING_TODAY = "kaagaz_op_reading_today(uuid, uuid, date)"
CUSTOMERS_HELD = "kaagaz_customer_ids_held()"
MAX_PAGES_A_DAY = 1_000_000  # kaagaz.api.routers.operator_models.MAX_PAGES_A_DAY
HOLDS = "('daily_limit', 'budget')"  # kaagaz.jobs.reading_pause.DAILY_LIMIT, BUDGET

# --- the changes, each made exactly once in its body (``_changed``) ---------------------------
_PARAMS = (
    "p_mistral_model text, OUT outcome text",
    "p_mistral_model text, p_daily_page_limit integer, OUT outcome text",
)
_INHERITING = (
    "               count(*) FILTER (WHERE s.mistral_model IS NULL)\n",
    "               count(*) FILTER (WHERE s.mistral_model IS NULL),\n"
    "               count(*) FILTER (WHERE s.daily_page_limit IS NULL)\n",
)
# After the output limit's check, before anything is written.
_RANGE = (
    "            outcome := 'limit_out_of_range';\n            RETURN;\n        END IF;\n",
    "            outcome := 'limit_out_of_range';\n            RETURN;\n        END IF;\n"
    f"        IF p_daily_page_limit NOT BETWEEN 1 AND {MAX_PAGES_A_DAY} THEN\n"
    "            outcome := 'pages_out_of_range';\n            RETURN;\n        END IF;\n",
)
ORG_SET_CHANGES = [
    _PARAMS,
    _RANGE,
    (
        "(customer_id, scan_reader, mistral_model,",
        "(customer_id, daily_page_limit, scan_reader, mistral_model,",
    ),
    (
        "VALUES (p_customer, p_scan_reader, p_mistral_model,",
        "VALUES (p_customer, p_daily_page_limit, p_scan_reader, p_mistral_model,",
    ),
    (
        "SET scan_reader = EXCLUDED.scan_reader,",
        "SET daily_page_limit = EXCLUDED.daily_page_limit, scan_reader = EXCLUDED.scan_reader,",
    ),
    (
        "'scan_reader', p_scan_reader, 'mistral_model', p_mistral_model,",
        "'scan_reader', p_scan_reader, 'mistral_model', p_mistral_model,\n"
        "            'daily_page_limit', p_daily_page_limit,",
    ),
    # The operator's platform audit holds it; the organisation's own row does not (0064's rule).
    ("'scan_reader', 'mistral_model']);", "'scan_reader', 'mistral_model', 'daily_page_limit']);"),
]
DEFAULTS_SET_CHANGES = [
    _PARAMS,
    _RANGE,
    (
        "        v_mistral_model bigint;\n",
        "        v_mistral_model bigint;\n        v_daily_page_limit bigint;\n",
    ),
    _INHERITING,
    ("v_scan_reader, v_mistral_model;", "v_scan_reader, v_mistral_model, v_daily_page_limit;"),
    (
        "SET scan_reader = p_scan_reader,",
        "SET daily_page_limit = p_daily_page_limit, scan_reader = p_scan_reader,",
    ),
    (
        "THEN 0 ELSE v_mistral_model END)));",
        "THEN 0 ELSE v_mistral_model END,\n                'daily_page_limit', CASE WHEN v_before ->"
        " 'daily_page_limit' = v_after -> 'daily_page_limit' THEN 0 ELSE v_daily_page_limit END)));",
    ),
]
# The defaults' before and after (twice in the save) hold it.
_DEFAULTS_JSON = (
    "'scan_reader', p.scan_reader, 'mistral_model', p.mistral_model)",
    "'scan_reader', p.scan_reader, 'mistral_model', p.mistral_model, 'daily_page_limit', p.daily_page_limit)",
)
ORG_READ_CHANGES = [
    (
        "mistral_model text,\n                   ensemble boolean",
        "mistral_model text, daily_page_limit integer,\n                   ensemble boolean",
    ),
    ("s.scan_reader, s.mistral_model,\n", "s.scan_reader, s.mistral_model, s.daily_page_limit,\n"),
]
DEFAULTS_READ_CHANGES = [
    (
        "mistral_model text,\n                   orgs_inheriting_scan_models",
        "mistral_model text, daily_page_limit integer,\n                   orgs_inheriting_scan_models",
    ),
    (
        "orgs_inheriting_mistral_model bigint)",
        "orgs_inheriting_mistral_model bigint, orgs_inheriting_daily_page_limit bigint)",
    ),
    ("p.scan_reader, p.mistral_model, n.*", "p.scan_reader, p.mistral_model, p.daily_page_limit, n.*"),
    _INHERITING,
]

# The bodies in force now (0065's), each restored exactly by the downgrade.
OLD = {
    ORG_AI: _0065.NEW[ORG_AI],
    AI_DEFAULTS: _0065.NEW[AI_DEFAULTS],
    SET_ORG_AI_OLD: _0065.NEW[SET_ORG_AI_OLD],
    SET_AI_DEFAULTS_OLD: _0065.NEW[SET_AI_DEFAULTS_OLD],
}
NEW = {
    ORG_AI: _changed(OLD[ORG_AI], ORG_READ_CHANGES),
    AI_DEFAULTS: _changed(OLD[AI_DEFAULTS], DEFAULTS_READ_CHANGES),
    SET_ORG_AI: _changed(OLD[SET_ORG_AI_OLD], ORG_SET_CHANGES),
    SET_AI_DEFAULTS: _changed(
        OLD[SET_AI_DEFAULTS_OLD], [*DEFAULTS_SET_CHANGES, _DEFAULTS_JSON], times={_DEFAULTS_JSON[0]: 2}
    ),
}

# One organisation's counter on one day: 0 and 0 with no row; no row for an operator who is
# not enabled.
READING_TODAY_SQL = f"""
    CREATE FUNCTION public.kaagaz_op_reading_today(p_operator uuid, p_customer uuid, p_day date)
    RETURNS TABLE (pages integer, cost_usd numeric)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT COALESCE(max(r.pages), 0), COALESCE(max(r.cost_usd), 0)
        FROM public.reading_days r
        WHERE r.customer_id = p_customer AND r.day = p_day
        HAVING EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
"""
READING_TODAY_COMMENT = (
    "Operator: one organisation's pages read by AI and the dollars reported on one India day"
    " (the reading pause's counter). Numbers only."
)

# The release sweep's organisations: ids only, on the partial index.
CUSTOMERS_HELD_SQL = """
    CREATE FUNCTION public.kaagaz_customer_ids_held()
    RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS
    $$ SELECT DISTINCT d.customer_id FROM public.documents d WHERE d.reading_held IS NOT NULL $$
"""
CUSTOMERS_HELD_COMMENT = (
    "The worker's release sweep: the organisations with a document held by a reading pause. Ids only."
)

TABLE = """
    CREATE TABLE reading_days (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        day date NOT NULL,
        pages integer NOT NULL DEFAULT 0 CONSTRAINT reading_days_pages CHECK (pages >= 0),
        cost_usd numeric(14, 6) NOT NULL DEFAULT 0 CONSTRAINT reading_days_cost CHECK (cost_usd >= 0),
        alerted_at timestamptz NULL,
        PRIMARY KEY (customer_id, day)
    )
"""
TABLE_COMMENT = (
    "The reading pause's live counter (#478): one row per organisation and India day (IST), bumped"
    " as each page is read, so a daily page limit holds while a long document is still being read."
)
# {table.column: comment} (0007's rule: every column has one).
COMMENTS = {
    "reading_days.customer_id": "The organisation.",
    "reading_days.day": "The India date (IST) the pages were read on.",
    "reading_days.pages": "Pages AI read that day (Gemini and Mistral; never the operator's accuracy checks)."
    " A document being started holds one page here until it ends.",
    "reading_days.cost_usd": "The dollars OpenRouter and Mistral reported for that day's page reads, as"
    " they were made (unknown costs count nothing); other work reaches the budget through model_usage.",
    "reading_days.alerted_at": "When the super admin was emailed that this organisation's reading paused"
    " that day; NULL: not yet. Once a day.",
    "documents.reading_held": "daily_limit or budget: the document waits for its organisation's reading"
    " pause to end, held here rather than in the queue; NULL: not held.",
    **{
        f"{table}.daily_page_limit": f"The most pages AI may read for an organisation in one India day, 1 to"
        f" {MAX_PAGES_A_DAY}; new documents wait for the next day past it. NULL, " + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", "no limit."),
        )
    },
}


def _limit_column(table: str, prefix: str) -> str:
    return (
        f"ALTER TABLE {table} ADD COLUMN daily_page_limit integer NULL CONSTRAINT {prefix}_daily_page_limit"
        f" CHECK (daily_page_limit BETWEEN 1 AND {MAX_PAGES_A_DAY})"
    )


UPGRADE = [
    # The four functions with their old parameters and columns go first: nothing calls them now.
    *[f"DROP FUNCTION public.{signature}" for signature in OLD],
    _limit_column("customer_ai_settings", "ai_customer_settings"),
    _limit_column("platform_ai_settings", "ai_platform_settings"),
    TABLE,
    f"GRANT SELECT, INSERT ON reading_days TO {APP_ROLE}",
    f"GRANT UPDATE (pages, cost_usd, alerted_at) ON reading_days TO {APP_ROLE}",
    "ALTER TABLE reading_days ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON reading_days
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    f"COMMENT ON TABLE reading_days IS {_literal(TABLE_COMMENT)}",
    "ALTER TABLE documents ADD COLUMN reading_held text NULL"
    f" CONSTRAINT documents_reading_held CHECK (reading_held IN {HOLDS})",
    "CREATE INDEX documents_reading_held ON documents (customer_id) WHERE reading_held IS NOT NULL",
    "COMMENT ON INDEX documents_reading_held IS 'The documents held by a reading pause (the release sweep).'",
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    # The four functions again, with the limit; the same comments and grants.
    *[
        statement
        for signature, body in NEW.items()
        for statement in _create(signature, body, _comment(signature))
    ],
    *_create(READING_TODAY, READING_TODAY_SQL, READING_TODAY_COMMENT),
    *_create(CUSTOMERS_HELD, CUSTOMERS_HELD_SQL, CUSTOMERS_HELD_COMMENT, APP_ROLE),
]

DOWNGRADE = [
    f"DROP FUNCTION public.{CUSTOMERS_HELD}",
    f"DROP FUNCTION public.{READING_TODAY}",
    *[f"DROP FUNCTION public.{signature}" for signature in NEW],
    "DROP INDEX documents_reading_held",
    "ALTER TABLE documents DROP COLUMN reading_held",
    "DROP TABLE reading_days",
    "ALTER TABLE customer_ai_settings DROP COLUMN daily_page_limit",
    "ALTER TABLE platform_ai_settings DROP COLUMN daily_page_limit",
    # The four functions as they were, with their comments and grants.
    *[
        statement
        for signature, body in OLD.items()
        for statement in _create(signature, body, _comment(signature))
    ],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
