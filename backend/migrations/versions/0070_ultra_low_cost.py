"""Ultra low cost (#488, Tushar, 4 Nov 2026; NEEDS VRUSHIT'S APPROVAL: schema and operator
functions): a third scan reader that reads each page once, as briefly as possible, and never for
more than a ceiling a page.

* ``platform_ai_settings`` and ``customer_ai_settings``: ``scan_reader``'s CHECK (0065) also takes
  ``ultra_low_cost`` (kaagaz.db.models.ULTRA_LOW_COST); one field, so Mistral and Ultra low cost
  are never both chosen. ``ulc_ceiling_paise`` (new): the most one read of a page may cost while
  it reads, 20 to 40 paise, smallint NULL with a named CHECK. NULL inherits as the other AI
  settings do: an organisation's the platform's, the platform's the code's (40 paise,
  kaagaz.db.models.ULC_DEFAULT_PAISE). Every new value is NULL on release day and no reader
  changes, so no organisation reads differently until the super admin chooses it. The app keeps
  SELECT only on both tables (0026).
* The operator's four AI functions (0066's) read, save and audit the ceiling, refuse one outside
  20 to 40 (``ceiling_out_of_range``), and the defaults count the organisations inheriting it.
  The operator's platform audit holds it; the organisation's own audit row does not (0064's
  rule). Each is dropped and created again from 0066's text with each change made exactly once
  (``_changed``), with the same comment, grants, SECURITY DEFINER and empty search path. Whether
  the lead can read with minimal thinking is the API's check (409 ``ulc_needs_gemini``).
* ``kaagaz_op_ulc_cost`` (new): for the operator's estimate, one model's ``ulc_scan`` ledger rows
  (never cached, OpenRouter's reported cost only) of the last LEDGER_DAYS days, one
  organisation's or every one's: the pages, their cost, the pages read locally to stay within the
  ceiling (``limit_hits``: the reader counts one on its row for each such page) and the highest
  cost a page of one document. Numbers only. ``model_usage_ulc_reads`` keeps it to those rows.
* ``kaagaz_op_models`` (0033): its pages read and their real cost count ``ulc_scan`` as a page
  read too; the live text is 0033's, changed in place (CREATE OR REPLACE keeps the owner, grants
  and comment).

The downgrade puts Ultra low cost back to NULL (Gemini, as every organisation read before: no
reader is lost to a CHECK), drops the new function and index, puts 0033's and 0066's functions
and 0065's CHECKs and comments back exactly, then drops the columns. Lost on downgrade: the
choice of Ultra low cost and the ceilings (the ledger's ``ulc_scan`` rows stay).

Revision ID: 0070
Revises: 0069a (paused runs)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

PREVIOUS = "0069a"
revision = "0070"
down_revision = PREVIOUS
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{name[:4]}_for_0070", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0066 = _load("0066_reading_pause.py")
_0065 = _0066._0065
_0033 = _load("0033_operator_real_cost.py")
_changed = _0066._changed
_create = _0066._create
_comment = _0066._comment
_literal = _0066._literal
_ENABLED = _0066._ENABLED
LEDGER_DAYS = _0065._0064.LEDGER_DAYS

ORG_AI = _0066.ORG_AI
AI_DEFAULTS = _0066.AI_DEFAULTS
SET_ORG_AI_OLD = _0066.SET_ORG_AI
SET_AI_DEFAULTS_OLD = _0066.SET_AI_DEFAULTS
# The saves take the ceiling after the daily page limit.
SET_ORG_AI = SET_ORG_AI_OLD.removesuffix(")") + ", integer)"
SET_AI_DEFAULTS = SET_AI_DEFAULTS_OLD.removesuffix(")") + ", integer)"
ULC_COST = "kaagaz_op_ulc_cost(uuid, uuid, text)"
MODELS = "kaagaz_op_models(uuid)"

ULC = "ultra_low_cost"  # kaagaz.db.models.ULTRA_LOW_COST
READERS = "('openrouter', 'mistral', 'ultra_low_cost')"
CEILING_LOW, CEILING_HIGH = 20, 40  # kaagaz.db.models.ULC_CEILING_PAISE
ULC_KIND = "ulc_scan"  # kaagaz.ai.usage's ledger kind of an Ultra low cost page read
_PREFIXES = {"customer_ai_settings": "ai_customer_settings", "platform_ai_settings": "ai_platform_settings"}

# --- the changes, each made exactly once in its body (``_changed``) ---------------------------
_PARAMS = (
    "p_daily_page_limit integer, OUT outcome text",
    "p_daily_page_limit integer, p_ulc_ceiling_paise integer, OUT outcome text",
)
_INHERITING = (
    "               count(*) FILTER (WHERE s.daily_page_limit IS NULL)\n",
    "               count(*) FILTER (WHERE s.daily_page_limit IS NULL),\n"
    "               count(*) FILTER (WHERE s.ulc_ceiling_paise IS NULL)\n",
)
# After the daily page limit's check, before anything is written.
_RANGE = (
    "            outcome := 'pages_out_of_range';\n            RETURN;\n        END IF;\n",
    "            outcome := 'pages_out_of_range';\n            RETURN;\n        END IF;\n"
    f"        IF p_ulc_ceiling_paise NOT BETWEEN {CEILING_LOW} AND {CEILING_HIGH} THEN\n"
    "            outcome := 'ceiling_out_of_range';\n            RETURN;\n        END IF;\n",
)
ORG_SET_CHANGES = [
    _PARAMS,
    _RANGE,
    ("(customer_id, daily_page_limit,", "(customer_id, ulc_ceiling_paise, daily_page_limit,"),
    (
        "VALUES (p_customer, p_daily_page_limit,",
        "VALUES (p_customer, p_ulc_ceiling_paise, p_daily_page_limit,",
    ),
    (
        "SET daily_page_limit = EXCLUDED.daily_page_limit,",
        "SET ulc_ceiling_paise = EXCLUDED.ulc_ceiling_paise, daily_page_limit = EXCLUDED.daily_page_limit,",
    ),
    (
        "            'daily_page_limit', p_daily_page_limit,",
        "            'daily_page_limit', p_daily_page_limit, 'ulc_ceiling_paise', p_ulc_ceiling_paise,",
    ),
    # The operator's platform audit holds it; the organisation's own row does not (0064's rule).
    (
        "'mistral_model', 'daily_page_limit']);",
        "'mistral_model', 'daily_page_limit', 'ulc_ceiling_paise']);",
    ),
]
DEFAULTS_SET_CHANGES = [
    _PARAMS,
    _RANGE,
    (
        "        v_daily_page_limit bigint;\n",
        "        v_daily_page_limit bigint;\n        v_ulc_ceiling_paise bigint;\n",
    ),
    _INHERITING,
    ("v_mistral_model, v_daily_page_limit;", "v_mistral_model, v_daily_page_limit, v_ulc_ceiling_paise;"),
    (
        "SET daily_page_limit = p_daily_page_limit,",
        "SET ulc_ceiling_paise = p_ulc_ceiling_paise, daily_page_limit = p_daily_page_limit,",
    ),
    (
        "THEN 0 ELSE v_daily_page_limit END)));",
        "THEN 0 ELSE v_daily_page_limit END,\n                'ulc_ceiling_paise', CASE WHEN v_before ->"
        " 'ulc_ceiling_paise' = v_after -> 'ulc_ceiling_paise' THEN 0 ELSE v_ulc_ceiling_paise END)));",
    ),
]
# The defaults' before and after (twice in the save) hold it.
_DEFAULTS_JSON = (
    "'daily_page_limit', p.daily_page_limit)",
    "'daily_page_limit', p.daily_page_limit, 'ulc_ceiling_paise', p.ulc_ceiling_paise)",
)
ORG_READ_CHANGES = [
    (
        "daily_page_limit integer,\n                   ensemble boolean",
        "daily_page_limit integer, ulc_ceiling_paise smallint,\n                   ensemble boolean",
    ),
    ("s.mistral_model, s.daily_page_limit,\n", "s.mistral_model, s.daily_page_limit, s.ulc_ceiling_paise,\n"),
]
DEFAULTS_READ_CHANGES = [
    (
        "daily_page_limit integer,\n                   orgs_inheriting_scan_models",
        "daily_page_limit integer, ulc_ceiling_paise smallint,\n                   orgs_inheriting_scan_models",
    ),
    (
        "orgs_inheriting_daily_page_limit bigint)",
        "orgs_inheriting_daily_page_limit bigint, orgs_inheriting_ulc_ceiling_paise bigint)",
    ),
    ("p.daily_page_limit, n.*", "p.daily_page_limit, p.ulc_ceiling_paise, n.*"),
    _INHERITING,
]

# The bodies in force now (0066's), each restored exactly by the downgrade.
OLD = {
    ORG_AI: _0066.NEW[ORG_AI],
    AI_DEFAULTS: _0066.NEW[AI_DEFAULTS],
    SET_ORG_AI_OLD: _0066.NEW[SET_ORG_AI_OLD],
    SET_AI_DEFAULTS_OLD: _0066.NEW[SET_AI_DEFAULTS_OLD],
}
NEW = {
    ORG_AI: _changed(OLD[ORG_AI], ORG_READ_CHANGES),
    AI_DEFAULTS: _changed(OLD[AI_DEFAULTS], DEFAULTS_READ_CHANGES),
    SET_ORG_AI: _changed(OLD[SET_ORG_AI_OLD], ORG_SET_CHANGES),
    SET_AI_DEFAULTS: _changed(
        OLD[SET_AI_DEFAULTS_OLD], [*DEFAULTS_SET_CHANGES, _DEFAULTS_JSON], times={_DEFAULTS_JSON[0]: 2}
    ),
}

# The measured cost of Ultra low cost (#488): one model's uncached ``ulc_scan`` rows of the last
# LEDGER_DAYS days with OpenRouter's own cost (an estimated or unknown one is never counted), one
# organisation's (every one's when p_customer is NULL), per document first, so the highest cost
# a page of one document is kept. 0 pages and NULLs with none; no row for an operator who is not
# enabled.
ULC_COST_SQL = f"""
    CREATE FUNCTION public.kaagaz_op_ulc_cost(p_operator uuid, p_customer uuid, p_model text)
    RETURNS TABLE (pages bigint, cost_usd numeric, limit_hits bigint, top_usd_per_page numeric)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH d AS (
            SELECT sum(u.pages) AS pages, sum(u.cost_usd) AS cost_usd, sum(u.limit_hits) AS limit_hits
            FROM public.model_usage u
            WHERE u.kind = '{ULC_KIND}' AND NOT u.cached AND u.model = p_model
              AND u.cost_usd IS NOT NULL AND NOT u.cost_estimated
              AND (p_customer IS NULL OR u.customer_id = p_customer)
              AND u.created_at >= now() - make_interval(days => {LEDGER_DAYS})
            GROUP BY u.customer_id, u.document_id
        )
        SELECT COALESCE(sum(d.pages), 0)::bigint, sum(d.cost_usd), COALESCE(sum(d.limit_hits), 0)::bigint,
               max(d.cost_usd / d.pages) FILTER (WHERE d.pages > 0)
        FROM d
        HAVING EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
"""
ULC_COST_COMMENT = (
    "Operator: what Ultra low cost really cost for one model over the last 30 days of the ledger, one"
    " organisation's or every one's: its pages, the cost OpenRouter reported for them, the pages read"
    " locally to stay within the ceiling, and the highest cost a page of one document. Numbers only."
)

# 0033's catalogue: an Ultra low cost read is a page read too (both of its uses of the kinds).
MODELS_NEW = _changed(
    _0033.NEW,
    [(_0033.SCAN_KINDS, f"{_0033.SCAN_KINDS}, '{ULC_KIND}'")],
    times={_0033.SCAN_KINDS: 2},
)
_REPLACE = ("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")  # in place: grants and comment stay

# {table.column: comment} (0007's rule: every column has one).
COMMENTS = {
    **{
        f"{table}.scan_reader": "Who reads scanned pages: openrouter (Gemini through OpenRouter), mistral"
        " (Mistral OCR) or ultra_low_cost (Gemini, each page once as briefly as possible, never past"
        " ulc_ceiling_paise). NULL, " + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", "openrouter."),
        )
    },
    **{
        f"{table}.ulc_ceiling_paise": f"The most one read of a page may cost while ultra_low_cost reads,"
        f" {CEILING_LOW} to {CEILING_HIGH} paise; a page that would cost more is read locally. NULL, "
        + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", f"{CEILING_HIGH} paise."),
        )
    },
}


def _readers(table: str, readers: str) -> str:
    """``table``'s scan_reader CHECK, under its 0065 name, taking ``readers``."""
    name = f"{_PREFIXES[table]}_scan_reader"
    return (
        f"ALTER TABLE {table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK (scan_reader IN {readers})"
    )


def _ceiling(table: str) -> str:
    return (
        f"ALTER TABLE {table} ADD COLUMN ulc_ceiling_paise smallint NULL CONSTRAINT"
        f" {_PREFIXES[table]}_ulc_ceiling_paise CHECK (ulc_ceiling_paise BETWEEN {CEILING_LOW} AND {CEILING_HIGH})"
    )


UPGRADE = [
    # The four functions with their old parameters and columns go first: nothing calls them now.
    *[f"DROP FUNCTION public.{signature}" for signature in OLD],
    *[_readers(table, READERS) for table in _PREFIXES],
    *[_ceiling(table) for table in _PREFIXES],
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    # The four functions again, with the ceiling; the same comments and grants.
    *[
        statement
        for signature, body in NEW.items()
        for statement in _create(signature, body, _comment(signature))
    ],
    f"CREATE INDEX model_usage_ulc_reads ON model_usage (created_at) WHERE kind = '{ULC_KIND}' AND NOT cached",
    "COMMENT ON INDEX model_usage_ulc_reads IS 'Every organisation''s uncached Ultra low cost page reads by"
    " time (the operator''s Ultra low cost cost).'",
    *_create(ULC_COST, ULC_COST_SQL, ULC_COST_COMMENT),
    _changed(MODELS_NEW, [_REPLACE]),
]

DOWNGRADE = [
    _changed(_0033.NEW, [_REPLACE]),
    f"DROP FUNCTION public.{ULC_COST}",
    "DROP INDEX model_usage_ulc_reads",
    *[f"DROP FUNCTION public.{signature}" for signature in NEW],
    # Gemini again, as before 0070: no organisation is left with a reader the CHECK refuses.
    *[f"UPDATE {table} SET scan_reader = NULL WHERE scan_reader = '{ULC}'" for table in _PREFIXES],
    *[_readers(table, _0065.READERS) for table in _PREFIXES],
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in _0065.COMMENTS.items()],
    *[f"ALTER TABLE {table} DROP COLUMN ulc_ceiling_paise" for table in _PREFIXES],
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
