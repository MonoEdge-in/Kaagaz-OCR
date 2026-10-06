"""Every customer table, found from the catalog, has both locks and the app's grants.

A table is a customer table when it has a ``customer_id`` column. Finding them
from the catalog means a new table can't be left out of this check: it needs
row level security, the ``customer_only`` policy with USING and WITH CHECK, and
grants to the app role, which must not own it (an owner skips the policy).

Super admin PR 2 (0015, NEEDS VRUSHIT'S APPROVAL): the operator's tables, role and functions.
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
# each narrow: {name: (what it returns, its search path)}. Those that list across
# customers or users give ids only.
EMPTY_PATH = ['search_path=""']
OWNER_RIGHTS = {
    "kaagaz_login_lookup": (
        "TABLE(user_id uuid, customer_id uuid, password_hash text, role text, disabled boolean)",
        ["search_path=public, pg_temp"],
    ),  # 0001, sign-in
    "kaagaz_create_customer": ("uuid", ["search_path=public, pg_temp"]),  # 0001
    "kaagaz_customer_ids_with_trash": ("SETOF uuid", EMPTY_PATH),  # 0002, the purge
    "kaagaz_customer_ids": ("SETOF uuid", EMPTY_PATH),  # 0006, reembed
    "kaagaz_stories_to_embed": ("TABLE(customer_id uuid, user_id uuid, family_id uuid)", EMPTY_PATH),  # 0010
    # 0018, the worker's purge of deleted organisations: ids only, and the one due, empty
    # customers row (never a kaagaz_op_* function: the app runs none of those).
    "kaagaz_customer_ids_to_purge": ("SETOF uuid", EMPTY_PATH),
    "kaagaz_finish_customer_delete": ("boolean", EMPTY_PATH),
    # 0019, the second sweep a day after a purge: ids only, and its org.swept row.
    "kaagaz_purged_ids_to_sweep": ("SETOF uuid", EMPTY_PATH),
    "kaagaz_customer_swept": ("boolean", EMPTY_PATH),
}
# The platform operator's (0015): only kaagaz_operator may run these, never the app.
OPERATOR = "kaagaz_operator"
OPERATOR_RIGHTS = {
    "kaagaz_op_begin_login": (
        "TABLE(operator_id uuid, email text, display_name text, password_hash text, disabled boolean,"
        " totp_secret_enc text, totp_last_step bigint, failed_logins integer)",
        EMPTY_PATH,
    ),
    "kaagaz_op_login_failed": ("void", EMPTY_PATH),
    "kaagaz_op_login_succeeded": ("integer", EMPTY_PATH),
    "kaagaz_op_session": ("TABLE(email text, display_name text, session_version integer)", EMPTY_PATH),
    "kaagaz_op_logout": ("void", EMPTY_PATH),
    # 0016: organisations with counts only, and the writes, each answering (outcome, result_id).
    "kaagaz_op_customers": (
        "TABLE(customer_id uuid, name text, status text, disabled_at timestamp with time zone,"
        " delete_after timestamp with time zone, created_at timestamp with time zone, admins bigint,"
        " reviewers bigint, members bigint, documents bigint, pages bigint, size_bytes bigint,"
        " last_activity_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    # 0017: one organisation's admins, and the changes to one of them.
    "kaagaz_op_admins": (
        "TABLE(user_id uuid, display_name text, email text, totp_enabled boolean,"
        " locked_until timestamp with time zone, disabled boolean, must_change_password boolean,"
        " last_login_at timestamp with time zone, created_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    **dict.fromkeys(
        (
            "kaagaz_op_create_customer",
            "kaagaz_op_set_customer_status",
            "kaagaz_op_create_admin",
            "kaagaz_op_change_admin",
            "kaagaz_op_schedule_delete",  # 0018
            "kaagaz_op_cancel_delete",
        ),
        ("record", EMPTY_PATH),
    ),
    "kaagaz_op_begin_step_up": (
        "TABLE(totp_secret_enc text, totp_last_step bigint, step_up_failures integer)",
        EMPTY_PATH,
    ),
    "kaagaz_op_step_up_failed": ("void", EMPTY_PATH),
    # 0020, read-only: the feed (no details, target id, person or document) and counts.
    "kaagaz_op_activity": (
        "TABLE(source text, entry_id bigint, created_at timestamp with time zone, action text,"
        " customer_id uuid, customer_name text, target_type text, actor text)",
        EMPTY_PATH,
    ),
    "kaagaz_op_health": (
        "TABLE(customer_id uuid, name text, status text, delete_after timestamp with time zone,"
        " documents_by_status jsonb, failed_by_code jsonb, in_progress bigint,"
        " oldest_in_progress_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    "kaagaz_op_purges": (
        "TABLE(purged bigint, last_purged_at timestamp with time zone, swept bigint,"
        " last_swept_at timestamp with time zone)",
        EMPTY_PATH,
    ),
}
# The only tables without customer_id: the customers themselves, alembic's own and
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
    fixed search path, never runnable by PUBLIC, and by the app (OWNER_RIGHTS) or
    the operator (OPERATOR_RIGHTS), never both."""
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
    assert {
        name: (result, config) for name, result, config, *_ in functions
    } == OWNER_RIGHTS | OPERATOR_RIGHTS
    for name, *_, public_may_run, app_may_run, operator_may_run in functions:
        expected = (False, name in OWNER_RIGHTS, name in OPERATOR_RIGHTS)
        assert (public_may_run, app_may_run, operator_may_run) == expected, name


def test_the_operator_role_has_no_table_rights_and_its_tables_no_grants_and_no_policy(db: Database) -> None:
    """kaagaz_operator reaches data only through its functions; the app has no right on
    the operator's tables; and with no policy, row level security shows them to no one."""
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
                " AND (has_table_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES')"
                " OR has_any_column_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))"
            ),
            {"o": OPERATOR, "a": APP_ROLE, "t": sorted(OPERATOR_TABLES)},
        ).all()
        locked = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policy WHERE polrelid = c.oid)"
                " FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(:t)"
            ),
            {"t": sorted(OPERATOR_TABLES)},
        ).all()
    assert (tuple(role), granted) == ((False, False, False), [])
    assert sorted(tuple(row) for row in locked) == [("operators", True, 0), ("platform_audit", True, 0)]
