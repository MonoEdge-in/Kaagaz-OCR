"""#479 (0067): the two limited logins can do nothing by themselves.

``kaagaz_api`` (the API and the workers) may only switch into ``kaagaz_app``, the organisation
mode; ``kaagaz_ops`` (the API's super admin part) only into ``kaagaz_operator``. Neither
inherits a right, so a transaction that forgot ``SET LOCAL ROLE`` reads nothing. Neither may
switch into the owner or the other mode, run a program on the server, read its files or make
a table (temporary or in ``public``) or a function. The app may only add to an audit log, and
every SECURITY DEFINER function has the empty search path and is not open to the public. Each
login gets a password made up here for the session (the migration sets none); it is removed
again at the end.
"""

import logging
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from kaagaz import worker_main
from kaagaz.api.app import create_app
from kaagaz.cli import main
from kaagaz.db.engine import Database, OperatorDatabase, make_engine
from kaagaz.db.sqlstate import sqlstate
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.catalog import last_refreshed
from kaagaz.jobs.fair_share import first_free_lock, sweep_lock
from kaagaz.jobs.reading_pause import release_held
from kaagaz.jobs.revive import revive_stuck
from kaagaz.jobs.upgrade import upgrade_oldest
from kaagaz.search.indexing import embed_missing
from kaagaz.search.story_vectors import embed_missing_stories
from tests.db_support import APP_ROLE, database_url, insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import Api, ApiFakeQueue, ApiFakeStorage, FakeAnswers, TestClient
from tests.test_indexing_db import FakeModel
from tests.test_isolation_customer_purge import KeyStorage

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
    "CREATE TABLE public.scratch (x int)",
    "CREATE FUNCTION public.scratch() RETURNS int LANGUAGE sql AS 'SELECT 1'",
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
def test_no_program_no_server_files_and_no_new_table_or_function(
    logins: dict[str, str], statement: str
) -> None:
    assert _state(logins[API], statement) == REFUSED
    assert _state(logins[API], f"SET ROLE {APP_ROLE}", statement) == REFUSED
    assert _state(logins[OPS], f"SET ROLE {OPERATOR}", statement) == REFUSED


def test_each_login_can_switch_into_only_its_mode(db: Database) -> None:
    """Every role a login may SET ROLE to, through any chain of grants: itself and its mode."""
    with db.engine.begin() as conn:
        reach = {
            login: set(
                conn.execute(
                    text("SELECT rolname FROM pg_roles WHERE pg_has_role(:login, oid, 'SET')"),
                    {"login": login},
                ).scalars()
            )
            for login in (API, OPS)
        }
    assert reach == {API: {API, APP_ROLE}, OPS: {OPS, OPERATOR}}


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
    # audit_log: the organisation's, added to by the app; audit_log_kept (0068): a deleted
    # organisation's, the owner's alone (the worker's sweep is SECURITY DEFINER);
    # platform_audit: the operator functions' (SECURITY DEFINER) and the CLI's alone.
    assert [tuple(row) for row in rights] == [
        ("audit_log", True, False, False, False),
        ("audit_log_kept", False, False, False, False),
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


def _start_api(database_url: str, ops_database_url: str) -> None:
    """Start the API as uvicorn does (its lifespan), with these two logins, and stop it."""
    settings = make_settings(database_url=database_url, ops_database_url=ops_database_url)
    app = create_app(settings, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers())
    with TestClient(app):
        pass


def test_the_api_refuses_to_start_when_its_operator_login_cannot_switch(
    db: Database, logins: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """QA W1: APP_DATABASE_URL set and OPS_DATABASE_URL not leaves the operator part on the app's
    login, which cannot switch into kaagaz_operator. The API stops at start-up with one plain
    line, instead of a console that fails while /api/health stays green."""
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="OPS_DATABASE_URL"):
        _start_api(logins[API], logins[API])
    said = [record for record in caplog.records if getattr(record, "code", None) == "operator_login_failed"]
    assert len(said) == 1 and said[0].getMessage().endswith(f"Limited database logins) ({REFUSED})")
    with db.engine.begin() as conn:  # the refused start closed its connections
        opened = "SELECT count(*) FROM pg_stat_activity WHERE usename = :login"
        assert conn.execute(text(opened), {"login": API}).scalar_one() == 0
    _start_api(logins[API], logins[OPS])  # both logins set: it starts
    _start_api(_owner_url(), _owner_url())  # release day, the owner's for both: it starts


def test_a_database_it_cannot_reach_is_said_as_such_never_as_a_login_to_fix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test server, 4 Nov 2026: postgres left on the old network made the start-up check blame
    OPS_DATABASE_URL, which was never set. No answer from the database is its own plain line."""
    nowhere = "postgresql://kaagaz:unused@127.0.0.1:1/kaagaz"
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="cannot reach the database"):
        _start_api(nowhere, nowhere)
    said = [record.getMessage() for record in caplog.records if getattr(record, "code", None) == "database_unreachable"]
    assert len(said) == 1 and "OPS_DATABASE_URL" not in said[0]


class _Queue:
    """The upgrade sweep's queue: the jobs it was given."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def enqueue(self, job: Job, *, express: bool = False) -> None:
        self.jobs.append(job)


def test_the_worker_sweeps_run_under_the_app_login(db: Database, logins: dict[str, str]) -> None:
    """QA missing test e: every sweep the worker runs between jobs (worker_main._serve) works on
    the app's login alone: the hourly purge (Trash, deleted organisations, kept audit rows), the
    fingerprints, the vectors, the upgrade, the stuck documents, the reading pause's release, the
    catalogue's last refresh and the sweep lock. A held document is released and an old Trash
    family purged, so their writes ran too."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        held = insert_document(conn, customer, status="received")
        conn.execute(text("UPDATE documents SET reading_held = 'daily_limit' WHERE id = :d"), {"d": held})
        binned = insert_document(
            conn, customer, deleted_at=datetime.now(UTC) - timedelta(days=60), deleted_by=user
        )
    engine = make_engine(logins[API])
    try:
        app_db = Database(engine, APP_ROLE)
        assert worker_main._purge(app_db, KeyStorage(), 30) >= 1
        worker_main.fingerprint_missing(app_db)
        embed_missing(app_db, FakeModel(), None, limit=1)
        embed_missing_stories(app_db, FakeModel(), limit=1)
        upgrade_oldest(app_db, _Queue())
        # Only this test's organisation is looked at: every other one is "busy".
        revive_stuck(app_db, lambda job, tries: None, customer.__ne__, stale_seconds=3600, max_attempts=5)
        released: list[Job] = []
        release_held(app_db, released.append, now=datetime.now(UTC))
        last_refreshed(app_db)
        with sweep_lock(engine)(f"test:{uuid.uuid4()}") as mine:
            assert mine
    finally:
        engine.dispose()
    assert Job(customer, held) in released
    with db.customer(customer) as conn:
        left = conn.execute(text("SELECT id FROM documents WHERE id = :d"), {"d": binned}).all()
    assert left == []
