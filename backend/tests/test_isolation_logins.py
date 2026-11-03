"""#479 (0067): the two limited logins can do nothing by themselves.

``kaagaz_api`` (the API and the workers) may only switch into ``kaagaz_app``, the organisation
mode; ``kaagaz_ops`` (the API's super admin part) only into ``kaagaz_operator``. Neither
inherits a right, so a transaction that forgot ``SET LOCAL ROLE`` reads nothing. Neither may
switch into the owner or the other mode, run a program on the server, read its files or make
a temporary table. The app may only add to an audit log, and every SECURITY DEFINER function
has the empty search path and is not open to the public. Each login gets a password made up
here for the session (the migration sets none); it is removed again at the end.
"""

import secrets
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from kaagaz.cli import main
from kaagaz.db.engine import Database, OperatorDatabase, make_engine
from kaagaz.db.sqlstate import sqlstate
from kaagaz.jobs.fair_share import first_free_lock
from tests.db_support import APP_ROLE, database_url, new_customer
from tests.test_api_support import Api

pytestmark = pytest.mark.db

API, OPS, OPERATOR = "kaagaz_api", "kaagaz_ops", "kaagaz_operator"
REFUSED = "42501"  # insufficient privilege
READS = (
    "SELECT count(*) FROM documents",
    "SELECT count(*) FROM customers",
    "SELECT count(*) FROM platform_audit",
    "SELECT * FROM kaagaz_login_lookup('someone@example.test')",
    "SELECT * FROM kaagaz_op_session(gen_random_uuid())",
)
SERVER = (
    "COPY (SELECT 1) TO PROGRAM 'true'",
    "SELECT pg_read_file('postgresql.conf')",
    "SELECT pg_ls_dir('.')",
    "CREATE TEMP TABLE scratch (x int)",
)


def _owner_url() -> str:
    return make_url(database_url()).set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def logins(db: Database) -> Iterator[dict[str, str]]:
    """Each login's URL, with a password set for this test session only."""
    owner = make_url(_owner_url())
    urls = {}
    with psycopg.connect(_owner_url(), autocommit=True) as conn:
        for role in (API, OPS):
            password = secrets.token_hex(16)
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
            )
            urls[role] = owner.set(username=role, password=password).render_as_string(hide_password=False)
    yield urls
    with psycopg.connect(_owner_url(), autocommit=True) as conn:
        for role in (API, OPS):
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD NULL").format(sql.Identifier(role)))


def _state(url: str, *statements: str) -> str | None:
    """Run ``statements`` in one transaction as the login in ``url``: the SQLSTATE of the one
    that failed, None when all ran."""
    with psycopg.connect(url) as conn:
        try:
            for statement in statements:
                conn.execute(statement)
        except psycopg.Error as error:
            return error.sqlstate
    return None


@pytest.mark.parametrize("statement", READS)
def test_the_app_login_without_the_switch_reads_nothing(logins: dict[str, str], statement: str) -> None:
    assert _state(logins[API], statement) == REFUSED
    assert _state(logins[API], f"SET ROLE {APP_ROLE}", "RESET ROLE", statement) == REFUSED
    assert _state(logins[OPS], statement) == REFUSED


def test_the_switch_works_and_reads_only_with_a_customer(logins: dict[str, str]) -> None:
    assert _state(logins[API], f"SET ROLE {APP_ROLE}", "SELECT count(*) FROM documents") is None
    assert (
        _state(logins[OPS], f"SET ROLE {OPERATOR}", "SELECT * FROM kaagaz_op_session(gen_random_uuid())")
        is None
    )


def test_neither_login_switches_into_the_owner_or_the_other_mode(logins: dict[str, str]) -> None:
    owner = make_url(_owner_url()).username
    assert owner
    for login, others in ((API, (OPERATOR, owner, OPS)), (OPS, (APP_ROLE, owner, API))):
        for other in others:
            assert _state(logins[login], f"SET ROLE {other}") == REFUSED, (login, other)
        assert _state(logins[login], f"SET SESSION AUTHORIZATION {owner}") == REFUSED, login


@pytest.mark.parametrize("statement", SERVER)
def test_no_program_no_server_files_and_no_temporary_table(logins: dict[str, str], statement: str) -> None:
    assert _state(logins[API], statement) == REFUSED
    assert _state(logins[API], f"SET ROLE {APP_ROLE}", statement) == REFUSED
    assert _state(logins[OPS], f"SET ROLE {OPERATOR}", statement) == REFUSED


def test_each_login_is_limited_and_holds_one_mode(db: Database) -> None:
    with db.engine.begin() as conn:  # the catalog, as the owner
        roles = conn.execute(
            text(
                "SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,"
                " rolreplication, rolbypassrls, rolconnlimit > 0 FROM pg_roles"
                " WHERE rolname IN ('kaagaz_api', 'kaagaz_ops') ORDER BY rolname"
            )
        ).all()
        modes = conn.execute(
            text(
                "SELECT m.rolname, r.rolname, a.inherit_option, a.set_option, a.admin_option"
                " FROM pg_auth_members a JOIN pg_roles m ON m.oid = a.member"
                " JOIN pg_roles r ON r.oid = a.roleid"
                " WHERE m.rolname IN ('kaagaz_api', 'kaagaz_ops') ORDER BY m.rolname"
            )
        ).all()
    limited = (True, False, False, False, False, False, False, True)
    assert [tuple(row) for row in roles] == [(API, *limited), (OPS, *limited)]
    assert [tuple(row) for row in modes] == [
        (API, APP_ROLE, False, True, False),
        (OPS, OPERATOR, False, True, False),
    ]


def test_no_right_is_granted_to_a_login_itself(db: Database) -> None:
    """Every right comes through the switch: nothing in ``public`` names a login."""
    with db.engine.begin() as conn:
        named = conn.execute(
            text(
                "SELECT x.name FROM ("
                " SELECT relname AS name, relacl AS acl FROM pg_class"
                " WHERE relnamespace = 'public'::regnamespace"
                " UNION ALL SELECT proname, proacl FROM pg_proc WHERE pronamespace = 'public'::regnamespace"
                ") x, aclexplode(x.acl) a JOIN pg_roles r ON r.oid = a.grantee"
                " WHERE r.rolname IN ('kaagaz_api', 'kaagaz_ops')"
            )
        ).all()
    assert named == []


def test_every_security_definer_function_has_the_empty_path_and_no_public_execute(db: Database) -> None:
    with db.engine.begin() as conn:
        unsafe = conn.execute(
            text(
                "SELECT p.proname FROM pg_proc p"
                " WHERE p.pronamespace = 'public'::regnamespace AND p.prosecdef AND ("
                "   NOT coalesce(p.proconfig @> ARRAY['search_path=\"\"'], false)"
                "   OR p.proacl IS NULL"  # no ACL: PUBLIC may run it
                "   OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0)"
                " ) ORDER BY 1"
            )
        ).all()
        count = conn.execute(
            text("SELECT count(*) FROM pg_proc WHERE pronamespace = 'public'::regnamespace AND prosecdef")
        ).scalar_one()
    assert unsafe == []
    assert count > 2  # 0001's two and every later one were checked


def test_the_app_only_adds_to_the_audit_logs(db: Database) -> None:
    with db.engine.begin() as conn:
        rights = conn.execute(
            text(
                "SELECT relname, has_table_privilege(:r, oid, 'INSERT'),"
                " has_table_privilege(:r, oid, 'UPDATE') OR has_any_column_privilege(:r, oid, 'UPDATE'),"
                " has_table_privilege(:r, oid, 'DELETE'), has_table_privilege(:r, oid, 'TRUNCATE')"
                " FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
                " AND relname LIKE '%audit%' ORDER BY relname"
            ),
            {"r": APP_ROLE},
        ).all()
    # audit_log: the organisation's, added to by the app; platform_audit: the operator
    # functions' (SECURITY DEFINER) and the CLI's alone.
    assert [tuple(row) for row in rights] == [
        ("audit_log", True, False, False, False),
        ("platform_audit", False, False, False, False),
    ]


def test_the_app_works_under_its_own_login(logins: dict[str, str]) -> None:
    """A representative request and worker path, with no owner login anywhere: sign-in
    (kaagaz_login_lookup), a new customer (kaagaz_create_customer), a document listed, the
    worker's advisory locks (no switch: they need no right) and the health ping; and the
    super admin's switch on its own login."""
    app_engine, ops_engine = make_engine(logins[API]), make_engine(logins[OPS])
    try:
        app_db = Database(app_engine, APP_ROLE)
        api = Api(app_db)
        signed = api.new_signed("member")
        document = api.add_document(signed.customer_id, uploaded_by=signed.user_id)
        listed = signed.client.get("/api/documents")
        assert listed.status_code == 200 and document in listed.text, listed.text
        with first_free_lock(app_engine, [f"kaagaz.test:{uuid.uuid4()}"]) as held:
            assert held is not None
        app_db.ping()
        with OperatorDatabase(ops_engine).operator() as conn:
            assert conn.execute(text("SELECT * FROM kaagaz_op_session(gen_random_uuid())")).all() == []
    finally:
        app_engine.dispose()
        ops_engine.dispose()


def test_an_owner_command_is_refused_under_the_app_login(db: Database, logins: dict[str, str]) -> None:
    """Why docs/running-locally.md runs them in migrate: in api, PostgreSQL refuses them."""
    customer, _ = new_customer(db)
    engine = make_engine(logins[API])
    try:
        with pytest.raises(DBAPIError) as refused:
            main(["disable-customer", "--customer-id", customer], db=Database(engine, APP_ROLE), environ={})
    finally:
        engine.dispose()
    assert sqlstate(refused.value) == REFUSED
    with db.customer(customer) as conn:
        assert conn.execute(text("SELECT status FROM customers")).scalar_one() == "active"  # unchanged
