"""PRD 7 PR 9a (#606, p3a:115): the database rule itself. A deliberately unfiltered statement, as the
API role with a person, returns nothing hidden from documents, folders, trashed folders and the
content tables; writes too; and the roles and the switch around it. PR 6's walled organisation
(tests/test_isolation_access_views.py), its reference rule written plainly (``_expected``).

Rows of the content tables are made for the shapes the rule has (one document, none, two, a
candidate family, a family); every other content table carries the same generated rule, which
the policy check below pins on the catalog."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import ProgrammingError

from kaagaz.db import storage_limits
from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import INSUFFICIENT_PRIVILEGE, sqlstate
from tests.db_support import APP_ROLE, WORKER_ROLE, new_customer, person_rule, until_0092
from tests.test_api_support import CSRF, PDF_BYTES, Api
from tests.test_isolation_access_views import Org, _all, _expected, _walled
from tests.test_isolation_schema import PERSON_RULE_TABLES
from tests.test_operator_auth_db import _owner

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("the_rule_on")]


_BOX = '{"kind": "box", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]}'
# One row a file, the owner's, for each shape of the content rule.
_ROWS = {
    "pages": "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit)"
    " VALUES (:c, :d, 1, 1, 1, 'pt')",
    "pieces": "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions, label)"  # noqa: S608
    f" VALUES (:c, :d, 0, 1, 1, 'words', '[{_BOX}]', 'verified')",
    "model_usage": "INSERT INTO model_usage (customer_id, document_id, kind, model, calls, input_tokens,"
    " output_tokens, reasoning_tokens) VALUES (:c, :d, 'scan', 'google/gemini', 1, 1, 1, 0)",
    "page_rotations": "INSERT INTO page_rotations (customer_id, document_id, page_no, turn, set_by)"
    " VALUES (:c, :d, 1, 90, :u)",
}
_CONTENT = sorted(PERSON_RULE_TABLES - {"documents", "folders", "trashed_folders"})


def _app(db: Database) -> Database:
    return Database(db.engine, APP_ROLE)


@contextmanager
def _as(db: Database, org: Org, name: str | None, role: str = APP_ROLE) -> Iterator[Connection]:
    database = Database(db.engine, role)
    if name is None:
        with database.customer(org.c) as conn:
            yield conn
    else:
        with database.user(org.c, org.people[name].user_id) as conn:
            yield conn


def _ids(conn: Connection, statement: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(text(statement))}


@pytest.fixture
def org(db: Database) -> Org:
    """The walled organisation, with a row of each shape for every file (a NULL-document usage row
    too), a link between two files and a suggestion between two families."""
    walled = _walled(Api(db))
    user = walled.people["admin"].user_id
    for document in walled.files.values():
        for statement in _ROWS.values():
            _owner(db, statement, {"c": walled.c, "d": document, "u": user})
    _owner(db, _ROWS["model_usage"], {"c": walled.c, "d": None, "u": user})
    f = walled.files
    _owner(
        db,
        "INSERT INTO document_links (customer_id, document_id, linked_document_id, identifier_kind,"
        " identifier_value) VALUES (:c, :a, :b, 'invoice_number', 'X1'),"
        " (:c, :b, :a, 'invoice_number', 'X1'),"
        " (:c, :a, :o, 'invoice_number', 'X2')",
        {"c": walled.c, "a": f["sales"], "b": f["fin"], "o": f["top"]},
    )
    _owner(
        db,
        "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, outcome, status)"
        " VALUES (:c, :a, :b, 'suggested', 'pending'), (:c, :a, :o, 'suggested', 'pending')",
        {"c": walled.c, "a": f["sales"], "b": f["fin"], "o": f["top"]},
    )
    return walled


_PEOPLE = ("management", "head", "member", "plain", "named", "admin", "off")


def test_an_unfiltered_read_returns_only_what_each_person_sees(db: Database, org: Org) -> None:
    files = _all(db, org.c)
    for name in _PEOPLE:
        user = org.people[name].user_id
        sees = _expected(db, org, user)
        with _as(db, org, name) as conn:
            assert _ids(conn, "SELECT id FROM documents") == sees, name
            assert _ids(conn, "SELECT id FROM folders") == _expected(db, org, user, folders=True), name
            assert _ids(conn, "SELECT id FROM trashed_folders") == _ids(
                conn, "SELECT id FROM visible_trashed_folders"
            ), name
            for table in _ROWS:
                assert (
                    _ids(conn, f"SELECT document_id FROM {table} WHERE document_id IS NOT NULL")  # noqa: S608
                    == sees & files
                )
            # A row of no document is the organisation's: it stays (the budget and usage counts), for a
            # session with no person too.
            assert (
                conn.execute(text("SELECT count(*) FROM model_usage WHERE document_id IS NULL")).scalar() == 1
            )
            links = {
                (str(a), str(b))
                for a, b in conn.execute(text("SELECT document_id, linked_document_id FROM document_links"))
            }
            assert links == {(a, b) for a, b in _links(org) if a in sees and b in sees}, name
            candidates = {
                str(r[0]) for r in conn.execute(text("SELECT candidate_family_id FROM version_suggestions"))
            }
            assert candidates == {
                b for b in (org.files["fin"], org.files["top"]) if b in sees and org.files["sales"] in sees
            }, name


def _links(org: Org) -> set[tuple[str, str]]:
    f = org.files
    return {(f["sales"], f["fin"]), (f["fin"], f["sales"]), (f["sales"], f["top"])}


def test_with_no_person_or_another_organisations_person_no_file_row_is_seen(db: Database, org: Org) -> None:
    other, other_admin = new_customer(db)
    with _as(db, org, None) as conn:
        for table in ("documents", "folders", "trashed_folders"):
            assert _ids(conn, f"SELECT id FROM {table}") == set(), table  # noqa: S608 - fixed names
        for table in _CONTENT:
            column = "family_id" if table == "document_stories" else "document_id"
            found = f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608 - fixed names
            assert _ids(conn, found) == set(), table
    with _app(db).user(other, other_admin) as conn:
        assert _ids(conn, "SELECT id FROM documents") & _all(db, org.c) == set()


def test_writes_touch_only_what_the_person_sees_and_never_add_a_hidden_row(db: Database, org: Org) -> None:
    plain = org.people["plain"].user_id
    sees = _expected(db, org, plain)
    with _as(db, org, "plain") as conn:
        with conn.begin_nested() as inner:
            touched = conn.execute(text("UPDATE documents SET display_name = display_name")).rowcount
            gone = {str(r[0]) for r in conn.execute(text("DELETE FROM pieces RETURNING document_id"))}
            inner.rollback()
        assert (touched, gone) == (len(sees), sees & _all(db, org.c))
        for statement, params in (
            (_ROWS["pages"].replace("1, 1, 1", "2, 1, 1"), {"c": org.c, "d": org.files["fin"]}),
            (
                "INSERT INTO documents (customer_id, display_name, sha256, size_bytes, detected_type,"
                " folder_id)"
                " VALUES (:c, 'x.pdf', :h, 1, 'pdf', :f)",
                {"c": org.c, "h": uuid.uuid4().hex * 2, "f": org.folders["finance"]},
            ),
        ):
            with pytest.raises(ProgrammingError) as refused, conn.begin_nested():
                conn.execute(text(statement), params)
            assert sqlstate(refused.value) == INSUFFICIENT_PRIVILEGE


def test_a_new_version_of_a_file_given_to_them_alone_is_theirs(db: Database, org: Org) -> None:
    """The family clause: the share reaches the new version only after its statement (0085)."""
    plain = org.people["plain"]
    reply = plain.client.post(
        f"/api/documents/{org.files['given']}/versions",
        files={"file": ("v2.pdf", PDF_BYTES + b"v2", "application/pdf")},
        headers=CSRF,
    )
    assert reply.status_code == 201, reply.text
    with _as(db, org, "plain") as conn:
        assert reply.json()["document"]["id"] in _ids(conn, "SELECT id FROM documents")


def test_the_worker_role_reads_every_row_and_the_views_its_person(db: Database, org: Org) -> None:
    files = _all(db, org.c)
    with _as(db, org, None, WORKER_ROLE) as conn:
        assert _ids(conn, "SELECT id FROM documents") == files
        assert _ids(conn, "SELECT document_id FROM pages") == files
    with _as(db, org, "plain", WORKER_ROLE) as conn:
        assert _ids(conn, "SELECT id FROM documents") == files
        assert _ids(conn, "SELECT id FROM visible_documents") == _expected(
            db, org, org.people["plain"].user_id
        )


def test_a_files_place_is_read_only_by_who_sees_it_an_admin_or_the_worker(db: Database, org: Org) -> None:
    """kaagaz_document_place (0091) reads the table whole, so it asks who calls: a hidden id is no row
    for a member, Management or the API role with no person; an Admin and the worker role read it."""
    place = "SELECT folder_id FROM kaagaz_document_place(CAST(:d AS uuid))"
    member, management = (_expected(db, org, org.people[n].user_id) for n in ("member", "management"))
    hidden = _all(db, org.c) - member - management - {org.files["trashed"]}
    assert hidden
    for name, role in (("member", APP_ROLE), ("management", APP_ROLE), (None, APP_ROLE)):
        with _as(db, org, name, role) as conn:
            for document in hidden:
                assert conn.execute(text(place), {"d": document}).all() == [], (name, document)
    with _as(db, org, "member") as conn:
        assert len(conn.execute(text(place), {"d": org.files["fin"]}).all()) == 1  # one they see
    for name, role in (("admin", APP_ROLE), (None, WORKER_ROLE)):
        with _as(db, org, name, role) as conn:
            for document in hidden:
                assert len(conn.execute(text(place), {"d": document}).all()) == 1, (name, document)


def test_the_switch_is_per_organisation(db: Database, org: Org) -> None:
    """Off for this organisation (on for another only): its tables show every row, its views are
    unchanged; the other's person is still held."""
    other, other_admin = new_customer(db)
    hidden = Api(db).add_document(other, name="h.pdf", uploaded_by=other_admin)
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d",
        {"u": other_admin, "d": hidden},
    )
    with until_0092():
        person_rule(db, on=True, customer_id=other)
        try:
            plain = org.people["plain"].user_id
            with _as(db, org, "plain") as conn:
                assert _ids(conn, "SELECT id FROM documents") == _all(db, org.c)
                assert _ids(conn, "SELECT id FROM visible_documents") == _expected(db, org, plain)
            with _app(db).user(other, other_admin) as conn:
                assert hidden not in _ids(conn, "SELECT id FROM documents")
        finally:
            person_rule(db, on=False, customer_id=other)


def _sees_everything(db: Database, customer: str, user: str | None) -> bool:
    database = _app(db)
    with database.customer(customer) if user is None else database.user(customer, user) as conn:
        return bool(conn.execute(text("SELECT kaagaz_sees_everything()")).scalar())


def test_the_fast_path_holds_only_when_every_file_is_seen(db: Database) -> None:
    """Day one (nothing walled, private, held or locked): yes for any active person, never with no
    person. Management: yes until a file is locked. A walled organisation: no for a member."""
    api = Api(db)
    customer, admin = new_customer(db)
    member, _ = api.add_user(customer, "member")
    boss, _ = api.add_user(customer, "member")
    _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": boss})
    file = api.add_document(customer, uploaded_by=admin)
    assert (_sees_everything(db, customer, member), _sees_everything(db, customer, None)) == (True, False)
    _owner(db, "UPDATE documents SET held_for = :u WHERE id = :d", {"u": admin, "d": file})
    assert (_sees_everything(db, customer, member), _sees_everything(db, customer, boss)) == (False, True)
    _owner(
        db, "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d", {"u": admin, "d": file}
    )
    assert _sees_everything(db, customer, boss) is False
    _owner(
        db,
        "UPDATE documents SET held_for = NULL, locked_at = NULL, locked_by = NULL WHERE id = :d",
        {"d": file},
    )
    _owner(db, "UPDATE customers SET walls_set = true WHERE id = :c", {"c": customer})
    assert (_sees_everything(db, customer, member), _sees_everything(db, customer, boss)) == (False, True)


def test_on_day_one_the_content_rule_never_runs_per_row(db: Database) -> None:
    """The fast path is one InitPlan: the per-row check shows ``never executed``."""
    api = Api(db)
    customer, admin = new_customer(db)
    member, _ = api.add_user(customer, "member")
    document = api.add_document(customer, uploaded_by=admin)
    _owner(db, _ROWS["pages"], {"c": customer, "d": document})
    with _app(db).user(customer, member) as conn:
        plan = "\n".join(
            str(r[0]) for r in conn.execute(text("EXPLAIN (ANALYZE, COSTS OFF) SELECT * FROM pages"))
        )
    assert "never executed" in plan, plan


def test_every_content_tables_rule_reads_its_document_through_documents(db: Database) -> None:
    """The catalog: each content table's person_only reads documents (so documents' own rule) for
    its document column, after the fast path, the same text for USING and WITH CHECK."""
    with db.engine.begin() as conn:
        rules: dict[str, Any] = {
            str(t): (str(q), str(c))
            for t, q, c in conn.execute(
                text("SELECT tablename, qual, with_check FROM pg_policies WHERE policyname = 'person_only'")
            )
        }
    for table in _CONTENT:
        using, check = rules[table]
        column = "family_id" if table == "document_stories" else "document_id"
        assert using == check and "kaagaz_sees_everything()" in using, table
        assert "FROM documents d" in using and f"{table}.{column}" in using, table


def test_the_whole_organisation_reads_count_what_the_person_cant_see_and_no_other(
    db: Database, org: Org
) -> None:
    """F4, F7 and F8: the duplicate check names a hidden copy by no id; the storage used and the
    spend count the files the plain reviewer can't see, and never another organisation's."""
    other, other_admin = new_customer(db)
    Api(db).add_document(other, uploaded_by=other_admin, data=PDF_BYTES + b"x" * 5000)
    stored = _owner(db, "SELECT sum(size_bytes) FROM documents WHERE customer_id = :c", {"c": org.c})[0][0]
    sha = _owner(db, "SELECT sha256 FROM documents WHERE id = :d", {"d": org.files["fin"]})[0][0]
    with _as(db, org, "plain") as conn:
        assert conn.execute(text("SELECT kaagaz_storage_used()")).scalar() == stored
        assert storage_limits.storage_room(conn, org.c).used_bytes == stored  # the cap can't fail open
        assert conn.execute(
            text("SELECT spent >= 0 FROM kaagaz_spent(now() - interval '1 day', now())")
        ).scalar()
        found = conn.execute(
            text("SELECT answer, document_id FROM kaagaz_duplicate(ARRAY[:h])"), {"h": sha}
        ).one()
        assert tuple(found) == ("hidden", None)
        assert (
            conn.execute(
                text("SELECT count(*) FROM kaagaz_existing('document', ARRAY[CAST(:d AS uuid)])"),
                {"d": org.files["fin"]},
            ).scalar()
            == 1
        )


# The tables that point at a file but carry no person rule, and why (a new one fails the test below).
EXEMPT = {
    "learned_corrections": "Q1: read only by the worker; the SQL guard stops any API read of it",
    "document_shares": "Q2: an access table (who sees what, not what is in a file)",
    "lock_grants": "Q2: an access table",
    "folder_upload_items": "the uploader's own items; a hidden copy is blanked by views.seen_items",
    "folder_uploads": "a zip's own document id; its items and their files go through the view",
}


def test_every_table_that_points_at_a_file_has_the_rule_or_a_reason(db: Database) -> None:
    """The catalog: each table with a key to documents, or a column named like a document id, is
    held by person_only (test_isolation_schema pins those) or listed in EXEMPT with why."""
    with db.engine.begin() as conn:
        pointing = {
            str(row[0])
            for row in conn.execute(
                text(
                    "SELECT conrelid::regclass::text FROM pg_constraint"
                    " WHERE contype = 'f' AND confrelid = 'public.documents'::regclass"
                    " UNION SELECT c.relname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid"
                    " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'"
                    " AND a.attname LIKE '%document_id' AND NOT a.attisdropped"
                )
            )
        } - {"documents"}
    assert pointing - PERSON_RULE_TABLES - set(EXEMPT) == set()
    assert set(EXEMPT) & PERSON_RULE_TABLES == set()


def test_an_admin_uploading_where_they_cant_see_the_files_is_refused_and_nothing_is_kept(
    db: Database,
) -> None:
    """Q6: an Admin sees Finance (every folder) but not its files: the rule refuses the new row, the
    reply is 409 upload_place_hidden, and the stored file is deleted again."""
    api = Api(db)
    org = _walled(api)
    stored = api.storage.count()
    reply = org.people["admin"].client.post(
        "/api/documents",
        data={"folder_id": org.folders["finance"]},
        files={"file": ("bill.pdf", PDF_BYTES + b"q6", "application/pdf")},
        headers=CSRF,
    )
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "upload_place_hidden"), reply.text
    assert api.storage.count() == stored


def _refused(db: Database, org: Org, statement: str) -> ProgrammingError:
    with pytest.raises(ProgrammingError) as refused, _as(db, org, "plain") as conn:
        conn.execute(text(statement), {"c": org.c, "d": org.files["fin"]})
    return refused.value


def test_the_rules_refusal_is_a_409_and_a_critical_line_and_any_other_42501_a_500(
    db: Database, org: Org, caplog: pytest.LogCaptureFixture
) -> None:
    """Q8: a statement the rule refused (a route's own check missed it) is 409 access_would_change,
    logged critical with the method and path; a 42501 for a missing right is a bug, a 500."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kaagaz.api import errors

    rule = _refused(db, org, _ROWS["pages"].replace("1, 1, 1", "3, 1, 1"))
    right = _refused(db, org, "SELECT * FROM kaagaz_person_rule")
    app = FastAPI()
    errors.install(app)

    @app.get("/rule")
    def by_the_rule() -> None:
        raise rule

    @app.get("/right")
    def by_a_right() -> None:
        raise right

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level("CRITICAL"):
        reply = client.get("/rule")
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "access_would_change")
    (line,) = [r for r in caplog.records if getattr(r, "code", None) == "access_would_change"]
    assert (line.levelname, getattr(line, "method", None), getattr(line, "path", None)) == (
        "CRITICAL",
        "GET",
        "/rule",
    )
    assert client.get("/right").status_code == 500
