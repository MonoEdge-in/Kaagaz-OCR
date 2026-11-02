"""Who reads scanned pages, per organisation and as the platform default (#473, Tushar,
2 Nov 2026): Gemini through OpenRouter, or Mistral OCR, and which Mistral model.

* ``platform_ai_settings`` and ``customer_ai_settings``: ``scan_reader`` (openrouter or
  mistral) and ``mistral_model`` (kaagaz.reading.mistral_ocr.MISTRAL_OCR_MODELS' ids), nullable
  text with named CHECKs. NULL inherits field by field as the scan cost settings do (0064): an
  organisation's the platform's, the platform's the code's (openrouter, mistral-ocr-4-1).
  Every new column is NULL on release day, so every page is still read as today. The app keeps
  SELECT only on both tables (0026): only the operator's functions below write them.
* The operator's four AI functions (0064's) read, save and audit both, and the defaults count
  the organisations inheriting each. They take both as new trailing parameters, so each is
  dropped and created again from 0064's text with each change made exactly once, with the
  same comment, grants, SECURITY DEFINER and empty search path. Org admins never see or set
  the reader in their settings or their audit log (the organisation's own audit row never
  holds them, as the scan cost settings); a page's read_by is shown to them as it was for the
  Gemini models. The operator's platform audit holds them, before and after for the defaults.
  Whether Mistral is set up on the server (its key and zero data retention) is the API's
  check (409 ``mistral_not_set_up``): the database does not know the server's settings.
* ``kaagaz_op_scan_cost`` (0064): the scope's scan calls count the priced model's alone, so a
  Mistral organisation's first reads never lower a Gemini model's share read again.
* ``scan_accuracy_checks.scan_resolution`` (0064) also takes ``page``: a check on Mistral is
  kept at Mistral's one setting (none, page; review, 2 Nov 2026).
* ``kaagaz_op_mistral_status`` (new): for the console's banner, the documents whose newest
  finished run, in the last STATUS_HOURS hours, waited on Mistral (error code
  ``mistral_key_unavailable``, ``mistral_setup_unavailable`` or ``mistral_unavailable``), the
  newest such code and when the first of them waited. Numbers and codes only, every
  organisation's together; no worker memory. ``processing_runs_mistral_waits`` keeps it to the
  few runs that waited on Mistral.

The downgrade drops the new function and index, puts 0064's scan cost function and four
functions back exactly, deletes the Mistral accuracy checks and puts 0064's resolution CHECK
back, then drops the columns (their CHECKs go with them). Lost on downgrade: the two settings
(every organisation is read by Gemini again) and the checks on Mistral.

Revision ID: 0065
Revises: 0064 (scan cost settings)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def _load_0064() -> ModuleType:
    path = Path(__file__).with_name("0064_scan_cost_settings.py")
    spec = importlib.util.spec_from_file_location("migration_0064_for_0065", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0064 = _load_0064()
_changed = _0064._changed
_create = _0064._create
_comment = _0064._comment
_literal = _0064._literal
_ENABLED = _0064._ENABLED

ORG_AI = _0064.ORG_AI
AI_DEFAULTS = _0064.AI_DEFAULTS
SET_ORG_AI_OLD = _0064.SET_ORG_AI
SET_AI_DEFAULTS_OLD = _0064.SET_AI_DEFAULTS
# The saves take the reader and the Mistral model after the code's step.
SET_ORG_AI = SET_ORG_AI_OLD.removesuffix(")") + ", text, text)"
SET_AI_DEFAULTS = SET_AI_DEFAULTS_OLD.removesuffix(")") + ", text, text)"
SCAN_COST = _0064.SCAN_COST
MISTRAL_STATUS = "kaagaz_op_mistral_status(uuid)"

READERS = "('openrouter', 'mistral')"  # kaagaz.reading.mistral_ocr.OPENROUTER, MISTRAL
MISTRAL_MODELS = "('mistral-ocr-4-1', 'mistral-ocr-2512')"  # mistral_ocr.MISTRAL_OCR_MODELS' ids
# The run error codes of a document waiting on Mistral (pipeline._unavailable_code of
# BackendUnavailable("mistral_key"), ("mistral_setup") and ("mistral")).
MISTRAL_WAITS = "('mistral_key_unavailable', 'mistral_setup_unavailable', 'mistral_unavailable')"
# A check's picture detail (0064's inline CHECK, by Postgres's own name): Mistral's "page" too.
CHECK_RESOLUTION = "scan_accuracy_checks_scan_resolution_check"
_RESOLUTIONS_0064 = f"scan_resolution IN {_0064.RESOLUTIONS}"
STATUS_HOURS = 24  # an older wait is not shown: Mistral has been stopped for a day, or not at all

NEW_FIELDS = ("scan_reader", "mistral_model")
_FIELDS = ", ".join(NEW_FIELDS)
_TYPED = "scan_reader text, mistral_model text"

# --- the changes, each made exactly once in its body (``_changed``) ---------------------------
_PARAMS = (
    "p_step bigint, OUT outcome text",
    "p_step bigint, p_scan_reader text, p_mistral_model text, OUT outcome text",
)
# The organisations inheriting each new field (not being deleted).
_INHERITING = (
    "               count(*) FILTER (WHERE s.page_max_output_tokens IS NULL)\n",
    "               count(*) FILTER (WHERE s.page_max_output_tokens IS NULL),\n"
    + ",\n".join(f"               count(*) FILTER (WHERE s.{name} IS NULL)" for name in NEW_FIELDS)
    + "\n",
)
ORG_SET_CHANGES = [
    _PARAMS,
    ("(customer_id, scan_thinking,", f"(customer_id, {_FIELDS}, scan_thinking,"),
    (
        "VALUES (p_customer, p_scan_thinking,",
        "VALUES (p_customer, p_scan_reader, p_mistral_model, p_scan_thinking,",
    ),
    (
        "SET scan_thinking = EXCLUDED.scan_thinking,",
        "SET scan_reader = EXCLUDED.scan_reader, mistral_model = EXCLUDED.mistral_model,"
        "\n            scan_thinking = EXCLUDED.scan_thinking,",
    ),
    (
        "'scan_thinking', p_scan_thinking,",
        "'scan_reader', p_scan_reader, 'mistral_model', p_mistral_model,\n            'scan_thinking', p_scan_thinking,",
    ),
    # Never in the organisation's own audit row (org admins never see them).
    ("'page_max_output_tokens']);", "'page_max_output_tokens', 'scan_reader', 'mistral_model']);"),
]
DEFAULTS_SET_CHANGES = [
    _PARAMS,
    (
        "        v_page_max_output_tokens bigint;\n",
        "        v_page_max_output_tokens bigint;\n"
        + "".join(f"        v_{name} bigint;\n" for name in NEW_FIELDS),
    ),
    _INHERITING,
    (
        "v_careful_resolution, v_page_max_output_tokens;",
        "v_careful_resolution, v_page_max_output_tokens, v_scan_reader, v_mistral_model;",
    ),
    (
        "SET scan_thinking = p_scan_thinking,",
        "SET scan_reader = p_scan_reader, mistral_model = p_mistral_model, scan_thinking = p_scan_thinking,",
    ),
    (
        "THEN 0 ELSE v_page_max_output_tokens END)));",
        "THEN 0 ELSE v_page_max_output_tokens END,"
        + ",".join(
            f"\n                '{name}', CASE WHEN v_before -> '{name}' = v_after -> '{name}'"
            f" THEN 0 ELSE v_{name} END"
            for name in NEW_FIELDS
        )
        + ")));",
    ),
]
# The defaults' before and after (twice in the save) hold both.
_DEFAULTS_JSON = (
    "'page_max_output_tokens', p.page_max_output_tokens)",
    "'page_max_output_tokens', p.page_max_output_tokens,"
    "\n                   'scan_reader', p.scan_reader, 'mistral_model', p.mistral_model)",
)
ORG_READ_CHANGES = [
    (
        "page_max_output_tokens integer,\n                   ensemble boolean",
        f"page_max_output_tokens integer,\n                   {_TYPED},\n                   ensemble boolean",
    ),
    ("s.page_max_output_tokens,\n", "s.page_max_output_tokens, s.scan_reader, s.mistral_model,\n"),
]
DEFAULTS_READ_CHANGES = [
    (
        "page_max_output_tokens integer,\n                   orgs_inheriting_scan_models",
        f"page_max_output_tokens integer,\n                   {_TYPED},\n                   orgs_inheriting_scan_models",
    ),
    (
        "orgs_inheriting_page_max_output_tokens bigint)",
        "orgs_inheriting_page_max_output_tokens bigint, "
        + ", ".join(f"orgs_inheriting_{name} bigint" for name in NEW_FIELDS)
        + ")",
    ),
    ("p.page_max_output_tokens, n.*", "p.page_max_output_tokens, p.scan_reader, p.mistral_model, n.*"),
    _INHERITING,
]

# The bodies in force now (0064's), each restored exactly by the downgrade.
OLD = {
    ORG_AI: _0064.NEW[ORG_AI],
    AI_DEFAULTS: _0064.NEW[AI_DEFAULTS],
    SET_ORG_AI_OLD: _0064.NEW[SET_ORG_AI_OLD],
    SET_AI_DEFAULTS_OLD: _0064.NEW[SET_AI_DEFAULTS_OLD],
}
NEW = {
    ORG_AI: _changed(OLD[ORG_AI], ORG_READ_CHANGES),
    AI_DEFAULTS: _changed(OLD[AI_DEFAULTS], DEFAULTS_READ_CHANGES),
    SET_ORG_AI: _changed(OLD[SET_ORG_AI_OLD], ORG_SET_CHANGES),
    SET_AI_DEFAULTS: _changed(
        OLD[SET_AI_DEFAULTS_OLD], [*DEFAULTS_SET_CHANGES, _DEFAULTS_JSON], times={_DEFAULTS_JSON[0]: 2}
    ),
}

# The estimate's scan calls: the priced model's first reads only (Mistral's never count).
_SCAN_CALLS = (
    "FILTER (WHERE u.in_scope AND u.kind = 'scan'), 0)::bigint AS scan_calls",
    "FILTER (WHERE u.in_scope AND u.kind = 'scan' AND u.model = p_model), 0)::bigint\n"
    "                       AS scan_calls",
)
SCAN_COST_SQL = _changed(
    _0064.SCAN_COST_SQL, [("SELECT u.kind, u.calls,", "SELECT u.kind, u.model, u.calls,"), _SCAN_CALLS]
)
SCAN_COST_COMMENT = _changed(
    _0064.SCAN_COST_COMMENT,
    [("the scope's scan and careful calls", "the scope's scan calls of the model, careful calls")],
)

# Mistral stopped (#473): the documents still waiting on it, every organisation's. A document
# waits while no run newer than its Mistral wait finished otherwise (a running or superseded
# one does not end the wait); ``since`` is when the first of those waits ended, ``code`` the
# newest wait's. No row for an operator who is not enabled; NULLs and 0 when none waits.
MISTRAL_STATUS_SQL = f"""
    CREATE FUNCTION public.kaagaz_op_mistral_status(p_operator uuid)
    RETURNS TABLE (code text, since timestamptz, documents_waiting bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT (array_agg(r.error_code ORDER BY r.started_at DESC, r.id DESC))[1], min(r.finished_at),
               count(DISTINCT (r.customer_id, r.document_id))
        FROM public.processing_runs r
        WHERE r.error_code IN {MISTRAL_WAITS} AND r.document_id IS NOT NULL
          AND r.started_at >= now() - make_interval(hours => {STATUS_HOURS})
          AND NOT EXISTS (SELECT 1 FROM public.processing_runs n
                          WHERE n.customer_id = r.customer_id AND n.document_id = r.document_id
                            AND (n.started_at, n.id) > (r.started_at, r.id)
                            AND n.status NOT IN ('running', 'superseded')
                            AND (n.error_code IS NULL OR n.error_code NOT IN {MISTRAL_WAITS}))
        HAVING EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
"""
MISTRAL_STATUS_COMMENT = (
    "Operator: whether Mistral stopped reading pages: the documents still waiting on it in the last"
    " 24 hours, every organisation's together, the newest wait's error code and when the first of"
    " them waited. Counts and codes only."
)

# {table.column: comment} (0007's rule: every column has one).
COMMENTS = {
    **{
        f"{table}.scan_reader": "Who reads scanned pages: openrouter (Gemini through OpenRouter) or mistral"
        " (Mistral OCR). NULL, " + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", "openrouter."),
        )
    },
    **{
        f"{table}.mistral_model": "The Mistral OCR model that reads scanned pages while the reader is"
        " mistral: mistral-ocr-4-1 or mistral-ocr-2512. NULL, " + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default."),
            ("platform_ai_settings", "mistral-ocr-4-1."),
        )
    },
}


def _columns(table: str, prefix: str) -> str:
    """``table``'s two new columns with their named CHECKs, in one ALTER."""
    return f"""
    ALTER TABLE {table}
        ADD COLUMN scan_reader text NULL CONSTRAINT {prefix}_scan_reader CHECK (scan_reader IN {READERS}),
        ADD COLUMN mistral_model text NULL CONSTRAINT {prefix}_mistral_model
            CHECK (mistral_model IN {MISTRAL_MODELS})
    """


UPGRADE = [
    # The four functions with their old parameters and columns go first: nothing calls them now.
    *[f"DROP FUNCTION public.{signature}" for signature in OLD],
    _columns("customer_ai_settings", "ai_customer_settings"),
    _columns("platform_ai_settings", "ai_platform_settings"),
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    # The four functions again, with both fields; the same comments and grants.
    *[
        statement
        for signature, body in NEW.items()
        for statement in _create(signature, body, _comment(signature))
    ],
    # The estimate's scan calls: the priced model's alone.
    f"DROP FUNCTION public.{SCAN_COST}",
    *_create(SCAN_COST, SCAN_COST_SQL, SCAN_COST_COMMENT),
    # A check on Mistral, at its one setting.
    f"ALTER TABLE scan_accuracy_checks DROP CONSTRAINT {CHECK_RESOLUTION},"
    f" ADD CONSTRAINT {CHECK_RESOLUTION} CHECK ({_RESOLUTIONS_0064} OR scan_resolution = 'page')",
    # Mistral's stop for the console: the few runs that waited on it.
    "CREATE INDEX processing_runs_mistral_waits ON processing_runs (started_at)"
    f" WHERE error_code IN {MISTRAL_WAITS}",
    "COMMENT ON INDEX processing_runs_mistral_waits IS 'The runs that waited on Mistral, by time (the"
    " operator''s Mistral status).'",
    *_create(MISTRAL_STATUS, MISTRAL_STATUS_SQL, MISTRAL_STATUS_COMMENT),
]

DOWNGRADE = [
    f"DROP FUNCTION public.{MISTRAL_STATUS}",
    "DROP INDEX processing_runs_mistral_waits",
    f"DROP FUNCTION public.{SCAN_COST}",
    *_create(SCAN_COST, _0064.SCAN_COST_SQL, _0064.SCAN_COST_COMMENT),
    "DELETE FROM scan_accuracy_checks WHERE scan_resolution = 'page'",
    f"ALTER TABLE scan_accuracy_checks DROP CONSTRAINT {CHECK_RESOLUTION},"
    f" ADD CONSTRAINT {CHECK_RESOLUTION} CHECK ({_RESOLUTIONS_0064})",
    *[f"DROP FUNCTION public.{signature}" for signature in NEW],
    "ALTER TABLE customer_ai_settings DROP COLUMN mistral_model, DROP COLUMN scan_reader",
    "ALTER TABLE platform_ai_settings DROP COLUMN mistral_model, DROP COLUMN scan_reader",
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
