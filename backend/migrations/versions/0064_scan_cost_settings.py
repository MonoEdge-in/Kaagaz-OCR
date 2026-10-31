"""The super admin sets the scan cost settings and sees the approximate cost a page (#463,
Tushar, 31 Oct 2026).

* ``platform_ai_settings`` and ``customer_ai_settings``: ``scan_resolution``,
  ``careful_thinking`` and ``careful_resolution``, nullable text with CHECKs. NULL inherits
  field by field: an organisation's the platform's, the platform's the server's
  (OCR_MEDIA_RESOLUTION, OCR_RETRY_THINKING_LEVEL, OCR_RETRY_MEDIA_RESOLUTION), as
  ``scan_thinking`` does, whose CHECK now takes ``minimal`` too (the answer levels do not).
  Every new column is NULL on release day, so every value is still the server's. The app
  keeps SELECT only on both tables (0026): only the operator's functions below write them.
* ``model_usage``: ``thinking`` and ``resolution``, what a page read was sent (``scan``,
  ``careful``, ``upgrade``, ``ensemble_scan``); NULL for every other kind and for old rows.
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
  calls (the share of pages read again). Numbers only.

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
# The new saves take scan_resolution, careful_thinking and careful_resolution after scan_thinking.
SET_ORG_AI = (
    "kaagaz_op_set_org_ai(uuid, uuid, text[], text, text[], text, text, text, text, text, text[], text,"
    " boolean, boolean, numeric, text, text, bigint)"
)
SET_AI_DEFAULTS = (
    "kaagaz_op_set_ai_defaults(uuid, text[], text, text[], text, text, text, text, text, text[], text,"
    " boolean, text, text, bigint)"
)
SCAN_COST = "kaagaz_op_scan_cost(uuid, uuid, text, text, text, text, text, text)"

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
# The three fields as parameters, right after the scan level.
_PARAMS = (
    "p_scan_thinking text, p_chat_levels text[]",
    "p_scan_thinking text, p_scan_resolution text, p_careful_thinking text, p_careful_resolution text,"
    "\n        p_chat_levels text[]",
)
# Careful's level as it will be in effect, checked as the scan level is.
_DECLARE = ("        v_eff_thinking text;\n", "        v_eff_thinking text;\n        v_eff_careful text;\n")
# A scan level may be minimal (the answer levels may not); a picture detail is low or high.
_LEVELS = (
    "IF p_scan_thinking NOT IN ('low', 'medium', 'high')",
    f"IF p_scan_thinking NOT IN {SCAN_LEVELS} OR p_careful_thinking NOT IN {SCAN_LEVELS}"
    f"\n           OR p_scan_resolution NOT IN {RESOLUTIONS} OR p_careful_resolution NOT IN {RESOLUTIONS}",
)
# Every effective scan model must accept Careful's level too (a model with no levels takes any, 0048).
_MODELS = (
    "WHERE m.id = ANY (v_eff_scan) AND cardinality(m.efforts) > 0 AND NOT v_eff_thinking = ANY (m.efforts)) THEN",
    "WHERE m.id = ANY (v_eff_scan) AND cardinality(m.efforts) > 0"
    "\n                        AND (NOT v_eff_thinking = ANY (m.efforts) OR NOT v_eff_careful = ANY (m.efforts)))"
    " THEN",
)
# The organisations inheriting each new field (not being deleted).
_INHERITING = (
    "               count(*) FILTER (WHERE s.dynamic IS NULL)\n",
    "               count(*) FILTER (WHERE s.dynamic IS NULL),\n"
    "               count(*) FILTER (WHERE s.scan_resolution IS NULL),\n"
    "               count(*) FILTER (WHERE s.careful_thinking IS NULL),\n"
    "               count(*) FILTER (WHERE s.careful_resolution IS NULL)\n",
)
_FIELDS = "scan_resolution, careful_thinking, careful_resolution"

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
        " v_levels,",
    ),
    (
        "SET scan_thinking = EXCLUDED.scan_thinking, chat_levels",
        "SET scan_thinking = EXCLUDED.scan_thinking, scan_resolution = EXCLUDED.scan_resolution,"
        "\n            careful_thinking = EXCLUDED.careful_thinking, careful_resolution = EXCLUDED.careful_resolution,"
        "\n            chat_levels",
    ),
    (
        "'scan_thinking', p_scan_thinking,",
        "'scan_thinking', p_scan_thinking, 'scan_resolution', p_scan_resolution,"
        "\n            'careful_thinking', p_careful_thinking, 'careful_resolution', p_careful_resolution,",
    ),
]
DEFAULTS_SET_CHANGES = [
    _PARAMS,
    _DECLARE,
    (
        "        v_dynamic bigint;\n",
        "        v_dynamic bigint;\n        v_scan_resolution bigint;\n        v_careful_thinking bigint;\n"
        "        v_careful_resolution bigint;\n",
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
        "\n             v_scan_resolution, v_careful_thinking, v_careful_resolution;",
    ),
    (
        "SET scan_thinking = p_scan_thinking, chat_levels = v_levels,",
        "SET scan_thinking = p_scan_thinking, scan_resolution = p_scan_resolution,"
        "\n            careful_thinking = p_careful_thinking, careful_resolution = p_careful_resolution,"
        " chat_levels = v_levels,",
    ),
    (
        "'dynamic', CASE WHEN v_before -> 'dynamic' = v_after -> 'dynamic' THEN 0 ELSE v_dynamic END)));",
        "'dynamic', CASE WHEN v_before -> 'dynamic' = v_after -> 'dynamic' THEN 0 ELSE v_dynamic END,"
        + "".join(
            f"\n                '{name}', CASE WHEN v_before -> '{name}' = v_after -> '{name}'"
            f" THEN 0 ELSE v_{name} END" + ("," if name != "careful_resolution" else ")));")
            for name in ("scan_resolution", "careful_thinking", "careful_resolution")
        ),
    ),
]
# The defaults' before and after (twice in the save) hold the three fields.
_DEFAULTS_JSON = (
    "'chat_default_level', p.chat_default_level, 'dynamic', p.dynamic)",
    "'chat_default_level', p.chat_default_level, 'dynamic', p.dynamic,"
    "\n                   'scan_resolution', p.scan_resolution, 'careful_thinking', p.careful_thinking,"
    "\n                   'careful_resolution', p.careful_resolution)",
)
ORG_READ_CHANGES = [
    (
        "dynamic boolean,\n                   ensemble boolean",
        f"dynamic boolean,\n                   {_FIELDS.replace(',', ' text,')} text,\n                   ensemble boolean",
    ),
    (
        "s.dynamic, COALESCE(s.ensemble, false),",
        "s.dynamic, s.scan_resolution, s.careful_thinking, s.careful_resolution,\n               COALESCE(s.ensemble, false),",
    ),
]
DEFAULTS_READ_CHANGES = [
    (
        "dynamic boolean,\n                   orgs_inheriting_scan_models",
        f"dynamic boolean,\n                   {_FIELDS.replace(',', ' text,')} text,"
        "\n                   orgs_inheriting_scan_models",
    ),
    (
        "orgs_inheriting_dynamic bigint)",
        "orgs_inheriting_dynamic bigint, orgs_inheriting_scan_resolution bigint,"
        "\n                   orgs_inheriting_careful_thinking bigint, orgs_inheriting_careful_resolution bigint)",
    ),
    (
        "p.chat_default_level, p.dynamic, n.*",
        "p.chat_default_level, p.dynamic, p.scan_resolution, p.careful_thinking, p.careful_resolution, n.*",
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
# p_customer is NULL). No row for an operator who is not enabled (HAVING on the one group).
SCAN_COST_SQL = f"""
    CREATE FUNCTION public.kaagaz_op_scan_cost(
        p_operator uuid, p_customer uuid, p_model text, p_thinking text, p_resolution text,
        p_careful_model text, p_careful_thinking text, p_careful_resolution text
    )
    RETURNS TABLE (first_usd numeric, first_pages bigint, careful_usd numeric, careful_pages bigint,
                   scan_calls bigint, careful_calls bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH u AS (
            SELECT u.kind, u.calls, u.cost_usd,
                   u.cost_usd IS NOT NULL AND NOT u.cost_estimated
                   AND ((u.kind = 'scan' AND u.model = p_model AND u.thinking = p_thinking
                         AND u.resolution = p_resolution)
                        OR (u.kind = 'careful' AND u.model = p_careful_model AND u.thinking = p_careful_thinking
                            AND u.resolution = p_careful_resolution)) AS measured,
                   (p_customer IS NULL OR u.customer_id = p_customer) AS in_scope
            FROM public.model_usage u
            WHERE u.kind IN ('scan', 'careful') AND NOT u.cached
              AND u.created_at >= now() - make_interval(days => {LEDGER_DAYS})
        )
        SELECT sum(u.cost_usd) FILTER (WHERE u.measured AND u.kind = 'scan'),
               COALESCE(sum(u.calls) FILTER (WHERE u.measured AND u.kind = 'scan'), 0)::bigint,
               sum(u.cost_usd) FILTER (WHERE u.measured AND u.kind = 'careful'),
               COALESCE(sum(u.calls) FILTER (WHERE u.measured AND u.kind = 'careful'), 0)::bigint,
               COALESCE(sum(u.calls) FILTER (WHERE u.in_scope AND u.kind = 'scan'), 0)::bigint,
               COALESCE(sum(u.calls) FILTER (WHERE u.in_scope AND u.kind = 'careful'), 0)::bigint
        FROM u
        HAVING EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
"""
SCAN_COST_COMMENT = (
    "Operator: the cost OpenRouter reported for first reads and Careful re-reads at the given"
    " model, thinking level and picture detail over the last 30 days of every organisation's ledger,"
    " and the scope's scan and careful calls. Numbers only."
)

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
}


def _settings_columns(table: str, prefix: str, old_check: str) -> str:
    """``table``'s scan level CHECK widened to minimal (the old one is ``old_check``) and the
    three new columns, in one ALTER (one lock, one read of a small table)."""
    return f"""
    ALTER TABLE {table}
        DROP CONSTRAINT {old_check},
        ADD CONSTRAINT {prefix}_scan_thinking CHECK (scan_thinking IN {SCAN_LEVELS}),
        ADD COLUMN scan_resolution text NULL CONSTRAINT {prefix}_scan_resolution
            CHECK (scan_resolution IN {RESOLUTIONS}),
        ADD COLUMN careful_thinking text NULL CONSTRAINT {prefix}_careful_thinking
            CHECK (careful_thinking IN {SCAN_LEVELS}),
        ADD COLUMN careful_resolution text NULL CONSTRAINT {prefix}_careful_resolution
            CHECK (careful_resolution IN {RESOLUTIONS})
    """


def _settings_columns_back(table: str, prefix: str, old_check: str) -> str:
    """The downgrade of ``_settings_columns``: the old CHECK back under its old name."""
    return f"""
    ALTER TABLE {table}
        DROP COLUMN careful_resolution,
        DROP COLUMN careful_thinking,
        DROP COLUMN scan_resolution,
        DROP CONSTRAINT {prefix}_scan_thinking,
        ADD CONSTRAINT {old_check} CHECK (scan_thinking IN ('low', 'medium', 'high'))
    """


def _create(signature: str, create: str, comment: str) -> list[str]:
    """``create``, its comment, and EXECUTE for the operator role alone (0026's grants)."""
    return [
        create,
        f"COMMENT ON FUNCTION public.{signature} IS {_literal(comment)}",
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
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
    "ALTER TABLE model_usage ADD COLUMN thinking text NULL, ADD COLUMN resolution text NULL",
    # Every new column's comment.
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    # The four functions again, with the three fields; the same comments and grants.
    *[
        statement
        for signature, body in NEW.items()
        for statement in _create(signature, body, _comment(signature))
    ],
    # The estimate's measured cost (new).
    *_create(SCAN_COST, SCAN_COST_SQL, SCAN_COST_COMMENT),
]

DOWNGRADE = [
    # The new function and the four new bodies go first.
    f"DROP FUNCTION public.{SCAN_COST}",
    *[f"DROP FUNCTION public.{signature}" for signature in NEW],
    # A minimal scan level inherits again (the old CHECK refuses it).
    "UPDATE customer_ai_settings SET scan_thinking = NULL WHERE scan_thinking = 'minimal'",
    "UPDATE platform_ai_settings SET scan_thinking = NULL WHERE scan_thinking = 'minimal'",
    # The settings' new columns go and the old CHECKs come back.
    _settings_columns_back("customer_ai_settings", "ai_customer_settings", CUSTOMER_CHECK),
    _settings_columns_back("platform_ai_settings", "ai_platform_settings", PLATFORM_CHECK),
    # The ledger's two columns go.
    "ALTER TABLE model_usage DROP COLUMN resolution, DROP COLUMN thinking",
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
