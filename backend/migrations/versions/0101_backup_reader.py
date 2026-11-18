"""The backup reader (#664, Tushar, 18 Nov 2026; NEEDS VRUSHIT'S APPROVAL: schema and operator
functions): a page Mistral OCR reads no text on (a map read as one picture, or a page it refuses)
is read once by a backup model the super admin chose.

* ``customer_ai_settings`` and ``platform_ai_settings``: ``backup_reader`` (new), how the backup
  model reads, ``openrouter`` (Gemini's normal read) or ``ultra_low_cost`` (one compact read under
  the ceiling), or ``off``; text NULL with a named CHECK. ``backup_model`` (new), the model, a
  ``model_catalog`` id (a foreign key, as ``documents.read_model``); set exactly when
  ``backup_reader`` names a way to read (a named CHECK). The pair inherits as one, as the other AI
  settings do field by field: an organisation's NULL the platform's, the platform's NULL off; an
  organisation's ``off`` is off whatever the platform says. Every new value is NULL on release
  day, so no organisation reads differently until the super admin chooses it. The app and the
  worker keep SELECT on both tables, the whole table, so they read the new columns alike (0026,
  0083); neither writes them.
* The operator's four AI functions (0070's reads, 0073's saves) read, save and audit the pair (the
  operator's audit only, as the scan settings: 0064's rule), and the defaults count the
  organisations inheriting it. A save refuses a model not in the catalogue, not eligible to scan
  or not enabled (``unavailable``), one not verified for the reader's prompt (``not_verified``), as
  for the scanning models, and a pair that is not whole (``backup_reader_incomplete``). Each is
  dropped and created again from its text in force with each change made exactly once
  (``_changed``), with the same comment, grants, SECURITY DEFINER and empty search path. Whether
  an Ultra low cost model can read with minimal thinking is the API's check (409
  ``ulc_needs_gemini``), as for Ultra low cost's lead.
* ``kaagaz_op_set_model_enabled`` (0026's): a model still named as a backup reader, by an
  organisation or by the platform, can't be disabled (``in_use``), as a model still allowed. Its
  live body is read back from the catalogue and changed exactly once, or the migration stops
  (0070's way); CREATE OR REPLACE keeps the owner, grants, comment, SECURITY DEFINER and empty
  search path.

The downgrade puts 0026's in-use check back, drops the four functions, the columns (their CHECKs
and foreign keys with them), then puts 0070's and 0073's functions back exactly. Lost on
downgrade: the backup reader chosen (Mistral's pages read no text on are left so again, as
before); the ledger keeps every backup read.

Revision ID: 0101
Revises: 0100 (linking files as versions)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0101", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0070 = _load("0070_ultra_low_cost.py")
_0073 = _load("0073_ulc_ceiling_55.py")
_changed = _0070._changed
_create = _0070._create
_comment = _0070._comment
_literal = _0070._literal
_PREFIXES = _0070._PREFIXES  # {table: its constraints' prefix}

ORG_AI = _0070.ORG_AI
AI_DEFAULTS = _0070.AI_DEFAULTS
SET_ORG_AI_OLD, SET_AI_DEFAULTS_OLD = _0073.SAVES
# The saves take the backup reader and its model after Ultra low cost's ceiling.
SET_ORG_AI = SET_ORG_AI_OLD.removesuffix(")") + ", text, text)"
SET_AI_DEFAULTS = SET_AI_DEFAULTS_OLD.removesuffix(")") + ", text, text)"
SET_MODEL_ENABLED = "kaagaz_op_set_model_enabled(uuid, text, boolean, text, text, bigint)"

# How the backup reader reads, or off; the ways that name a model (the API's BackupReaderWay).
READERS = "('off', 'openrouter', 'ultra_low_cost')"
MODEL_READERS = "('openrouter', 'ultra_low_cost')"

# --- the changes, each made exactly once in its body (``_changed``) ---------------------------
_PARAMS = (
    "p_ulc_ceiling_paise integer, OUT outcome text",
    "p_ulc_ceiling_paise integer, p_backup_reader text, p_backup_model text, OUT outcome text",
)
_BACKUP = (
    "        v_eff_scan text[];\n",
    "        v_backup text[] := array_remove(ARRAY[p_backup_model], NULL);\n        v_eff_scan text[];\n",
)
# The model is checked as the scanning models are: locked, then in the catalogue, eligible to scan,
# enabled and verified for the reader's prompt.
_LOCK = (
    "ANY (v_scan || v_chat) ORDER BY m.id FOR SHARE;",
    "ANY (v_scan || v_chat || v_backup) ORDER BY m.id FOR SHARE;",
)
_AVAILABLE = (
    "SELECT 1 FROM unnest(v_scan) AS w(model)",
    "SELECT 1 FROM unnest(v_scan || v_backup) AS w(model)",
)
_VERIFIED = (
    "WHERE m.id = ANY (v_scan) AND m.verified_prompt_version IS DISTINCT FROM p_scan_prompt",
    "WHERE m.id = ANY (v_scan || v_backup) AND m.verified_prompt_version IS DISTINCT FROM p_scan_prompt",
)
# After the ceiling's check, before anything is written: a model exactly when a way to read is named.
_WHOLE = (
    "            outcome := 'ceiling_out_of_range';\n            RETURN;\n        END IF;\n",
    "            outcome := 'ceiling_out_of_range';\n            RETURN;\n        END IF;\n"
    f"        IF p_backup_reader NOT IN {READERS}\n"
    f"           OR (p_backup_model IS NOT NULL) <> COALESCE(p_backup_reader IN {MODEL_READERS}, false) THEN\n"
    "            outcome := 'backup_reader_incomplete';\n            RETURN;\n        END IF;\n",
)
_INHERITING = (
    "               count(*) FILTER (WHERE s.ulc_ceiling_paise IS NULL)\n",
    "               count(*) FILTER (WHERE s.ulc_ceiling_paise IS NULL),\n"
    "               count(*) FILTER (WHERE s.backup_reader IS NULL)\n",
)
ORG_SET_CHANGES = [
    _PARAMS,
    _BACKUP,
    _LOCK,
    _AVAILABLE,
    _VERIFIED,
    _WHOLE,
    ("(customer_id, ulc_ceiling_paise,", "(customer_id, backup_reader, backup_model, ulc_ceiling_paise,"),
    (
        "VALUES (p_customer, p_ulc_ceiling_paise,",
        "VALUES (p_customer, p_backup_reader, p_backup_model, p_ulc_ceiling_paise,",
    ),
    (
        "SET ulc_ceiling_paise = EXCLUDED.ulc_ceiling_paise,",
        "SET backup_reader = EXCLUDED.backup_reader, backup_model = EXCLUDED.backup_model,"
        " ulc_ceiling_paise = EXCLUDED.ulc_ceiling_paise,",
    ),
    (
        "'daily_page_limit', p_daily_page_limit, 'ulc_ceiling_paise', p_ulc_ceiling_paise,",
        "'daily_page_limit', p_daily_page_limit, 'ulc_ceiling_paise', p_ulc_ceiling_paise,\n"
        "            'backup_reader', p_backup_reader, 'backup_model', p_backup_model,",
    ),
    # The operator's platform audit holds it; the organisation's own row does not (0064's rule).
    (
        "'daily_page_limit', 'ulc_ceiling_paise']);",
        "'daily_page_limit', 'ulc_ceiling_paise', 'backup_reader', 'backup_model']);",
    ),
]
DEFAULTS_SET_CHANGES = [
    _PARAMS,
    _BACKUP,
    _LOCK,
    _AVAILABLE,
    _VERIFIED,
    _WHOLE,
    (
        "        v_ulc_ceiling_paise bigint;\n",
        "        v_ulc_ceiling_paise bigint;\n        v_backup_reader bigint;\n",
    ),
    _INHERITING,
    ("v_daily_page_limit, v_ulc_ceiling_paise;", "v_daily_page_limit, v_ulc_ceiling_paise, v_backup_reader;"),
    (
        "SET ulc_ceiling_paise = p_ulc_ceiling_paise,",
        "SET backup_reader = p_backup_reader, backup_model = p_backup_model,"
        " ulc_ceiling_paise = p_ulc_ceiling_paise,",
    ),
    (
        "THEN 0 ELSE v_ulc_ceiling_paise END)));",
        "THEN 0 ELSE v_ulc_ceiling_paise END,\n                'backup_reader', CASE WHEN v_before ->"
        " 'backup_reader' = v_after -> 'backup_reader' AND v_before -> 'backup_model' = v_after ->"
        " 'backup_model' THEN 0 ELSE v_backup_reader END)));",
    ),
]
# The defaults' before and after (twice in the save) hold it.
_DEFAULTS_JSON = (
    "'daily_page_limit', p.daily_page_limit, 'ulc_ceiling_paise', p.ulc_ceiling_paise)",
    "'daily_page_limit', p.daily_page_limit, 'ulc_ceiling_paise', p.ulc_ceiling_paise,"
    " 'backup_reader', p.backup_reader, 'backup_model', p.backup_model)",
)
ORG_READ_CHANGES = [
    (
        "ulc_ceiling_paise smallint,\n                   ensemble boolean",
        "ulc_ceiling_paise smallint,\n                   backup_reader text, backup_model text, ensemble boolean",
    ),
    (
        "s.daily_page_limit, s.ulc_ceiling_paise,\n",
        "s.daily_page_limit, s.ulc_ceiling_paise, s.backup_reader, s.backup_model,\n",
    ),
]
DEFAULTS_READ_CHANGES = [
    (
        "ulc_ceiling_paise smallint,\n                   orgs_inheriting_scan_models",
        "ulc_ceiling_paise smallint,\n                   backup_reader text, backup_model text,"
        " orgs_inheriting_scan_models",
    ),
    (
        "orgs_inheriting_ulc_ceiling_paise bigint)",
        "orgs_inheriting_ulc_ceiling_paise bigint, orgs_inheriting_backup_reader bigint)",
    ),
    (
        "p.daily_page_limit, p.ulc_ceiling_paise, n.*",
        "p.daily_page_limit, p.ulc_ceiling_paise, p.backup_reader, p.backup_model, n.*",
    ),
    _INHERITING,
]

# The bodies in force now (0070's reads, 0073's saves), each restored exactly by the downgrade.
OLD = {
    ORG_AI: _0070.NEW[ORG_AI],
    AI_DEFAULTS: _0070.NEW[AI_DEFAULTS],
    SET_ORG_AI_OLD: _0073.NEW[SET_ORG_AI_OLD],
    SET_AI_DEFAULTS_OLD: _0073.NEW[SET_AI_DEFAULTS_OLD],
}
NEW = {
    ORG_AI: _changed(OLD[ORG_AI], ORG_READ_CHANGES),
    AI_DEFAULTS: _changed(OLD[AI_DEFAULTS], DEFAULTS_READ_CHANGES),
    SET_ORG_AI: _changed(OLD[SET_ORG_AI_OLD], ORG_SET_CHANGES),
    SET_AI_DEFAULTS: _changed(
        OLD[SET_AI_DEFAULTS_OLD], [*DEFAULTS_SET_CHANGES, _DEFAULTS_JSON], times={_DEFAULTS_JSON[0]: 2}
    ),
}

# 0026's in-use check, in the live body, made exactly once (0070's way: the migration stops on any
# other count).
IN_USE = (
    "OR EXISTS (SELECT 1 FROM public.platform_models pm WHERE pm.model = p_model)) THEN",
    "OR EXISTS (SELECT 1 FROM public.platform_models pm WHERE pm.model = p_model)\n"
    "                              OR EXISTS (SELECT 1 FROM public.customer_ai_settings s"
    " WHERE s.backup_model = p_model)\n"
    "                              OR EXISTS (SELECT 1 FROM public.platform_ai_settings p"
    " WHERE p.backup_model = p_model)) THEN",
)
_REWRITE = """
DO $body$
DECLARE
    v_def text := pg_get_functiondef('public.{function}'::regprocedure);
BEGIN
    IF coalesce(array_length(string_to_array(v_def, $k${old}$k$), 1), 0) <> 2 THEN
        RAISE EXCEPTION '{function} does not check a model in use as 0101 expects';
    END IF;
    EXECUTE replace(v_def, $k${old}$k$, $k${new}$k$);
END
$body$
"""


def _in_use(*, back: bool) -> str:
    old, new = (IN_USE[1], IN_USE[0]) if back else IN_USE
    return _REWRITE.format(function=SET_MODEL_ENABLED, old=old, new=new)


# {table.column: comment} (0007's rule: every column has one).
COMMENTS = {
    **{
        f"{table}.backup_reader": "How a page Mistral OCR read no text on (a map read as one picture, a page"
        " it refused) is read once more: openrouter (the model's normal read), ultra_low_cost (one compact"
        " read under the ceiling) or off. Never for Mistral being down, its key or its credit. NULL, "
        + inherits
        for table, inherits in (
            ("customer_ai_settings", "the platform default (with backup_model)."),
            ("platform_ai_settings", "off."),
        )
    },
    **{
        f"{table}.backup_model": "model_catalog.id of the backup reader's model, set exactly when backup_reader"
        " is openrouter or ultra_low_cost; it must be enabled, eligible to scan and verified when saved, and"
        " can't be disabled while named here."
        for table in _PREFIXES
    },
}


def _columns(table: str) -> list[str]:
    prefix = _PREFIXES[table]
    return [
        f"ALTER TABLE {table} ADD COLUMN backup_reader text NULL CONSTRAINT {prefix}_backup_reader"
        f" CHECK (backup_reader IN {READERS})",
        f"ALTER TABLE {table} ADD COLUMN backup_model text NULL CONSTRAINT {prefix}_backup_model_fk"
        " REFERENCES model_catalog (id)",
        f"ALTER TABLE {table} ADD CONSTRAINT {prefix}_backup_model CHECK ((backup_model IS NOT NULL) ="
        f" COALESCE(backup_reader IN {MODEL_READERS}, false))",
    ]


UPGRADE = [
    # The four functions with their old parameters and columns go first: nothing calls them now.
    *[f"DROP FUNCTION public.{signature}" for signature in OLD],
    *[statement for table in _PREFIXES for statement in _columns(table)],
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    # The four functions again, with the backup reader; the same comments and grants.
    *[
        statement
        for signature, body in NEW.items()
        for statement in _create(signature, body, _comment(signature))
    ],
    _in_use(back=False),
]

DOWNGRADE = [
    _in_use(back=True),
    *[f"DROP FUNCTION public.{signature}" for signature in NEW],
    *[
        f"ALTER TABLE {table} DROP COLUMN {column}"
        for table in _PREFIXES
        for column in ("backup_model", "backup_reader")
    ],
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
