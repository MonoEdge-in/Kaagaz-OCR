"""File management (Phase 2 PRD 1, section 7; Tushar, 19 Oct 2026). The PRD names it 0040;
0040 is the session timeout, so this is 0041.

* ``folders.kind`` (department, category, party, property, year, custom; every existing
  folder custom, F4) and ``folders.sort_order`` (the department order on the dashboard).
  A department is top level only. ``folders.autofile_key`` (0025, 0027) also takes the
  keys of the department template (F3): ``type:<type>:<department id>``,
  ``property:<village>:<number>`` and ``year:<yyyy>:<category id>``.
* ``documents``: ``owner_id`` (backfilled from the family's first uploader, O1),
  ``sensitivity`` (backfilled by the type rule from each family's current version, O2) and
  ``sensitivity_set_by_person``, ``details_updated_by`` and ``details_updated_at`` (O3),
  ``version_note`` (V9), ``name_key`` (V2, backfilled), ``text_minhash`` and ``text_bands``
  (V1, filled later by the worker's sweep), with a trigram index on the name key and a GIN
  index on the bands.
* ``version_suggestions`` (V5): customer-only, the app reads, adds and decides them.
* ``audit_log.folder_id`` and ``family_id`` (A3), with three indexes for the timelines.
* ``version_spotting`` (V8) joins the platform (0038) and organisation (0039) switch lists:
  both CHECKs and the operator's four functions that name the features.

The new document columns have constant defaults or none (no table rewrite); the backfills
are one UPDATE each. The downgrade drops it all and puts 0040's schema back (keys and
switch rows only the new rules allow are removed first).

Revision ID: 0041
Revises: 0040 (the sign-in session timeout)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0041", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0038 = _load("0038_ai_task_settings.py")
_0039 = _load("0039_org_feature_switches.py")
APP_ROLE = _0038.APP_ROLE
NEW_FEATURE = "version_spotting"

# --- the feature switch lists (V8) ---
_CREATE, _REPLACE = "    CREATE FUNCTION public.", "    CREATE OR REPLACE FUNCTION public."
_PLATFORM_OLD = _0038.FEATURES[1:-1]
_ORG_OLD = _0039.ORG_FEATURES[1:-1]


def _replaced(body: str, old: str, new: str) -> str:
    if old not in body:
        raise RuntimeError("an earlier function is not the text 0041 replaces")
    return body.replace(_CREATE, _REPLACE).replace(old, new)


def _switch_functions(platform: str, org: str) -> list[str]:
    """The four functions that name the features, as CREATE OR REPLACE, with these lists."""
    return [
        _replaced(_0039._platform(_0039.COUNT_EDITS), _PLATFORM_OLD, platform),
        _replaced(
            _0038.FUNCTIONS["kaagaz_op_set_feature_switch(uuid, text, boolean, bigint)"],
            _PLATFORM_OLD,
            platform,
        ),
        *(_replaced(body, _ORG_OLD, org) for body in _0039.FUNCTIONS.values()),
    ]


def _feature_check(table: str, names: str) -> list[str]:
    name = f"{table}_feature_check"  # 0038's and 0039's inline CHECKs, by their default names
    return [
        f"ALTER TABLE {table} DROP CONSTRAINT {name}",
        f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK (feature IN ({names}))",
    ]


# --- the auto-filing keys (F3) ---
KEY_CHECK = "folders_autofile_key_check"  # 0025's inline CHECK, by its default name
UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
OLD_KEYS = "autofile_key ~ '^type:[a-z_]{1,32}$' OR autofile_key ~ '^party:[0-9A-Z]{15}$'"  # 0027
NEW_KEYS = (
    f"autofile_key ~ '^type:[a-z_]{{1,32}}(:{UUID})?$' OR autofile_key ~ '^party:[0-9A-Z]{{15}}$'"
    " OR autofile_key ~ '^property:[a-z0-9-]{0,40}:[0-9a-z/]{1,32}$'"
    f" OR autofile_key ~ '^year:[0-9]{{4}}:{UUID}$'"
)

# --- the name key (V2), the same rule as kaagaz's own: lower case, no extension, Devanagari
# digits as ASCII, no dates, no version markers, no punctuation, single spaces ---
TYPOGRAPHIC = "".join(map(chr, (0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D)))  # dashes, curly quotes
NAME_KEY = (
    "btrim(regexp_replace(regexp_replace(regexp_replace(regexp_replace("
    "translate(lower(regexp_replace(display_name, '\\.[A-Za-z0-9]{1,5}$', '')),"
    " '०१२३४५६७८९', '0123456789'),"
    " '[0-9]{1,4}[-./][0-9]{1,2}[-./][0-9]{1,4}', ' ', 'g'),"
    " '\\m(v|ver|version|rev)\\s*[0-9]*\\M|\\m(final|latest|copy)\\M|\\([0-9]+\\)', ' ', 'g'),"
    f" '[!-/:-@[-`{{-~।॥{TYPOGRAPHIC}]+', ' ', 'g'),"
    " '\\s+', ' ', 'g'))"
)
SENSITIVE_TYPES = "('land_record', 'contract', 'bank_statement')"

UPGRADE = [
    # Folders: F1 and F4.
    "ALTER TABLE folders ADD COLUMN kind text NOT NULL DEFAULT 'custom'"
    " CONSTRAINT folders_kind CHECK (kind IN ('department', 'category', 'party', 'property', 'year', 'custom')),"
    " ADD COLUMN sort_order integer NULL,"
    " ADD CONSTRAINT folders_department_top_level CHECK (kind <> 'department' OR parent_id IS NULL)",
    f"ALTER TABLE folders DROP CONSTRAINT {KEY_CHECK}",
    f"ALTER TABLE folders ADD CONSTRAINT {KEY_CHECK} CHECK ({NEW_KEYS})",
    # Documents: O1 to O3, V1, V2 and V9.
    "ALTER TABLE documents ADD COLUMN owner_id uuid NULL,"
    " ADD COLUMN sensitivity text NOT NULL DEFAULT 'internal' CONSTRAINT documents_sensitivity"
    " CHECK (sensitivity IN ('public', 'internal', 'confidential', 'restricted')),"
    " ADD COLUMN sensitivity_set_by_person boolean NOT NULL DEFAULT false,"
    " ADD COLUMN details_updated_by uuid NULL,"
    " ADD COLUMN details_updated_at timestamptz NULL,"
    " ADD COLUMN version_note text NULL CONSTRAINT documents_version_note CHECK (length(version_note) <= 200),"
    " ADD COLUMN name_key text NULL,"
    " ADD COLUMN text_minhash bigint[] NULL,"
    " ADD COLUMN text_bands bigint[] NULL,"
    " ADD CONSTRAINT documents_owner FOREIGN KEY (customer_id, owner_id) REFERENCES users (customer_id, id)",
    "UPDATE documents d SET owner_id = r.uploaded_by FROM documents r"
    " WHERE r.customer_id = d.customer_id AND r.id = d.family_id",
    "UPDATE documents d SET sensitivity = 'confidential' WHERE EXISTS (SELECT 1 FROM documents c"
    " WHERE c.customer_id = d.customer_id AND c.family_id = d.family_id AND c.is_current"
    f" AND c.doc_type IN {SENSITIVE_TYPES})",
    f"UPDATE documents SET name_key = {NAME_KEY}",
    "CREATE INDEX documents_name_key ON documents USING gin (name_key gin_trgm_ops)",
    "CREATE INDEX documents_text_bands ON documents USING gin (text_bands)",
    # Version suggestions: V5.
    """
    CREATE TABLE version_suggestions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        document_id uuid NOT NULL,
        candidate_family_id uuid NOT NULL,
        text_overlap real NULL,
        name_similarity real NULL,
        shared_identifier text NULL,
        outcome text NOT NULL CHECK (outcome IN ('suggested', 'auto')),
        status text NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected', 'undone')),
        decided_by uuid NULL,
        decided_at timestamptz NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, candidate_family_id),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """,
    f"GRANT SELECT, INSERT, UPDATE ON version_suggestions TO {APP_ROLE}",
    "ALTER TABLE version_suggestions ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON version_suggestions
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    # The activity log: A3.
    "ALTER TABLE audit_log ADD COLUMN folder_id uuid NULL, ADD COLUMN family_id uuid NULL",
    "CREATE INDEX audit_log_family ON audit_log (customer_id, family_id, created_at DESC)",
    "CREATE INDEX audit_log_folder ON audit_log (customer_id, folder_id, created_at DESC)",
    "CREATE INDEX audit_log_actor ON audit_log (customer_id, actor_user_id, created_at DESC)",
    # The feature switches: V8.
    *_feature_check("feature_switches", f"{_PLATFORM_OLD}, '{NEW_FEATURE}'"),
    *_feature_check("org_feature_switches", f"{_ORG_OLD}, '{NEW_FEATURE}'"),
    *_switch_functions(f"{_PLATFORM_OLD}, '{NEW_FEATURE}'", f"{_ORG_OLD}, '{NEW_FEATURE}'"),
    "COMMENT ON COLUMN folders.kind IS 'department (top level only), category (a type), party,"
    " property, year, or custom (made by a person; every folder before 0041).'",
    "COMMENT ON COLUMN documents.owner_id IS 'The owner, on the whole family; the first uploader at first.'",
    "COMMENT ON COLUMN documents.sensitivity_set_by_person IS 'A person chose the label: the type rule"
    " never changes it.'",
    "COMMENT ON TABLE version_suggestions IS 'An upload that looks like a new version of another family,"
    " and what a person decided.'",
]

DOWNGRADE = [
    f"DELETE FROM feature_switches WHERE feature = '{NEW_FEATURE}'",
    f"DELETE FROM org_feature_switches WHERE feature = '{NEW_FEATURE}'",
    *_switch_functions(_PLATFORM_OLD, _ORG_OLD),
    *_feature_check("feature_switches", _PLATFORM_OLD),
    *_feature_check("org_feature_switches", _ORG_OLD),
    "DROP INDEX IF EXISTS audit_log_actor",
    "DROP INDEX IF EXISTS audit_log_folder",
    "DROP INDEX IF EXISTS audit_log_family",
    "ALTER TABLE audit_log DROP COLUMN IF EXISTS family_id, DROP COLUMN IF EXISTS folder_id",
    "DROP TABLE IF EXISTS version_suggestions",
    "DROP INDEX IF EXISTS documents_text_bands",
    "DROP INDEX IF EXISTS documents_name_key",
    "ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_owner,"
    " DROP COLUMN IF EXISTS text_bands, DROP COLUMN IF EXISTS text_minhash, DROP COLUMN IF EXISTS name_key,"
    " DROP COLUMN IF EXISTS version_note, DROP COLUMN IF EXISTS details_updated_at,"
    " DROP COLUMN IF EXISTS details_updated_by, DROP COLUMN IF EXISTS sensitivity_set_by_person,"
    " DROP COLUMN IF EXISTS sensitivity, DROP COLUMN IF EXISTS owner_id",
    f"UPDATE folders SET autofile_key = NULL WHERE NOT ({OLD_KEYS})",
    f"ALTER TABLE folders DROP CONSTRAINT {KEY_CHECK}",
    f"ALTER TABLE folders ADD CONSTRAINT {KEY_CHECK} CHECK ({OLD_KEYS})",
    "ALTER TABLE folders DROP CONSTRAINT IF EXISTS folders_department_top_level,"
    " DROP COLUMN IF EXISTS sort_order, DROP COLUMN IF EXISTS kind",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
