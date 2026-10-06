"""Every customer table, found from the catalog, has both locks and the app's grants.

A table is a customer table when it has a ``customer_id`` column. Finding them
from the catalog means a new table can't be left out of this check: it needs
row level security, the ``customer_only`` policy with USING and WITH CHECK, and
grants to the app role, which must not own it (an owner skips the policy).

Super admin PR 2 (0015, NEEDS VRUSHIT'S APPROVAL): the platform operator's
tables have no customer_id and nobody's grants; the role ``kaagaz_operator``
has no table rights; each owner-rights function may be run by exactly one role.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.db_support import APP_ROLE

pytestmark = pytest.mark.db

POLICY = "(customer_id = kaagaz_current_customer())"
FROM_0001 = {
    "users",
    "documents",
    "pages",
    "reader_outputs",
    "pieces",
    "extracted_values",
    "corrections",
    "learned_corrections",
    "conversations",
    "messages",
    "feedback",
    "audit_log",
}
FROM_0002 = {"folders", "tags", "document_tags"}
# (SELECT, INSERT, UPDATE, DELETE) for the app role, the least each needs: a save
# deletes and inserts invoices and links, never updates them; lines are only
# inserted (the invoice's delete cascades to them); runs are never deleted, and
# only their outcome columns may be updated (RUN_OUTCOME below).
FROM_0004 = {
    "invoices": (True, True, False, True),
    "invoice_lines": (True, True, False, False),
    "document_links": (True, True, False, True),
    "processing_runs": (True, True, False, False),
}
# My words (0008): a word's text and meaning may change; a spot is only added or deleted.
FROM_0008 = {"user_words": (True, True, True, True), "user_word_spots": (True, True, False, True)}
# Story of this PDF (0009): a story is created, replaced (its vector too) and deleted.
FROM_0009 = {"document_stories": (True, True, True, True)}
# The second policy of the tables that belong to one user (0008, 0009): it must pass too.
OWN_USER = "(user_id = (NULLIF(current_setting('kaagaz.user_id'::text, true), ''::text))::uuid)"
RUN_OUTCOME = {
    "status", "error_code", "ocr_engine", "model_chat", "model_vision", "model_embed", "config",
    "pages_cells", "pages_text_layer", "pages_ocr", "pages_blank", "vision_crops", "model_calls",
    "dropped_quotes", "quotes_returned", "values_verified", "values_needs_review", "seconds_total",
    "seconds_ocr", "finished_at",
}  # fmt: skip
# The owner-rights (SECURITY DEFINER) functions, the only ways past the policies,
# each narrow: {name: (what it returns, its search path, the ONE role that may run
# it)}. Those that list across customers or users give ids only. The app may never
# run an operator's function, nor the operator an app's.
EMPTY_PATH = ['search_path=""']
OPERATOR = "kaagaz_operator"
OWNER_RIGHTS = {
    "kaagaz_login_lookup": (
        "TABLE(user_id uuid, customer_id uuid, password_hash text, role text, disabled boolean)",
        ["search_path=public, pg_temp"],
        APP_ROLE,
    ),  # 0001, sign-in
    "kaagaz_create_customer": ("uuid", ["search_path=public, pg_temp"], APP_ROLE),  # 0001
    "kaagaz_customer_ids_with_trash": ("SETOF uuid", EMPTY_PATH, APP_ROLE),  # 0002, the purge
    "kaagaz_customer_ids": ("SETOF uuid", EMPTY_PATH, APP_ROLE),  # 0006, reembed
    "kaagaz_stories_to_embed": (
        "TABLE(customer_id uuid, user_id uuid, family_id uuid)",
        EMPTY_PATH,
        APP_ROLE,
    ),  # 0010
    # 0015, the platform operator's sign-in
    "kaagaz_op_begin_login": (
        "TABLE(operator_id uuid, email text, display_name text, password_hash text, disabled boolean,"
        " session_version integer, totp_secret_enc text, totp_enabled boolean, totp_last_step bigint,"
        " failed_logins integer)",
        EMPTY_PATH,
        OPERATOR,
    ),
    "kaagaz_op_login_failed": ("void", EMPTY_PATH, OPERATOR),
    "kaagaz_op_login_succeeded": ("integer", EMPTY_PATH, OPERATOR),
    "kaagaz_op_enrol": ("integer", EMPTY_PATH, OPERATOR),
    "kaagaz_op_session": (
        "TABLE(email text, display_name text, session_version integer)",
        EMPTY_PATH,
        OPERATOR,
    ),
    "kaagaz_op_logout": ("void", EMPTY_PATH, OPERATOR),
}
# The only tables without customer_id: the customers themselves, alembic's own, and
# the platform operator's (0015), which belong to no customer.
OPERATOR_TABLES = {"operators", "platform_audit"}
NO_CUSTOMER_ID = {"customers", "alembic_version"} | OPERATOR_TABLES

_CUSTOMER_TABLES = text(
    "SELECT c.relname, c.relrowsecurity, pg_get_userbyid(c.relowner)"
    " FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid"
    " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')"
    " AND a.attname = 'customer_id' AND NOT a.attisdropped"
    " ORDER BY c.relname"
)


def _customer_tables(db: Database) -> dict[str, tuple[bool, str]]:
    with db.system() as conn:
        return {name: (rls, owner) for name, rls, owner in conn.execute(_CUSTOMER_TABLES)}


def test_every_customer_table_has_row_level_security_and_the_customer_only_policy(db: Database) -> None:
    tables = _customer_tables(db)
    assert set(tables) >= FROM_0001 | FROM_0002 | set(FROM_0004) | set(FROM_0008) | set(FROM_0009)

    with db.system() as conn:
        policies = {
            table: (command, permissive, roles, using, check)
            for table, command, permissive, roles, using, check in conn.execute(
                text(
                    "SELECT tablename, cmd, permissive, roles, qual, with_check FROM pg_policies"
                    " WHERE schemaname = 'public' AND policyname = 'customer_only'"
                )
            )
        }
        app_role = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
        ).one()

    assert tuple(app_role) == (False, False)
    for table, (rls, owner) in tables.items():
        assert rls, f"{table} has no row level security"
        assert owner != APP_ROLE, f"{table} is owned by the app role, which skips its policy"
        # For every command and every role, a row must be the current customer's
        # both to be seen (USING) and to be written (WITH CHECK).
        assert policies.get(table) == ("ALL", "PERMISSIVE", ["public"], POLICY, POLICY), table


def test_every_customer_table_is_granted_to_the_app_role(db: Database) -> None:
    tables = _customer_tables(db)
    with db.system() as conn:
        granted = {
            table: (select, insert, update, delete)
            for table, select, insert, update, delete in conn.execute(
                text(
                    "SELECT c.relname, has_table_privilege(:r, c.oid, 'SELECT'),"
                    " has_table_privilege(:r, c.oid, 'INSERT'), has_table_privilege(:r, c.oid, 'UPDATE'),"
                    " has_table_privilege(:r, c.oid, 'DELETE')"
                    " FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(:t)"
                ),
                {"r": APP_ROLE, "t": sorted(tables)},
            )
        }

    assert set(granted) == set(tables)
    for table, (select, insert, _update, _delete) in granted.items():
        assert (select, insert) == (True, True), table
    for table in FROM_0002:
        assert granted[table] == (True, True, True, True), table
    for table, expected in (FROM_0004 | FROM_0008 | FROM_0009).items():
        assert granted[table] == expected, table


def test_the_tables_of_one_user_also_have_the_restrictive_own_user_only_policy(db: Database) -> None:
    """It must pass as well as customer_only: with no user set, no row is seen or written."""
    with db.system() as conn:
        policies = {
            table: (command, permissive, roles, using, check)
            for table, command, permissive, roles, using, check in conn.execute(
                text(
                    "SELECT tablename, cmd, permissive, roles, qual, with_check FROM pg_policies"
                    " WHERE schemaname = 'public' AND policyname <> 'customer_only'"
                )
            )
        }
    own = FROM_0008 | FROM_0009
    assert policies == {table: ("ALL", "RESTRICTIVE", ["public"], OWN_USER, OWN_USER) for table in own}


def test_the_app_may_update_only_the_outcome_columns_of_a_run(db: Database) -> None:
    """Never its customer, document, start time or id."""
    with db.system() as conn:
        columns = conn.execute(
            text(
                "SELECT attname::text, has_column_privilege(:r, attrelid, attname, 'UPDATE')"
                " FROM pg_attribute WHERE attrelid = 'processing_runs'::regclass"
                " AND attnum > 0 AND NOT attisdropped"
            ),
            {"r": APP_ROLE},
        ).all()
    assert {name for name, may_update in columns if may_update} == RUN_OUTCOME


def test_the_app_may_update_only_the_upload_limit_of_its_customer(db: Database) -> None:
    """Never its status, so a disabled organisation cannot switch itself back on (0014)."""
    with db.system() as conn:
        columns = conn.execute(
            text(
                "SELECT attname::text, has_column_privilege(:r, attrelid, attname, 'UPDATE')"
                " FROM pg_attribute WHERE attrelid = 'customers'::regclass"
                " AND attnum > 0 AND NOT attisdropped"
            ),
            {"r": APP_ROLE},
        ).all()
    assert {name for name, may_update in columns if may_update} == {"max_upload_mb"}


_FOREIGN_KEYS = text(
    "SELECT con.conname, con.conrelid::regclass::text, con.confrelid::regclass::text,"
    " ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY AS k (num, pos)"
    "  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num ORDER BY k.pos),"
    " ARRAY(SELECT a.attname::text FROM unnest(con.confkey) WITH ORDINALITY AS k (num, pos)"
    "  JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.num ORDER BY k.pos)"
    " FROM pg_constraint con WHERE con.contype = 'f' AND con.connamespace = 'public'::regnamespace"
)


def test_every_foreign_key_between_customer_tables_matches_the_customer_on_both_sides(db: Database) -> None:
    """A key on the id alone would let a row hang under another customer's parent: the
    key checks run without row level security."""
    tables = _customer_tables(db)
    with db.system() as conn:
        keys = [tuple(row) for row in conn.execute(_FOREIGN_KEYS)]
    between = [key for key in keys if key[1] in tables and key[2] in tables]

    assert {key[1] for key in between} >= set(FROM_0004)
    for name, _table, _parent, columns, parent_columns in between:
        assert ("customer_id", "customer_id") in zip(columns, parent_columns, strict=True), name


def test_only_the_listed_tables_have_no_customer_id(db: Database) -> None:
    with db.system() as conn:
        every = conn.execute(
            text(
                "SELECT relname FROM pg_class"
                " WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p')"
            )
        )
        names = {str(row[0]) for row in every}
    assert names - set(_customer_tables(db)) == NO_CUSTOMER_ID


def test_only_the_listed_owner_rights_functions_exist_and_only_their_role_may_run_them(db: Database) -> None:
    """Found from the catalog, so a new one can't be left out: what it returns, its
    fixed search path, never runnable by PUBLIC, and by exactly one of the app and
    the operator."""
    with db.system() as conn:
        functions = conn.execute(
            text(
                "SELECT p.proname, pg_get_function_result(p.oid), p.proconfig,"
                " has_function_privilege('public', p.oid, 'EXECUTE'),"
                " has_function_privilege(:r, p.oid, 'EXECUTE'), has_function_privilege(:o, p.oid, 'EXECUTE')"
                " FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.prosecdef"
            ),
            {"r": APP_ROLE, "o": OPERATOR},
        ).all()
    assert {name for name, *_ in functions} == set(OWNER_RIGHTS)
    for name, result, config, public_may_run, app_may_run, operator_may_run in functions:
        role = OWNER_RIGHTS[name][2]
        assert (result, config, role) == OWNER_RIGHTS[name], name
        assert (public_may_run, app_may_run, operator_may_run) == (
            False,
            role == APP_ROLE,
            role == OPERATOR,
        ), name


def test_the_operator_role_has_no_table_rights_and_its_tables_no_grants_and_no_policy(db: Database) -> None:
    """kaagaz_operator reaches data only through its functions; nobody but the owner
    reads or writes operators and platform_audit, and with no policy, row level
    security shows them to nobody else either."""
    with db.system() as conn:
        role = conn.execute(
            text("SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = :o"),
            {"o": OPERATOR},
        ).one()
        granted = conn.execute(
            text(
                "SELECT c.relname, r.rolname FROM pg_class c CROSS JOIN pg_roles r"
                " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm')"
                " AND (r.rolname = :o OR (r.rolname = :a AND c.relname = ANY(:t)))"
                " AND (has_table_privilege(r.oid, c.oid,"
                " 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"
                " OR has_any_column_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))"
            ),
            {"o": OPERATOR, "a": APP_ROLE, "t": sorted(OPERATOR_TABLES)},
        ).all()
        locked = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity,"
                " (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)"
                " FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(:t)"
            ),
            {"t": sorted(OPERATOR_TABLES)},
        ).all()
    assert tuple(role) == (False, False, False)
    assert granted == []
    assert sorted(tuple(row) for row in locked) == [("operators", True, 0), ("platform_audit", True, 0)]
