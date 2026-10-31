"""The super admin sets the scan cost settings and sees the approximate cost a page (#463,
Tushar, 31 Oct 2026).

* ``platform_ai_settings`` and ``customer_ai_settings``: ``scan_resolution``,
  ``careful_thinking`` and ``careful_resolution``, nullable text with CHECKs, and
  ``page_max_output_tokens`` (a page read's output limit, 1024 to 65536; ``limit_out_of_range``
  from the saves). NULL inherits field by field: an organisation's the platform's, the
  platform's the server's (OCR_MEDIA_RESOLUTION, OCR_RETRY_THINKING_LEVEL,
  OCR_RETRY_MEDIA_RESOLUTION, openrouter.PAGE_MAX_OUTPUT_TOKENS), as
  ``scan_thinking`` does, whose CHECK now takes ``minimal`` too (the answer levels do not).
  Every new column is NULL on release day, so every value is still the server's. The app
  keeps SELECT only on both tables (0026): only the operator's functions below write them.
* ``model_usage``: ``thinking`` and ``resolution``, what a page read was sent (``scan``,
  ``careful``, ``upgrade``, ``ensemble_scan``); NULL for every other kind and for old rows; and
  ``limit_hits``, the calls whose reply hit the output limit (read again in halves), 0 before.
  The app's table grant (0023) covers them.
* The operator's four AI functions (0026's reads, 0048's saves; no later migration replaces
  them) read, save, check and audit the three fields: ``careful_thinking`` is checked
  against the effective scan models as ``scan_thinking`` is (``level_not_supported``), the
  audit's before and after hold them, and the defaults count the organisations inheriting
  each. Their parameters and result columns change, so each is dropped and created again
  from its own text with each change made exactly once (0037's way), with the same
  comment, grants, SECURITY DEFINER and empty search path.
* ``kaagaz_op_scan_cost`` (new): for the operator's estimate, the cost OpenRouter reported
  for the first reads and Careful's re-reads of one (model, thinking, resolution) each, in
  every organisation's ledger of the last LEDGER_DAYS days, and the scope's scan and careful
  calls (the share of pages read again); and the scope's pages read by the model in those
  days at the first read's settings and at max settings, and how many of each were clean
  (read_quality good). Numbers only.
* ``pages.read_thinking`` and ``read_resolution``: the settings of the read that produced a
  page's stored text (the pipeline writes them); NULL for old pages and pages no model read.
* ``scan_accuracy_checks`` (new; accuracy against max settings): one organisation's check,
  its settings, how many pages it read and the share of words and numbers that matched,
  never a word of text. An operator table as ``platform_audit`` is (its organisation in
  ``subject_customer_id``, row level security on, no policy, no grant): the operator starts
  and reads it through ``kaagaz_op_start_accuracy_check`` (a fresh code; ``check_running``,
  ``rate_limited`` past ACCURACY_CHECKS_PER_HOUR platform-wide, ``no_pages``; audited as
  ``org.accuracy_check``) and ``kaagaz_op_accuracy_check``; the worker claims and finishes
  its own organisation's running check only (``kaagaz_current_customer()``) through
  ``kaagaz_accuracy_check_claim`` and ``kaagaz_accuracy_check_finish``, the app's only rights.
* ``kaagaz_op_org_health`` (0024): a month's spend against the budget leaves out the
  ``accuracy_check`` ledger kind (the check is the operator's, never the organisation's
  budget); its live body has the one line changed exactly once (0061's way).

No index: the estimate reads the same days of every organisation's ledger that
``kaagaz_op_models`` already reads (0026); ``model_usage_period`` serves the scope's part.
The downgrade drops the new function, restores the four exactly, puts a minimal
``scan_thinking`` back to NULL (inherit: the old CHECK refuses it), then drops the columns
and puts the old CHECKs back under their old names. Lost on downgrade: the three settings,
a minimal scan level and the ledger's two columns.

Revision ID: 0064
Revises: 0063 (stale sign-ups)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def _load_0048() -> ModuleType:
    path = Path(__file__).with_name("0048_ai_save_models_without_levels.py")
    spec = importlib.util.spec_from_file_location("migration_0048_for_0064", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0048 = _load_0048()
_0026 = _0048._OLD._OLD  # 0048 loads 0037, which loads 0026
OPERATOR_ROLE = _0026.OPERATOR_ROLE
LEDGER_DAYS = _0026.LEDGER_DAYS
_ENABLED = _0026._ENABLED
_literal = _0026._literal

ORG_AI = "kaagaz_op_org_ai(uuid, uuid)"
AI_DEFAULTS = "kaagaz_op_ai_defaults(uuid)"
SET_ORG_AI_OLD = (
    "kaagaz_op_set_org_ai(uuid, uuid, text[], text, text[], text, text, text[], text, boolean, boolean,"
    " numeric, text, text, bigint)"
)
SET_AI_DEFAULTS_OLD = (
    "kaagaz_op_set_ai_defaults(uuid, text[], text, text[], text, text, text[], text, boolean, text, text,"
    " bigint)"
)
# The new saves take scan_resolution, careful_thinking, careful_resolution and
# page_max_output_tokens after scan_thinking.
SET_ORG_AI = (
    "kaagaz_op_set_org_ai(uuid, uuid, text[], text, text[], text, text, text, text, text, integer, text[],"
    " text, boolean, boolean, numeric, text, text, bigint)"
)
SET_AI_DEFAULTS = (
    "kaagaz_op_set_ai_defaults(uuid, text[], text, text[], text, text, text, text, text, integer, text[],"
    " text, boolean, text, text, bigint)"
)
# A page read's output limit (Tushar, 31 Oct 2026): openrouter.PAGE_MAX_OUTPUT_TOKENS at most.
TOKENS_LOW, TOKENS_HIGH = 1024, 65536
SCAN_COST = "kaagaz_op_scan_cost(uuid, uuid, text, text, text, text, text, text, text, text)"
START_CHECK = (
    "kaagaz_op_start_accuracy_check(uuid, uuid, text, text, text, text, text, integer, integer, bigint)"
)
LATEST_CHECK = "kaagaz_op_accuracy_check(uuid, uuid, text, text, text, text, text, integer)"
CLAIM_CHECK = "kaagaz_accuracy_check_claim(uuid)"
FINISH_CHECK = (
    "kaagaz_accuracy_check_finish(uuid, text, integer, integer, double precision, double precision, numeric)"
)
ORG_HEALTH = "kaagaz_op_org_health(uuid, date, text)"
APP_ROLE = "kaagaz_app"
# shortcut: a fixed platform-wide cap on accuracy checks an hour (Tushar, 31 Oct 2026), not a setting.
ACCURACY_CHECKS_PER_HOUR = 6
CHECK_PAGES = (20, 50, 100)  # the sample sizes an operator may ask for
# A running check older than this is taken as abandoned (a lost queue message): it no longer
# blocks a new one. shortcut: no sweep marks it failed; the next check's start makes it moot.
CHECK_RUNNING_FOR = "interval '1 hour'"
# A page a model read (its id has a "/": never rapidocr, text_layer or cells) with its picture kept.
MODEL_READ = "p.has_image AND p.route = 'ocr' AND p.read_by LIKE '%/%'"

SCAN_LEVELS = "('minimal', 'low', 'medium', 'high')"  # kaagaz.settings.THINKING_LEVELS
RESOLUTIONS = "('low', 'high')"  # kaagaz.settings.SENDABLE_MEDIA_RESOLUTIONS["detail"]

# The bodies in force now, each restored exactly by the downgrade.
OLD = {
    ORG_AI: _0026.FUNCTIONS[ORG_AI],
    AI_DEFAULTS: _0026.FUNCTIONS[AI_DEFAULTS],
    SET_ORG_AI_OLD: _0048.UPGRADE[0],
    SET_AI_DEFAULTS_OLD: _0048.UPGRADE[1],
}

# --- the changes, each made exactly once in its body (``_changed``) ---------------------------
# The four fields as parameters, right after the scan level.
_PARAMS = (
    "p_scan_thinking text, p_chat_levels text[]",
    "p_scan_thinking text, p_scan_resolution text, p_careful_thinking text, p_careful_resolution text,"
    "\n        p_page_max_output_tokens integer, p_chat_levels text[]",
)
# Careful's level as it will be in effect, checked as the scan level is.
_DECLARE = ("        v_eff_thinking text;\n", "        v_eff_thinking text;\n        v_eff_careful text;\n")
# A page read's output limit is 1024 to 65536 tokens (``limit_out_of_range``); a scan level may
# be minimal (the answer levels may not); a picture detail is low or high.
_LEVELS = (
    "IF p_scan_thinking NOT IN ('low', 'medium', 'high')",
    f"IF p_page_max_output_tokens NOT BETWEEN {TOKENS_LOW} AND {TOKENS_HIGH} THEN"
    "\n            outcome := 'limit_out_of_range';"
    "\n            RETURN;"
    "\n        END IF;"
    f"\n        IF p_scan_thinking NOT IN {SCAN_LEVELS} OR p_careful_thinking NOT IN {SCAN_LEVELS}"
    f"\n           OR p_scan_resolution NOT IN {RESOLUTIONS} OR p_careful_resolution NOT IN {RESOLUTIONS}",
)
# Every effective scan model must accept Careful's level too (a model with no levels takes any, 0048).
_MODELS = (
    "WHERE m.id = ANY (v_eff_scan) AND cardinality(m.efforts) > 0 AND NOT v_eff_thinking = ANY (m.efforts)) THEN",
    "WHERE m.id = ANY (v_eff_scan) AND cardinality(m.efforts) > 0"
    "\n                        AND (NOT v_eff_thinking = ANY (m.efforts) OR NOT v_eff_careful = ANY (m.efforts)))"
    " THEN",
)
NEW_FIELDS = ("scan_resolution", "careful_thinking", "careful_resolution", "page_max_output_tokens")
# The organisations inheriting each new field (not being deleted).
_INHERITING = (
    "               count(*) FILTER (WHERE s.dynamic IS NULL)\n",
    "               count(*) FILTER (WHERE s.dynamic IS NULL),\n"
    + ",\n".join(f"               count(*) FILTER (WHERE s.{name} IS NULL)" for name in NEW_FIELDS)
    + "\n",
)
_FIELDS = ", ".join(NEW_FIELDS)
_TYPED = (
    "scan_resolution text, careful_thinking text, careful_resolution text, page_max_output_tokens integer"
)

ORG_SET_CHANGES = [
    _PARAMS,
    _DECLARE,
    (
        "        INTO v_eff_scan, v_eff_chat, v_eff_levels, v_eff_thinking;",
        "               COALESCE(p_careful_thinking, (SELECT p.careful_thinking FROM public.platform_ai_settings p))"
        "\n        INTO v_eff_scan, v_eff_chat, v_eff_levels, v_eff_thinking, v_eff_careful;",
    ),
    (
        "(SELECT p.scan_thinking FROM public.platform_ai_settings p))\n",
        "(SELECT p.scan_thinking FROM public.platform_ai_settings p)),\n",
    ),
    _LEVELS,
    _MODELS,
    (
        "(customer_id, scan_thinking, chat_levels,",
        f"(customer_id, scan_thinking, {_FIELDS}, chat_levels,",
    ),
    (
        "VALUES (p_customer, p_scan_thinking, v_levels,",
        "VALUES (p_customer, p_scan_thinking, p_scan_resolution, p_careful_thinking, p_careful_resolution,"
        " p_page_max_output_tokens, v_levels,",
    ),
    (
        "SET scan_thinking = EXCLUDED.scan_thinking, chat_levels",
        "SET scan_thinking = EXCLUDED.scan_thinking, scan_resolution = EXCLUDED.scan_resolution,"
        "\n            careful_thinking = EXCLUDED.careful_thinking, careful_resolution = EXCLUDED.careful_resolution,"
        "\n            page_max_output_tokens = EXCLUDED.page_max_output_tokens, chat_levels",
    ),
    (
        "'scan_thinking', p_scan_thinking,",
        "'scan_thinking', p_scan_thinking, 'scan_resolution', p_scan_resolution,"
        "\n            'careful_thinking', p_careful_thinking, 'careful_resolution', p_careful_resolution,"
        "\n            'page_max_output_tokens', p_page_max_output_tokens,",
    ),
]
DEFAULTS_SET_CHANGES = [
    _PARAMS,
    _DECLARE,
    (
        "        v_dynamic bigint;\n",
        "        v_dynamic bigint;\n" + "".join(f"        v_{name} bigint;\n" for name in NEW_FIELDS),
    ),
    (
        "        v_eff_thinking := p_scan_thinking;\n",
        "        v_eff_thinking := p_scan_thinking;\n        v_eff_careful := p_careful_thinking;\n",
    ),
    _LEVELS,
    _MODELS,
    _INHERITING,
    (
        "INTO v_scan_models, v_chat_models, v_scan_thinking, v_chat_levels, v_dynamic;",
        "INTO v_scan_models, v_chat_models, v_scan_thinking, v_chat_levels, v_dynamic,"
        "\n             v_scan_resolution, v_careful_thinking, v_careful_resolution, v_page_max_output_tokens;",
    ),
    (
        "SET scan_thinking = p_scan_thinking, chat_levels = v_levels,",
        "SET scan_thinking = p_scan_thinking, scan_resolution = p_scan_resolution,"
        "\n            careful_thinking = p_careful_thinking, careful_resolution = p_careful_resolution,"
        "\n            page_max_output_tokens = p_page_max_output_tokens, chat_levels = v_levels,",
    ),
    (
        "'dynamic', CASE WHEN v_before -> 'dynamic' = v_after -> 'dynamic' THEN 0 ELSE v_dynamic END)));",
        "'dynamic', CASE WHEN v_before -> 'dynamic' = v_after -> 'dynamic' THEN 0 ELSE v_dynamic END,"
        + ",".join(
            f"\n                '{name}', CASE WHEN v_before -> '{name}' = v_after -> '{name}'"
            f" THEN 0 ELSE v_{name} END"
            for name in NEW_FIELDS
        )
        + ")));",
    ),
]
# The defaults' before and after (twice in the save) hold the four fields.
_DEFAULTS_JSON = (
    "'chat_default_level', p.chat_default_level, 'dynamic', p.dynamic)",
    "'chat_default_level', p.chat_default_level, 'dynamic', p.dynamic,"
    "\n                   'scan_resolution', p.scan_resolution, 'careful_thinking', p.careful_thinking,"
    "\n                   'careful_resolution', p.careful_resolution,"
    " 'page_max_output_tokens', p.page_max_output_tokens)",
)
ORG_READ_CHANGES = [
    (
        "dynamic boolean,\n                   ensemble boolean",
        f"dynamic boolean,\n                   {_TYPED},\n                   ensemble boolean",
    ),
    (
        "s.dynamic, COALESCE(s.ensemble, false),",
        "s.dynamic, s.scan_resolution, s.careful_thinking, s.careful_resolution, s.page_max_output_tokens,"
        "\n               COALESCE(s.ensemble, false),",
    ),
]
DEFAULTS_READ_CHANGES = [
    (
        "dynamic boolean,\n                   orgs_inheriting_scan_models",
        f"dynamic boolean,\n                   {_TYPED},\n                   orgs_inheriting_scan_models",
    ),
    (
        "orgs_inheriting_dynamic bigint)",
        "orgs_inheriting_dynamic bigint, "
        + ", ".join(f"orgs_inheriting_{name} bigint" for name in NEW_FIELDS)
        + ")",
    ),
    (
        "p.chat_default_level, p.dynamic, n.*",
        "p.chat_default_level, p.dynamic, p.scan_resolution, p.careful_thinking, p.careful_resolution,"
        " p.page_max_output_tokens, n.*",
    ),
    _INHERITING,
]


def _changed(body: str, changes: list[tuple[str, str]], *, times: dict[str, int] | None = None) -> str:
    """``body`` with each (old, new) made, ``old`` there exactly once (``times``: that many)."""
    for old, new in changes:
        expected = (times or {}).get(old, 1)
        if body.count(old) != expected:
            raise RuntimeError(f"a body 0064 changes does not hold {old!r} {expected} time(s)")
        body = body.replace(old, new)
    return body


NEW = {
    ORG_AI: _changed(OLD[ORG_AI], ORG_READ_CHANGES),
    AI_DEFAULTS: _changed(OLD[AI_DEFAULTS], DEFAULTS_READ_CHANGES),
    SET_ORG_AI: _changed(OLD[SET_ORG_AI_OLD], ORG_SET_CHANGES),
    SET_AI_DEFAULTS: _changed(
        OLD[SET_AI_DEFAULTS_OLD], [*DEFAULTS_SET_CHANGES, _DEFAULTS_JSON], times={_DEFAULTS_JSON[0]: 2}
    ),
}

# The measured cost of a page (#463): every organisation's ledger of the last LEDGER_DAYS days,
# never cached, with OpenRouter's own cost only (an estimated or unknown one is never counted);
# the first reads (scan) of p_model at p_thinking and p_resolution, Careful's re-reads (careful)
# of its own three; and the scope's scan and careful calls (every organisation's when
# p_customer is NULL). The quality: the scope's pages p_model read (its stored read) in a
# document read in those days, at the first read's settings and at max ones (p_max_*), and
# the clean ones (good) of each. The first reads' tokens (every organisation's, not cached, any
# cost) and the scope's of them that hit the output limit. shortcut: the pages are read whole (no
# index on the settings; the operator asks rarely). No row for an operator who is not enabled.
SCAN_COST_SQL = f"""
    CREATE FUNCTION public.kaagaz_op_scan_cost(
        p_operator uuid, p_customer uuid, p_model text, p_thinking text, p_resolution text,
        p_careful_model text, p_careful_thinking text, p_careful_resolution text,
        p_max_thinking text, p_max_resolution text
    )
    RETURNS TABLE (first_usd numeric, first_pages bigint, careful_usd numeric, careful_pages bigint,
                   scan_calls bigint, careful_calls bigint, quality_pages bigint, clean_pages bigint,
                   max_quality_pages bigint, max_clean_pages bigint, tokens_in numeric, tokens_out numeric,
                   token_pages bigint, limit_hits bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH u AS (
            SELECT u.kind, u.calls, u.cost_usd, u.input_tokens, u.output_tokens, u.limit_hits,
                   u.kind = 'scan' AND u.model = p_model AND u.thinking = p_thinking
                   AND u.resolution = p_resolution AS first,
                   u.cost_usd IS NOT NULL AND NOT u.cost_estimated
                   AND ((u.kind = 'scan' AND u.model = p_model AND u.thinking = p_thinking
                         AND u.resolution = p_resolution)
                        OR (u.kind = 'careful' AND u.model = p_careful_model AND u.thinking = p_careful_thinking
                            AND u.resolution = p_careful_resolution)) AS measured,
                   (p_customer IS NULL OR u.customer_id = p_customer) AS in_scope
            FROM public.model_usage u
            WHERE u.kind IN ('scan', 'careful') AND NOT u.cached
              AND u.created_at >= now() - make_interval(days => {LEDGER_DAYS})
        ), cost AS (
            SELECT sum(u.cost_usd) FILTER (WHERE u.measured AND u.kind = 'scan') AS first_usd,
                   COALESCE(sum(u.calls) FILTER (WHERE u.measured AND u.kind = 'scan'), 0)::bigint AS first_pages,
                   sum(u.cost_usd) FILTER (WHERE u.measured AND u.kind = 'careful') AS careful_usd,
                   COALESCE(sum(u.calls) FILTER (WHERE u.measured AND u.kind = 'careful'), 0)::bigint
                       AS careful_pages,
                   COALESCE(sum(u.calls) FILTER (WHERE u.in_scope AND u.kind = 'scan'), 0)::bigint AS scan_calls,
                   COALESCE(sum(u.calls) FILTER (WHERE u.in_scope AND u.kind = 'careful'), 0)::bigint
                       AS careful_calls,
                   sum(u.input_tokens) FILTER (WHERE u.first) AS tokens_in,
                   sum(u.output_tokens) FILTER (WHERE u.first) AS tokens_out,
                   COALESCE(sum(u.calls) FILTER (WHERE u.first), 0)::bigint AS token_pages,
                   COALESCE(sum(u.limit_hits) FILTER (WHERE u.first AND u.in_scope), 0)::bigint AS limit_hits
            FROM u
        ), q AS (
            SELECT count(*) FILTER (WHERE p.read_thinking = p_thinking AND p.read_resolution = p_resolution)
                       AS quality_pages,
                   count(*) FILTER (WHERE p.read_thinking = p_thinking AND p.read_resolution = p_resolution
                                    AND p.read_quality = 'good') AS clean_pages,
                   count(*) FILTER (WHERE p.read_thinking = p_max_thinking
                                    AND p.read_resolution = p_max_resolution) AS max_quality_pages,
                   count(*) FILTER (WHERE p.read_thinking = p_max_thinking AND p.read_resolution = p_max_resolution
                                    AND p.read_quality = 'good') AS max_clean_pages
            FROM public.pages p
            WHERE p.read_by = p_model AND (p_customer IS NULL OR p.customer_id = p_customer)
              AND EXISTS (SELECT 1 FROM public.processing_runs r
                          WHERE r.customer_id = p.customer_id AND r.document_id = p.document_id
                            AND r.started_at >= now() - make_interval(days => {LEDGER_DAYS}))
        )
        SELECT cost.first_usd, cost.first_pages, cost.careful_usd, cost.careful_pages, cost.scan_calls,
               cost.careful_calls, q.*, cost.tokens_in, cost.tokens_out, cost.token_pages, cost.limit_hits
        FROM cost CROSS JOIN q
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
"""
SCAN_COST_COMMENT = (
    "Operator: the cost OpenRouter reported for first reads and Careful re-reads at the given"
    " model, thinking level and picture detail over the last 30 days of every organisation's ledger,"
    " the scope's scan and careful calls, and its pages read at these and at max settings and how"
    " many were clean. Numbers only."
)

# --- accuracy against max settings -----------------------------------------------------------
ACCURACY_TABLE = f"""
    CREATE TABLE scan_accuracy_checks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        subject_customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        operator_id uuid NOT NULL REFERENCES operators (id),
        status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
        model text NOT NULL CHECK (length(model) BETWEEN 1 AND 300),
        scan_thinking text NOT NULL CHECK (scan_thinking IN {SCAN_LEVELS} OR scan_thinking = 'none'),
        scan_resolution text NOT NULL CHECK (scan_resolution IN {RESOLUTIONS}),
        careful_thinking text NOT NULL CHECK (careful_thinking IN {SCAN_LEVELS} OR careful_thinking = 'none'),
        careful_resolution text NOT NULL CHECK (careful_resolution IN {RESOLUTIONS}),
        page_max_output_tokens integer NOT NULL CHECK (page_max_output_tokens BETWEEN {TOKENS_LOW} AND {TOKENS_HIGH}),
        max_pages integer NOT NULL CHECK (max_pages IN {CHECK_PAGES}),
        pages_checked integer NOT NULL DEFAULT 0 CHECK (pages_checked >= 0),
        pages_failed integer NOT NULL DEFAULT 0 CHECK (pages_failed >= 0),
        word_match double precision NULL CHECK (word_match BETWEEN 0 AND 1),
        numbers_match double precision NULL CHECK (numbers_match BETWEEN 0 AND 1),
        cost_usd numeric(14,6) NULL CHECK (cost_usd >= 0),
        started_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz NULL,
        CONSTRAINT accuracy_checks_finished CHECK ((status = 'running') = (finished_at IS NULL))
    )
"""
_ACTOR = _0026._ACTOR
_STEP_UP = _0026._STEP_UP
# The check's settings, as the operator's estimate resolved them (every one a value, never NULL).
_SETTINGS_OUT = (
    "model text, scan_thinking text, scan_resolution text, careful_thinking text, careful_resolution text,"
    " page_max_output_tokens integer"
)
CHECK_FUNCTIONS = {
    # Start one organisation's check, with a fresh code: one running at a time per organisation,
    # ACCURACY_CHECKS_PER_HOUR started an hour on the platform, and a page to read. Audited.
    START_CHECK: f"""
    CREATE FUNCTION public.kaagaz_op_start_accuracy_check(
        p_operator uuid, p_customer uuid, p_model text, p_scan_thinking text, p_scan_resolution text,
        p_careful_thinking text, p_careful_resolution text, p_page_max_output_tokens integer, p_pages integer,
        p_step bigint, OUT outcome text, OUT result_id uuid
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
    BEGIN
        {_ACTOR}
        {_STEP_UP}
        SELECT c.status INTO v_status FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        IF EXISTS (SELECT 1 FROM public.scan_accuracy_checks a
                   WHERE a.subject_customer_id = p_customer AND a.status = 'running'
                     AND a.started_at > now() - {CHECK_RUNNING_FOR}) THEN
            outcome := 'check_running';
            RETURN;
        END IF;
        -- shortcut: two organisations' starts at the same instant may both pass this count.
        IF (SELECT count(*) FROM public.scan_accuracy_checks a WHERE a.started_at > now() - interval '1 hour')
           >= {ACCURACY_CHECKS_PER_HOUR} THEN
            outcome := 'rate_limited';
            RETURN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.pages p WHERE p.customer_id = p_customer AND {MODEL_READ}) THEN
            outcome := 'no_pages';
            RETURN;
        END IF;
        INSERT INTO public.scan_accuracy_checks (subject_customer_id, operator_id, model, scan_thinking,
            scan_resolution, careful_thinking, careful_resolution, page_max_output_tokens, max_pages)
        VALUES (p_customer, p_operator, p_model, p_scan_thinking, p_scan_resolution, p_careful_thinking,
                p_careful_resolution, p_page_max_output_tokens, p_pages)
        RETURNING id INTO result_id;
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id,
                                           details)
        VALUES (p_operator, 'org.accuracy_check', p_customer, 'customer', p_customer::text, jsonb_build_object(
            'check_id', result_id, 'model', p_model, 'scan_thinking', p_scan_thinking,
            'scan_resolution', p_scan_resolution, 'careful_thinking', p_careful_thinking,
            'careful_resolution', p_careful_resolution, 'page_max_output_tokens', p_page_max_output_tokens,
            'pages', p_pages));
        outcome := 'done';
    END
    $$
    """,
    # One organisation's latest check (p_model NULL), else its latest done check at exactly these
    # settings. No row: none, or the operator is not enabled.
    LATEST_CHECK: f"""
    CREATE FUNCTION public.kaagaz_op_accuracy_check(
        p_operator uuid, p_customer uuid, p_model text, p_scan_thinking text, p_scan_resolution text,
        p_careful_thinking text, p_careful_resolution text, p_page_max_output_tokens integer
    )
    RETURNS TABLE (id uuid, status text, {_SETTINGS_OUT}, max_pages integer, pages_checked integer,
                   pages_failed integer, word_match double precision, numbers_match double precision,
                   cost_usd numeric, started_at timestamptz, finished_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT a.id, a.status, a.model, a.scan_thinking, a.scan_resolution, a.careful_thinking,
               a.careful_resolution, a.page_max_output_tokens, a.max_pages, a.pages_checked, a.pages_failed, a.word_match,
               a.numbers_match, a.cost_usd, a.started_at, a.finished_at
        FROM public.scan_accuracy_checks a
        WHERE a.subject_customer_id = p_customer AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
          AND (p_model IS NULL
               OR (a.status = 'done' AND a.model = p_model AND a.scan_thinking = p_scan_thinking
                   AND a.scan_resolution = p_scan_resolution AND a.careful_thinking = p_careful_thinking
                   AND a.careful_resolution = p_careful_resolution
                   AND a.page_max_output_tokens = p_page_max_output_tokens))
        ORDER BY a.started_at DESC, a.id
        LIMIT 1
    $$
    """,
}
APP_CHECK_FUNCTIONS = {
    # The worker's: the current organisation's running check (its settings and sample size).
    CLAIM_CHECK: f"""
    CREATE FUNCTION public.kaagaz_accuracy_check_claim(p_check uuid)
    RETURNS TABLE ({_SETTINGS_OUT}, max_pages integer)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT a.model, a.scan_thinking, a.scan_resolution, a.careful_thinking, a.careful_resolution,
               a.page_max_output_tokens, a.max_pages
        FROM public.scan_accuracy_checks a
        WHERE a.id = p_check AND a.subject_customer_id = public.kaagaz_current_customer()
          AND a.status = 'running'
    $$
    """,
    # The worker's: end the current organisation's running check with its numbers (no text).
    FINISH_CHECK: """
    CREATE FUNCTION public.kaagaz_accuracy_check_finish(
        p_check uuid, p_status text, p_checked integer, p_failed integer, p_word double precision,
        p_numbers double precision, p_cost numeric
    ) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
        WITH done AS (
            UPDATE public.scan_accuracy_checks AS a
            SET status = p_status, pages_checked = p_checked, pages_failed = p_failed, word_match = p_word,
                numbers_match = p_numbers, cost_usd = p_cost, finished_at = now()
            WHERE a.id = p_check AND a.subject_customer_id = public.kaagaz_current_customer()
              AND a.status = 'running' AND p_status IN ('done', 'failed')
            RETURNING 1
        )
        SELECT EXISTS (SELECT 1 FROM done)
    $$
    """,
}
CHECK_COMMENTS = {
    START_CHECK: "Operator, with a fresh code: start one organisation's accuracy check against max"
    " settings (one running at a time, a few an hour on the platform, a page read by a model needed)."
    " Audited.",
    LATEST_CHECK: "Operator: one organisation's latest accuracy check, or its latest done one at the"
    " given settings. Numbers only, never text.",
    CLAIM_CHECK: "Worker: the current organisation's running accuracy check, its settings and sample size.",
    FINISH_CHECK: "Worker: end the current organisation's running accuracy check with its numbers."
    " True when it was running.",
}
# A month's spend against the budget (0024) leaves out the operator's accuracy checks.
HEALTH_OLD = "WHERE u.customer_id = c.id AND u.created_at < v_hi"
HEALTH_NEW = "WHERE u.customer_id = c.id AND u.kind <> 'accuracy_check' AND u.created_at < v_hi"


def _load_0061() -> ModuleType:
    path = Path(__file__).with_name("0061_email.py")
    spec = importlib.util.spec_from_file_location("migration_0061_for_0064", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REWRITE = _load_0061()._REWRITE

# {table.column: comment} (0007's rule: every column has one).
COMMENTS = {
    **{
        f"{table}.scan_resolution": "The picture detail scans are read at, low or high. NULL, " + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", "OCR_MEDIA_RESOLUTION."),
        )
    },
    **{
        f"{table}.careful_thinking": "The thinking level pages read again (Careful, Read this page again)"
        " are read at, minimal, low, medium or high. NULL, " + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", "OCR_RETRY_THINKING_LEVEL."),
        )
    },
    **{
        f"{table}.careful_resolution": "The picture detail pages read again are read at, low or high."
        " NULL, " + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", "OCR_RETRY_MEDIA_RESOLUTION."),
        )
    },
    "model_usage.thinking": "The thinking level a page read was sent at (none for a model with no"
    " levels). NULL for every kind but scan, careful, upgrade and ensemble_scan, and for older rows.",
    "model_usage.resolution": "The picture detail a page read was sent at, low or high. NULL for every"
    " kind but scan, careful, upgrade and ensemble_scan, and for older rows.",
    **{
        f"{table}.page_max_output_tokens": "The most output tokens a page read may take, 1024 to 65536;"
        " a reply that reaches it is read again in halves. NULL, " + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", "65536 (PAGE_MAX_OUTPUT_TOKENS)."),
        )
    },
    "model_usage.limit_hits": "The calls whose reply hit the page read's output limit (read again in"
    " halves). 0 for every other call and older rows.",
    "pages.read_thinking": "The thinking level of the read that produced the page's text. NULL for"
    " older pages and pages no model read.",
    "pages.read_resolution": "The picture detail of the read that produced the page's text. NULL for"
    " older pages and pages no model read.",
}
TABLE_COMMENT = (
    "Operator only: one organisation's scan accuracy check against max settings, its settings and"
    " numbers (the share of words and numbers that matched), never text."
)
ACCURACY_COMMENTS = {
    "id": "The check.",
    "subject_customer_id": "The organisation whose pages were read.",
    "operator_id": "The operator who started it.",
    "status": "running, done or failed.",
    "model": "The first read's model.",
    "scan_thinking": "The first read's thinking level, as asked (before Dynamic's rule).",
    "scan_resolution": "The first read's picture detail.",
    "careful_thinking": "Careful's thinking level.",
    "careful_resolution": "Careful's picture detail.",
    "page_max_output_tokens": "The draft reads' output limit (the max reads take 65536).",
    "max_pages": "The most pages it reads, 20, 50 or 100.",
    "pages_checked": "Pages read at both settings and compared.",
    "pages_failed": "Pages skipped: a read failed or the picture is gone.",
    "word_match": "1 - the word error rate against the max read, every page's words together. NULL, none.",
    "numbers_match": "The share of the max read's numbers the check's read has too. NULL, no number.",
    "cost_usd": "What its model calls cost, OpenRouter's reported cost. NULL when one was not known.",
    "started_at": "When it was started.",
    "finished_at": "When it ended. NULL while running.",
}


def _settings_columns(table: str, prefix: str, old_check: str) -> str:
    """``table``'s scan level CHECK widened to minimal (the old one is ``old_check``) and the
    four new columns, in one ALTER (one lock, one read of a small table)."""
    return f"""
    ALTER TABLE {table}
        DROP CONSTRAINT {old_check},
        ADD CONSTRAINT {prefix}_scan_thinking CHECK (scan_thinking IN {SCAN_LEVELS}),
        ADD COLUMN scan_resolution text NULL CONSTRAINT {prefix}_scan_resolution
            CHECK (scan_resolution IN {RESOLUTIONS}),
        ADD COLUMN careful_thinking text NULL CONSTRAINT {prefix}_careful_thinking
            CHECK (careful_thinking IN {SCAN_LEVELS}),
        ADD COLUMN careful_resolution text NULL CONSTRAINT {prefix}_careful_resolution
            CHECK (careful_resolution IN {RESOLUTIONS}),
        ADD COLUMN page_max_output_tokens integer NULL CONSTRAINT {prefix}_page_max_output_tokens
            CHECK (page_max_output_tokens BETWEEN {TOKENS_LOW} AND {TOKENS_HIGH})
    """


def _settings_columns_back(table: str, prefix: str, old_check: str) -> str:
    """The downgrade of ``_settings_columns``: the old CHECK back under its old name."""
    return f"""
    ALTER TABLE {table}
        DROP COLUMN page_max_output_tokens,
        DROP COLUMN careful_resolution,
        DROP COLUMN careful_thinking,
        DROP COLUMN scan_resolution,
        DROP CONSTRAINT {prefix}_scan_thinking,
        ADD CONSTRAINT {old_check} CHECK (scan_thinking IN ('low', 'medium', 'high'))
    """


def _create(signature: str, create: str, comment: str, role: str = OPERATOR_ROLE) -> list[str]:
    """``create``, its comment, and EXECUTE for ``role`` alone (0026's grants)."""
    return [
        create,
        f"COMMENT ON FUNCTION public.{signature} IS {_literal(comment)}",
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {role}",
    ]


def _comment(signature: str) -> str:
    return str(_0026.FUNCTION_COMMENTS[signature.split("(")[0]])


# The inline CHECKs 0026 wrote, under the names PostgreSQL gave them.
CUSTOMER_CHECK = "customer_ai_settings_scan_thinking_check"
PLATFORM_CHECK = "platform_ai_settings_scan_thinking_check"

UPGRADE = [
    # The four functions with their old parameters and columns go first: nothing calls them now.
    *[f"DROP FUNCTION public.{signature}" for signature in OLD],
    # An organisation's own picture detail and Careful pair, and a minimal scan level.
    _settings_columns("customer_ai_settings", "ai_customer_settings", CUSTOMER_CHECK),
    # The platform defaults' (one row; NULL, the server's settings).
    _settings_columns("platform_ai_settings", "ai_platform_settings", PLATFORM_CHECK),
    # The ledger: what each page read was sent (no default, so no table rewrite).
    "ALTER TABLE model_usage ADD COLUMN thinking text NULL, ADD COLUMN resolution text NULL,"
    " ADD COLUMN limit_hits integer NOT NULL DEFAULT 0 CONSTRAINT model_usage_limit_hits CHECK (limit_hits >= 0)",
    # The four functions again, with the three fields; the same comments and grants.
    *[
        statement
        for signature, body in NEW.items()
        for statement in _create(signature, body, _comment(signature))
    ],
    # The settings of the read that produced each page's text (the pipeline writes them).
    "ALTER TABLE pages ADD COLUMN read_thinking text NULL, ADD COLUMN read_resolution text NULL",
    # Every new column's comment.
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    # The estimate's measured cost and quality (new).
    *_create(SCAN_COST, SCAN_COST_SQL, SCAN_COST_COMMENT),
    # The accuracy checks: an operator table (row level security on, no policy, no grant).
    ACCURACY_TABLE,
    "CREATE INDEX scan_accuracy_checks_latest ON scan_accuracy_checks (subject_customer_id, started_at)",
    "ALTER TABLE scan_accuracy_checks ENABLE ROW LEVEL SECURITY",
    f"COMMENT ON TABLE scan_accuracy_checks IS {_literal(TABLE_COMMENT)}",
    *[
        f"COMMENT ON COLUMN scan_accuracy_checks.{column} IS {_literal(comment)}"
        for column, comment in ACCURACY_COMMENTS.items()
    ],
    "COMMENT ON INDEX scan_accuracy_checks_latest IS 'An organisation''s checks, newest last.'",
    # The operator starts and reads checks; the worker claims and ends its organisation's own.
    *[
        statement
        for signature, body in CHECK_FUNCTIONS.items()
        for statement in _create(signature, body, CHECK_COMMENTS[signature])
    ],
    *[
        statement
        for signature, body in APP_CHECK_FUNCTIONS.items()
        for statement in _create(signature, body, CHECK_COMMENTS[signature], APP_ROLE)
    ],
    # The budget's month spend leaves the checks out.
    _REWRITE.format(function=ORG_HEALTH, old=HEALTH_OLD, new=HEALTH_NEW),
]

DOWNGRADE = [
    # The budget's month spend as it was; the checks, their functions and table go.
    _REWRITE.format(function=ORG_HEALTH, old=HEALTH_NEW, new=HEALTH_OLD),
    *[f"DROP FUNCTION public.{signature}" for signature in (*APP_CHECK_FUNCTIONS, *CHECK_FUNCTIONS)],
    "DROP TABLE scan_accuracy_checks",
    # The pages' two columns go.
    "ALTER TABLE pages DROP COLUMN read_resolution, DROP COLUMN read_thinking",
    # The estimate's function and the four new bodies go.
    f"DROP FUNCTION public.{SCAN_COST}",
    *[f"DROP FUNCTION public.{signature}" for signature in NEW],
    # A minimal scan level inherits again (the old CHECK refuses it).
    "UPDATE customer_ai_settings SET scan_thinking = NULL WHERE scan_thinking = 'minimal'",
    "UPDATE platform_ai_settings SET scan_thinking = NULL WHERE scan_thinking = 'minimal'",
    # The settings' new columns go and the old CHECKs come back.
    _settings_columns_back("customer_ai_settings", "ai_customer_settings", CUSTOMER_CHECK),
    _settings_columns_back("platform_ai_settings", "ai_platform_settings", PLATFORM_CHECK),
    # The ledger's two columns go.
    "ALTER TABLE model_usage DROP COLUMN limit_hits, DROP COLUMN resolution, DROP COLUMN thinking",
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
