"""Organisation status (0014, super admin PR 1, council 6 Oct 2026) against PostgreSQL.

* The migration: up, down (0013's schema exactly) and up again, with each new
  column's comment; the downgrade refuses while a customer is not active.
* The grant fix: as ``kaagaz_app`` with the customer set, changing any column of
  its customers row is refused (42501), except ``max_upload_mb``; the upload
  limit and the folder tree's row lock still work. The CHECKs keep the three
  columns together.
* Sign-in and sessions: a disabled customer's users get the one 401 (a right
  password, a right code and a temporary password alike, audited as
  ``customer_disabled``) and their sessions 401, while customer B goes on;
  after enabling, everyone signs in again.
* The worker's real check fails A's job with ``customer_disabled`` and reads B's;
  the two vector sweeps and ``cli reembed`` pass A over until it is enabled.
* The CLI: disable-customer and enable-customer, one audit row each, a customer
  being deleted, an unknown or malformed id.
"""

import io
import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from kaagaz.cli import main
from kaagaz.db import admin as admin_sql
from kaagaz.db import customers
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.folders import _LOCK_TREE
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.sqlstate import sqlstate
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import DocumentProcessor
from kaagaz.scanning.gate import ScanGate, Verdict
from kaagaz.search.indexing import embed_missing
from kaagaz.search.story_vectors import StoryPass, embed_missing_stories
from tests import test_migration_0002_db as m0002
from tests.db_support import APP_ROLE, insert_document, migrate, new_customer, violation
from tests.fakes import (
    FakePreparer,
    FakeScanner,
    MemoryPageImages,
    MemoryQueue,
    MemoryStorage,
    fake_sheet_pages,
)
from tests.test_api_support import CSRF, PASSWORD, Api, TestClient
from tests.test_document_stories_db import put
from tests.test_embed_missing_db import CURRENT, SameVector, drain, tags
from tests.test_indexing_db import FakeModel
from tests.test_migration_0004_db import _current, _head
from tests.test_pipeline import PIPELINE_POLICY, real_socket
from tests.test_pipeline_db import CSV, one
from tests.test_search_db import add_document, add_pieces

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
INSUFFICIENT_PRIVILEGE = "42501"
_NEW_COLUMNS = text(
    "SELECT a.attname::text, format_type(a.atttypid, a.atttypmod), a.attnotnull,"
    " pg_get_expr(d.adbin, d.adrelid), col_description(a.attrelid, a.attnum)"
    " FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
    " WHERE a.attrelid = 'public.customers'::regclass"
    " AND a.attname IN ('status', 'disabled_at', 'delete_after')"
)
_APP_MAY_UPDATE = text(
    "SELECT attname::text FROM pg_attribute WHERE attrelid = 'public.customers'::regclass"
    " AND attnum > 0 AND NOT attisdropped AND has_column_privilege(:r, attrelid, attname, 'UPDATE')"
)
_STATUS = text("SELECT status, disabled_at, delete_after FROM customers WHERE id = :c")
# What the app may no longer do to its own customers row (one statement each).
_REFUSED = (
    "UPDATE customers SET status = 'active' WHERE id = :c",
    "UPDATE customers SET disabled_at = NULL WHERE id = :c",
    "UPDATE customers SET delete_after = NULL WHERE id = :c",
    "UPDATE customers SET name = 'Renamed' WHERE id = :c",
)


def _app_may_update(database: Database) -> set[str]:
    with database.system() as conn:
        return {str(row[0]) for row in conn.execute(_APP_MAY_UPDATE, {"r": APP_ROLE})}


def _status(db: Database, customer_id: str) -> tuple[Any, ...]:
    with db.customer(customer_id) as conn:
        return tuple(conn.execute(_STATUS, {"c": customer_id}).one())


def _cli(db: Database, command: str, customer_id: str) -> tuple[int, str]:
    out = io.StringIO()
    return main([command, "--customer-id", customer_id], db=db, environ={}, out=out), out.getvalue()


def _audit(db: Database, customer_id: str) -> list[tuple[Any, ...]]:
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text(
                "SELECT action, target_type, target_id, details FROM audit_log"
                " WHERE customer_id = :c AND action LIKE 'customer.%' ORDER BY id"
            ),
            {"c": customer_id},
        )
        return [tuple(row) for row in rows]


def _login(client: TestClient, email: str) -> httpx.Response:
    return client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)


# --- the migration ------------------------------------------------------------------------


def test_0014_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0013")
    at_0013 = m0002._schema(database)
    every_column = {"id", "name", "max_upload_mb", "created_at"}
    assert _app_may_update(database) == every_column
    migrate(url, "head")
    at_head = m0002._schema(database)
    with database.system() as conn:
        columns = {str(row[0]): tuple(row[1:]) for row in conn.execute(_NEW_COLUMNS)}
    assert {name: column[:3] for name, column in columns.items()} == {
        "status": ("text", True, "'active'::text"),
        "disabled_at": ("timestamp with time zone", False, None),
        "delete_after": ("timestamp with time zone", False, None),
    }
    assert all(column[3] for column in columns.values())  # each commented (0007's rule)
    assert _app_may_update(database) == {"max_upload_mb"}

    m0002._downgrade(url, "0013")
    assert m0002._schema(database) == at_0013
    assert _app_may_update(database) == every_column
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_the_downgrade_refuses_while_a_customer_is_not_active(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "head")
    customer, _ = new_customer(database)
    with database.engine.begin() as conn:  # the owner, as the CLI
        assert customers.set_status(conn, customer, customers.DISABLED, now=NOW) == customers.ACTIVE

    with pytest.raises(subprocess.CalledProcessError) as refused:
        m0002._downgrade(url, "0013")
    assert b"enable it before downgrading" in refused.value.stderr
    assert _current(url) == _head()  # the whole downgrade is one transaction

    with database.engine.begin() as conn:
        customers.set_status(conn, customer, customers.ACTIVE, now=NOW)
    m0002._downgrade(url, "0013")


# --- the grant fix and the CHECKs ----------------------------------------------------------


@pytest.mark.parametrize("statement", _REFUSED)
def test_the_app_may_not_change_its_own_customers_row(db: Database, statement: str) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), {"c": customer})
    assert sqlstate(refused.value) == INSUFFICIENT_PRIVILEGE
    assert _status(db, customer) == ("active", None, None)


def test_the_app_still_sets_its_upload_limit_and_locks_its_folder_tree(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        changed = admin_sql.update_settings(conn, customer, max_upload_mb=5)
        locked = conn.execute(text(_LOCK_TREE), {"c": customer}).scalar()  # FOR NO KEY UPDATE
        active = customers.is_active(conn, customer)
    assert changed is not None and changed["max_upload_mb"] == 5
    assert (locked, active) == (1, True)


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE customers SET status = 'paused', disabled_at = :t WHERE id = :c", "customers_status_check"),
        ("UPDATE customers SET status = 'disabled' WHERE id = :c", "customers_disabled_at_check"),
        ("UPDATE customers SET disabled_at = :t WHERE id = :c", "customers_disabled_at_check"),
        (
            "UPDATE customers SET status = 'deleting', disabled_at = :t WHERE id = :c",
            "customers_delete_after_check",
        ),
        ("UPDATE customers SET delete_after = :t WHERE id = :c", "customers_delete_after_check"),
    ],
)
def test_the_checks_keep_the_three_columns_together(db: Database, statement: str, constraint: str) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(IntegrityError) as refused, db.engine.begin() as conn:  # even the owner
        conn.execute(text(statement), {"c": customer, "t": NOW})
    assert violation(refused.value) == ("23514", constraint)


# --- sign-in and sessions ------------------------------------------------------------------


def test_a_disabled_customer_can_not_sign_in_and_its_sessions_end_while_b_goes_on(db: Database) -> None:
    api = Api(db)
    member = api.new_signed()
    a = member.customer_id
    admin = api.admin_sign_in(a)
    b_member = api.new_signed()
    reviewer_id, reviewer_email = api.add_user(a, "reviewer")
    temporary_id, temporary_email = api.add_user(a, "admin")
    with db.customer(a) as conn:
        conn.execute(
            text("UPDATE users SET must_change_password = true WHERE customer_id = :c AND id = :u"),
            {"c": a, "u": temporary_id},
        )
    version = api.user_state(a, member.user_id)["session_version"]

    assert _cli(db, "disable-customer", a) == (0, f"customer {a}: disabled\n")

    for client, path in ((member.client, "/api/auth/me"), (member.client, "/api/documents")):
        assert client.get(path).status_code == 401, path
    assert admin.client.get("/api/admin/users").status_code == 401
    assert b_member.client.get("/api/auth/me").status_code == 200
    refused = _login(api.client(), reviewer_email)
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "bad_credentials"
    api.clock.advance(30)  # a code that was never used
    assert admin.totp_secret is not None
    assert api.admin_login(api.client(), admin.email, code=api.code(admin.totp_secret)).status_code == 401
    assert api.admin_login(api.client(), temporary_email).status_code == 401  # not the 403 that says "right"
    assert ("auth.login_failed", "user", reviewer_id, {"reason": "customer_disabled"}) in api.audit_rows(a)
    assert api.user_state(a, member.user_id)["session_version"] == version + 1

    assert _cli(db, "enable-customer", a) == (0, f"customer {a}: enabled\n")
    assert member.client.get("/api/auth/me").status_code == 401  # ended for good
    assert _login(api.client(), reviewer_email).status_code == 200


# --- the worker and the sweeps -------------------------------------------------------------


def test_the_worker_fails_a_disabled_customers_job_and_reads_bs(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    documents, storage = PgDocuments(db), MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, documents, storage, MemoryQueue())
    processor = DocumentProcessor(
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, documents),
        statuses=documents,
        documents=documents,
        socket=real_socket(),
        prepare=FakePreparer().prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=PgPipelineStore(db),
        customer_active=documents.customer_active,
    )
    a_doc = upload.accept(a, io.BytesIO(CSV)).document_id
    b_doc = upload.accept(b, io.BytesIO(CSV)).document_id
    _cli(db, "disable-customer", a)

    with pytest.raises(JobFailed) as failed:
        processor(Job(a, a_doc), lambda: None)
    processor(Job(b, b_doc), lambda: None)

    assert (failed.value.code, failed.value.retryable) == ("customer_disabled", False)
    status = "SELECT status, failure_code FROM documents WHERE id = :d"
    assert one(db, a, status, a_doc) == ("failed", "customer_disabled")
    assert one(db, a, "SELECT count(*) FROM pieces WHERE document_id = :d", a_doc) == (0,)
    assert one(db, b, status, b_doc) == ("ready", None)
    assert (documents.customer_active(a), documents.customer_active(b)) == (False, True)


def test_the_sweeps_and_reembed_pass_a_disabled_customer_over_until_it_is_enabled(db: Database) -> None:
    drain(db)
    a, a_admin = new_customer(db)
    b, _ = new_customer(db)
    a_doc, b_doc = add_document(db, a), add_document(db, b)
    add_pieces(db, a, a_doc, [("piece of A", None)])
    add_pieces(db, b, b_doc, [("piece of B", None)])
    with db.customer(a) as conn:
        story_doc = insert_document(conn, a)
    put(db, a, a_admin, story_doc, "Daregaon paper of A")
    _cli(db, "disable-customer", a)

    pieces = SameVector()
    assert embed_missing(db, pieces, None).written == 1
    assert pieces.calls == [["piece of B"]]
    assert (tags(db, a, a_doc), tags(db, b, b_doc)) == ([None], [CURRENT])
    stories = FakeModel()
    assert embed_missing_stories(db, stories, limit=10, customer_id=a) == StoryPass(found=1, written=0)
    out = io.StringIO()
    assert main(["reembed", "--customer-id", a], db=db, environ={}, out=out, model=stories) == 0
    assert out.getvalue() == f"customer {a}: not active, skipped\n"
    assert stories.calls == [["probe"]]  # reembed's probe only: nothing of A

    _cli(db, "enable-customer", a)
    assert embed_missing(db, SameVector(), None).written == 1
    assert embed_missing_stories(db, FakeModel(), limit=10, customer_id=a).written == 1


# --- the CLI ---------------------------------------------------------------------------------


def test_the_cli_disables_and_enables_a_customer_with_one_audit_row_each(db: Database) -> None:
    customer, _ = new_customer(db)

    assert _cli(db, "disable-customer", customer) == (0, f"customer {customer}: disabled\n")
    status, disabled_at, delete_after = _status(db, customer)
    assert (status, disabled_at is not None, delete_after) == ("disabled", True, None)
    again = f"customer {customer}: already disabled, nothing changed\n"
    assert _cli(db, "disable-customer", customer) == (0, again)
    assert _cli(db, "enable-customer", customer) == (0, f"customer {customer}: enabled\n")
    assert _status(db, customer) == ("active", None, None)
    again = f"customer {customer}: already enabled, nothing changed\n"
    assert _cli(db, "enable-customer", customer) == (0, again)

    by_cli = {"by": "cli"}
    assert _audit(db, customer) == [
        ("customer.disable", "customer", customer, by_cli),
        ("customer.enable", "customer", customer, by_cli),
    ]


def test_the_cli_refuses_a_customer_being_deleted_and_says_so(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    customer, _ = new_customer(db)
    with db.engine.begin() as conn:  # as PR 4's schedule-delete will leave it
        conn.execute(
            text(
                "UPDATE customers SET status = 'deleting', disabled_at = :t, delete_after = :t WHERE id = :c"
            ),
            {"c": customer, "t": NOW},
        )

    for command in ("disable-customer", "enable-customer"):
        assert _cli(db, command, customer) == (2, "")
        assert "being deleted; nothing was changed" in capsys.readouterr().err
    assert _status(db, customer) == ("deleting", NOW, NOW)
    assert _audit(db, customer) == []


def test_the_cli_needs_a_real_customer_id(db: Database, capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli(db, "disable-customer", str(uuid.uuid4())) == (2, "")
    assert "no such customer" in capsys.readouterr().err
    assert _cli(db, "enable-customer", "not-a-uuid") == (2, "")
    assert "the customer id is not valid" in capsys.readouterr().err
