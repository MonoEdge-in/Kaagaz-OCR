"""#479 (0067), #587 (0083): the three limited logins can do nothing by themselves.

``kaagaz_api`` (the API, and the workers until a server names theirs) may only switch into
``kaagaz_app``, the organisation mode; ``kaagaz_ops`` (the API's super admin part) only into
``kaagaz_operator``; ``kaagaz_jobs`` (the workers') only into ``kaagaz_worker``, which holds
exactly ``kaagaz_app``'s rights. None inherits a right, so a transaction that forgot
``SET LOCAL ROLE`` reads nothing. None may switch into the owner or another mode, run a program
on the server, read its files or make a table (temporary or in ``public``) or a function. The
app may only add to an audit log, and every SECURITY DEFINER function has the empty search path
and is not open to the public. Each login gets a password made up here for the session (the
migrations set none); it is removed again at the end.
"""

import logging
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest import mock

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from kaagaz import worker_main
from kaagaz.api import app as app_module
from kaagaz.api.app import create_app
from kaagaz.cli import main
from kaagaz.db import person_rule
from kaagaz.db.engine import Database, OperatorDatabase, make_engine
from kaagaz.db.sqlstate import sqlstate
from kaagaz.db.storage_limits import GIB
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
from tests.support import TEXT_BYTES
from tests.test_api_folder_upload_files import ResumableStorage
from tests.test_api_folder_uploads import TREE, item, plan, register
from tests.test_api_storage_limit import _send_all as send_all
from tests.test_api_support import CSRF, PDF_BYTES, Api, ApiFakeQueue, ApiFakeStorage, FakeAnswers, TestClient
from tests.test_indexing_db import FakeModel
from tests.test_isolation_customer_purge import KeyStorage
from tests.test_migration_0023_db import new_operator
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

API, OPS, OPERATOR = "kaagaz_api", "kaagaz_ops", "kaagaz_operator"
JOBS, WORKER = "kaagaz_jobs", "kaagaz_worker"
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
        for role in (API, OPS, JOBS):
            password = secrets.token_hex(16)
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
            )
            urls[role] = owner.set(username=role, password=password).render_as_string(hide_password=False)
    yield urls
    with psycopg.connect(_owner_url(), autocommit=True) as conn:
        for role in (API, OPS, JOBS):
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
    assert _state(logins[JOBS], statement) == REFUSED
    assert _state(logins[JOBS], f"SET ROLE {WORKER}", "RESET ROLE", statement) == REFUSED


def test_the_switch_works_and_reads_only_with_a_customer(logins: dict[str, str]) -> None:
    assert _state(logins[API], f"SET ROLE {APP_ROLE}", "SELECT count(*) FROM documents") is None
    assert (
        _state(logins[OPS], f"SET ROLE {OPERATOR}", "SELECT * FROM kaagaz_op_session(gen_random_uuid())")
        is None
    )
    assert _state(logins[JOBS], f"SET ROLE {WORKER}", "SELECT count(*) FROM documents") is None


def test_no_login_switches_into_the_owner_or_another_mode(logins: dict[str, str]) -> None:
    owner = make_url(_owner_url()).username
    assert owner
    for login, others in (
        (API, (OPERATOR, WORKER, owner, OPS, JOBS)),
        (OPS, (APP_ROLE, WORKER, owner, API, JOBS)),
        (JOBS, (APP_ROLE, OPERATOR, owner, API, OPS)),
    ):
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
    assert _state(logins[JOBS], f"SET ROLE {WORKER}", statement) == REFUSED


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
            for login in (API, OPS, JOBS)
        }
    assert reach == {API: {API, APP_ROLE}, OPS: {OPS, OPERATOR}, JOBS: {JOBS, WORKER}}


def test_each_login_is_limited_and_holds_one_mode(db: Database) -> None:
    with db.engine.begin() as conn:  # the catalog, as the owner
        roles = conn.execute(
            text(
                "SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,"
                " rolreplication, rolbypassrls, rolconnlimit > 0 FROM pg_roles"
                " WHERE rolname IN ('kaagaz_api', 'kaagaz_jobs', 'kaagaz_ops') ORDER BY rolname"
            )
        ).all()
        modes = conn.execute(
            text(
                "SELECT m.rolname, r.rolname, a.inherit_option, a.set_option, a.admin_option"
                " FROM pg_auth_members a JOIN pg_roles m ON m.oid = a.member"
                " JOIN pg_roles r ON r.oid = a.roleid"
                " WHERE m.rolname IN ('kaagaz_api', 'kaagaz_jobs', 'kaagaz_ops') ORDER BY m.rolname"
            )
        ).all()
    limited = (True, False, False, False, False, False, False, True)
    assert [tuple(row) for row in roles] == [(API, *limited), (JOBS, *limited), (OPS, *limited)]
    assert [tuple(row) for row in modes] == [
        (API, APP_ROLE, False, True, False),
        (JOBS, WORKER, False, True, False),
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
                " WHERE r.rolname IN ('kaagaz_api', 'kaagaz_jobs', 'kaagaz_ops')"
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
    """A representative request and worker path, with no owner login except the sign-in's Keycloak
    authority (the Keycloak callback can't run in tests): the session is minted, a new
    customer (kaagaz_create_customer), a document listed, the worker's advisory locks
    (no switch: they need no right) and the health ping; and the super admin's switch on its own login."""
    # Changed on purpose (#737, 21 Nov 2026): the session is minted, not signed in through a route; only
    # its Keycloak authority is written as the owner, because the Keycloak callback can't run in tests.
    app_engine, ops_engine = make_engine(logins[API]), make_engine(logins[OPS])
    try:
        app_db = Database(app_engine, APP_ROLE)
        api = Api(app_db)
        signed = api.new_signed("member")
        # Changed on purpose (#606): written as the person; under the database rule the app role with
        # no person writes no file.
        with app_db.user(signed.customer_id, signed.user_id) as conn:
            document = insert_document(conn, signed.customer_id, uploaded_by=signed.user_id)
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


def test_a_folder_upload_plans_registers_and_sends_under_the_app_login(logins: dict[str, str]) -> None:
    """#484 under #479: the folder upload needs no owner right. Plan (the plan lock, the tree lock,
    the limits), register a page of items and send a file to its item, all on ``kaagaz_api``; the
    file lands in its folder and the progress says so."""
    engine = make_engine(logins[API])
    try:
        api = Api(Database(engine, APP_ROLE))
        signed = api.new_signed("member")
        planned = plan(signed, TREE, file_count=1, total_bytes=len(PDF_BYTES))
        assert planned.status_code == 200, planned.text
        upload, deepest = planned.json()["id"], planned.json()["folder_ids"][2]
        registered = register(signed, upload, [item("A/B/C/bill.pdf", deepest, len(PDF_BYTES))])
        assert registered.status_code == 200, registered.text
        (one,) = registered.json()["items"]
        sent = signed.client.post(
            f"/api/documents?folder_upload_item_id={one['id']}",
            files={"file": ("bill.pdf", PDF_BYTES, "application/pdf")},
            headers=CSRF,
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["document"]["folder_id"] == deepest
        progress = signed.client.get(f"/api/folder-uploads/{upload}").json()
        assert (progress["stored"], progress["waiting"], progress["reading"]) == (1, 0, 1)
    finally:
        engine.dispose()


def test_the_storage_limit_is_read_and_enforced_under_the_two_logins(
    db: Database, logins: dict[str, str]
) -> None:
    """#516 (0061d, merged by 0075) under #479: no owner right but the sign-in's Keycloak authority.
    The super admin's login reads one organisation's limit and the bytes it holds
    (``kaagaz_op_storage_limit``); on the app's login its admin reads the same, a file of exactly
    the bytes left goes in, the next is refused ``storage_full`` at the upload, the resumable complete
    and the folder upload plan, and a duplicate is still answered. The limit is written as the owner
    here: its write needs a fresh code (test_operator_storage_limit_db)."""
    # Changed on purpose (#737, 21 Nov 2026): only the minted session's Keycloak authority is written as the
    # owner, because the Keycloak callback can't run in tests.
    app_engine, ops_engine = make_engine(logins[API]), make_engine(logins[OPS])
    try:
        api = Api(Database(app_engine, APP_ROLE), storage=ResumableStorage())
        admin = api.new_admin()
        customer = admin.customer_id
        _owner(db, "INSERT INTO org_storage_limits (customer_id, storage_gb) VALUES (:c, 1)", {"c": customer})
        with db.customer(customer) as conn:
            insert_document(conn, customer, size_bytes=GIB - len(PDF_BYTES))
        with OperatorDatabase(ops_engine).operator() as conn:
            read = text("SELECT * FROM kaagaz_op_storage_limit(:o, :c)")
            row = conn.execute(read, {"o": new_operator(db), "c": customer}).one()
        assert tuple(row) == (1, None, GIB - len(PDF_BYTES))
        assert admin.client.get("/api/admin/storage").json() == {
            "used_bytes": GIB - len(PDF_BYTES),
            "limit_bytes": GIB,
            "limit_gb": 1,
        }
        pdf = {"file": ("bill.pdf", PDF_BYTES, "application/pdf")}
        assert admin.client.post("/api/documents", files=pdf, headers=CSRF).status_code == 201
        refused = [
            admin.client.post(
                "/api/documents", files={"file": ("b.csv", TEXT_BYTES, "text/csv")}, headers=CSRF
            ),
            send_all(admin, TEXT_BYTES),  # the resumable start, a part, then its complete
            plan(admin, TREE, file_count=1, total_bytes=1),
        ]
        assert [(reply.status_code, reply.json()["error"]["code"]) for reply in refused] == [
            (413, "storage_full")
        ] * 3, [reply.text for reply in refused]
        again = admin.client.post("/api/documents", files=pdf, headers=CSRF)
        assert (again.status_code, again.json()["duplicate"]) == (200, True)
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


def test_unsafe_settings_never_stop_the_start_and_the_operator_sees_them(
    db: Database, logins: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """A6 (stability pass, 7 Nov 2026): at start-up a setting unsafe on a server is logged and
    alerted, and the operator's Health page shows it; the API starts all the same (a refusal could
    take the test server down on a deploy). The limited app login is no superuser; the owner is."""
    with db.engine.connect() as conn:
        owner = person_rule.login_risks(conn)  # Changed on purpose (#606): every way past the rule
    for url, expected in (
        (logins[API], ["eicar_scanner"]),
        (_owner_url(), ["eicar_scanner", *owner]),
    ):
        settings = make_settings(database_url=url, ops_database_url=logins[OPS], scanner="eicar_dev")
        app = create_app(settings, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers())
        with caplog.at_level(logging.ERROR), TestClient(app) as client:
            assert client.get("/api/health").json() == {"status": "ok"}
            assert app.state.kaagaz.unsafe_config == expected
    said = [r for r in caplog.records if r.getMessage() in app_module.UNSAFE_SAID.values()]
    assert len(said) == 2 + len(owner)


def test_with_db_login_check_refuse_only_a_login_the_rule_holds_starts(
    logins: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """PRD 7 (#606, decision 22): DB_LOGIN_CHECK=refuse stops the API on the owner's login (it owns
    the tables and can switch into kaagaz_worker), with one critical line; the limited one starts."""
    refuse = {"scanner": "clamav", "db_login_check": "refuse"}
    owner = make_settings(database_url=_owner_url(), ops_database_url=logins[OPS], **refuse)
    app = create_app(owner, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers())
    with (
        caplog.at_level(logging.CRITICAL),
        pytest.raises(RuntimeError, match="owns the tables"),
        TestClient(app),
    ):
        pass
    codes = [getattr(r, "code", None) for r in caplog.records]
    assert codes.count("login_refused") == 1
    limited = make_settings(database_url=logins[API], ops_database_url=logins[OPS], **refuse)
    with TestClient(
        create_app(limited, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers())
    ):
        pass


def test_a_database_it_cannot_reach_is_said_as_such_never_as_a_login_to_fix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test server, 4 Nov 2026: postgres left on the old network made the start-up check blame
    OPS_DATABASE_URL, which was never set. No answer from the database is its own plain line."""
    nowhere = "postgresql://kaagaz:unused@127.0.0.1:1/kaagaz"
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="cannot reach the database"):
        _start_api(nowhere, nowhere)
    said = [r.getMessage() for r in caplog.records if getattr(r, "code", None) == "database_unreachable"]
    assert len(said) == 1 and "OPS_DATABASE_URL" not in said[0]


class _Queue:
    """The upgrade sweep's queue: the jobs it was given."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def enqueue(self, job: Job, *, express: bool = False) -> None:
        self.jobs.append(job)


def _held_and_binned(db: Database) -> tuple[str, str, str, str]:
    """A new organisation, its admin, a document the reading pause holds and one 60 days in Trash."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        held = insert_document(conn, customer, status="received")
        conn.execute(text("UPDATE documents SET reading_held = 'daily_limit' WHERE id = :d"), {"d": held})
        binned = insert_document(
            conn, customer, deleted_at=datetime.now(UTC) - timedelta(days=60), deleted_by=user
        )
    return customer, user, held, binned


def _sweep(worker: Database, customer: str) -> list[Job]:
    """Every sweep the worker runs between jobs (worker_main._serve), on ``worker``'s login and in
    its role; the jobs the reading pause released."""
    assert worker_main._purge(worker, KeyStorage(), 30) >= 1
    worker_main.fingerprint_missing(worker)
    embed_missing(worker, FakeModel(), None, limit=1)
    embed_missing_stories(worker, FakeModel(), limit=1)
    upgrade_oldest(worker, _Queue())
    # Only this test's organisation is looked at: every other one is "busy".
    revive_stuck(worker, lambda job, tries: None, customer.__ne__, stale_seconds=3600, max_attempts=5)
    released: list[Job] = []
    release_held(worker, released.append, now=datetime.now(UTC))
    last_refreshed(worker)
    with sweep_lock(worker.engine)(f"test:{uuid.uuid4()}") as mine:
        assert mine
    return released


def test_the_worker_sweeps_run_under_the_app_login(db: Database, logins: dict[str, str]) -> None:
    """QA missing test e: every sweep the worker runs between jobs (worker_main._serve) works on
    the app's login alone: the hourly purge (Trash, deleted organisations, kept audit rows), the
    fingerprints, the vectors, the upgrade, the stuck documents, the reading pause's release, the
    catalogue's last refresh and the sweep lock. A held document is released and an old Trash
    family purged, so their writes ran too."""
    customer, _, held, binned = _held_and_binned(db)
    engine = make_engine(logins[API])
    try:
        released = _sweep(Database(engine, APP_ROLE), customer)
    finally:
        engine.dispose()
    assert Job(customer, held) in released
    with db.customer(customer) as conn:
        left = conn.execute(text("SELECT id FROM documents WHERE id = :d"), {"d": binned}).all()
    assert left == []


def test_the_workers_run_in_their_own_role_on_their_own_login(db: Database, logins: dict[str, str]) -> None:
    """#587 (0083): the same sweeps on ``kaagaz_jobs`` in ``kaagaz_worker``, and a worker's work
    for one person: an Ensemble run or a requested reading takes its model slot in
    ``Database.user``, as the person who asked (kaagaz_model_rate_slot refuses any other)."""
    customer, user, held, _ = _held_and_binned(db)
    engine = make_engine(logins[JOBS])
    try:
        worker = Database(engine, WORKER)
        released = _sweep(worker, customer)
        worker.model_rate_slot(str(uuid.uuid4()), customer, user, 0.0)
    finally:
        engine.dispose()
    assert Job(customer, held) in released


def test_with_db_login_check_refuse_the_worker_stops_before_any_job() -> None:
    """PRD 7 (#606): _serve checks its login before it builds storage or takes a job."""
    settings = make_settings(database_url=_owner_url(), db_login_check="refuse", db_worker_role=APP_ROLE)
    with (
        mock.patch.object(worker_main, "S3Storage", side_effect=AssertionError("a job's set-up ran")),
        pytest.raises(RuntimeError, match="DB_LOGIN_CHECK"),
    ):
        worker_main._serve(settings)


def test_with_db_login_check_refuse_a_worker_starts_only_as_kaagaz_worker_on_its_login(
    db: Database, logins: dict[str, str]
) -> None:
    """PRD 7 (#606, decision 22): the worker's start-up check passes kaagaz_jobs in kaagaz_worker;
    under refuse it stops the owner's login, or the app role; under warn it only logs."""
    engine = make_engine(logins[JOBS])
    try:
        worker_main.check_worker_login("refuse", WORKER, Database(engine, WORKER))
    finally:
        engine.dispose()
    for role in (WORKER, APP_ROLE):
        with pytest.raises(RuntimeError, match="DB_LOGIN_CHECK"):
            worker_main.check_worker_login("refuse", role, Database(db.engine, role))
        worker_main.check_worker_login("warn", role, Database(db.engine, role))


def test_the_worker_role_is_held_by_the_organisation_rule(db: Database, logins: dict[str, str]) -> None:
    """Every row level security policy is for PUBLIC, so ``kaagaz_worker`` sees one organisation's
    rows and none with no organisation set, as ``kaagaz_app`` does."""
    mine, other = new_customer(db)[0], new_customer(db)[0]
    with db.customer(mine) as conn:
        document = insert_document(conn, mine)
    with db.customer(other) as conn:
        insert_document(conn, other)
    engine = make_engine(logins[JOBS])
    try:
        worker = Database(engine, WORKER)
        with worker.customer(mine) as conn:
            seen = [str(row) for row in conn.execute(text("SELECT id FROM documents")).scalars()]
        with worker.system() as conn:
            unset = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
    finally:
        engine.dispose()
    assert (seen, unset) == ([document], 0)


# Every right a role holds in this database, wherever one can be granted: (kind, object, right,
# grantable). Tables, views and sequences; columns; functions; schemas; the database; types;
# default privileges; server parameters.
_RIGHTS = text(
    "SELECT x.kind, x.name, a.privilege_type, a.is_grantable FROM ("
    " SELECT 'relation', oid::regclass::text, relacl FROM pg_class"
    " UNION ALL SELECT 'column', attrelid::regclass::text || '.' || quote_ident(attname), attacl"
    "  FROM pg_attribute WHERE attacl IS NOT NULL"
    " UNION ALL SELECT 'function', oid::regprocedure::text, proacl FROM pg_proc"
    " UNION ALL SELECT 'schema', nspname::text, nspacl FROM pg_namespace"
    " UNION ALL SELECT 'database', datname::text, datacl FROM pg_database WHERE datname = current_database()"
    " UNION ALL SELECT 'type', oid::regtype::text, typacl FROM pg_type"
    " UNION ALL SELECT 'default ' || defaclobjtype::text, defaclrole::regrole::text || ' in '"
    "  || defaclnamespace::regnamespace::text, defaclacl FROM pg_default_acl"
    " UNION ALL SELECT 'parameter', parname, paracl FROM pg_parameter_acl"
    ") x(kind, name, acl), aclexplode(x.acl) a WHERE a.grantee = CAST(:role AS regrole)"
)
# The role itself: its attributes and the roles it is a member of.
_ROLE = text(
    "SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls,"
    " ARRAY(SELECT roleid::regrole::text FROM pg_auth_members WHERE member = r.oid ORDER BY 1)"
    " FROM pg_roles r WHERE rolname = :role"
)


def test_the_worker_role_has_exactly_the_app_roles_rights(db: Database) -> None:
    """#587 (0083): ``kaagaz_worker`` may do exactly what ``kaagaz_app`` may, everywhere. A later
    migration that grants to one and forgets the other fails here: grant to both (PRD 7)."""
    with db.engine.begin() as conn:  # the catalog, as the owner
        both = (APP_ROLE, WORKER)
        rights = {role: {tuple(row) for row in conn.execute(_RIGHTS, {"role": role})} for role in both}
        roles = {role: tuple(conn.execute(_ROLE, {"role": role}).one()) for role in both}
    assert len(rights[APP_ROLE]) > 100  # the query still finds them
    assert sorted(rights[APP_ROLE] ^ rights[WORKER]) == []
    assert roles[WORKER] == roles[APP_ROLE] == (False, True, False, False, False, False, False, [])
