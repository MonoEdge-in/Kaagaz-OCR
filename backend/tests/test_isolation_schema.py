"""Every customer table, found from the catalog, has both locks and the app's grants.

A table is a customer table when it has a ``customer_id`` column. Finding them
from the catalog means a new table can't be left out of this check: it needs
row level security, the ``customer_only`` policy with USING and WITH CHECK, and
grants to the app role, which must not own it (an owner skips the policy).
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
# (SELECT, INSERT, UPDATE, DELETE) for the app role. Lines are only inserted (the
# invoice's delete cascades to them), and runs are never deleted (amendment A9).
FROM_0004 = {
    "invoices": (True, True, True, True),
    "invoice_lines": (True, True, False, False),
    "document_links": (True, True, True, True),
    "processing_runs": (True, True, True, False),
}
# The only tables without customer_id: the customers themselves, and alembic's own.
NO_CUSTOMER_ID = {"customers", "alembic_version"}

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
    assert set(tables) >= FROM_0001 | FROM_0002 | set(FROM_0004)

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
    for table, expected in FROM_0004.items():
        assert granted[table] == expected, table


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
