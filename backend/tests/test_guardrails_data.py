"""#520 guard rails, data separation: attacks the existing isolation suite did not make.

1. Organisation A sends organisation B's ids to the routes no isolation test covered yet (the
   resumable upload, a department or owner filter, a folder upload's items, a clarify reply,
   story matches' scope, a word's replace): each is 404, 400 or empty, and B's rows stay as they
   were. The other id-taking routes are covered by tests/test_isolation_*.
2. Row level security holds when a query forgets its filter, under the real app login.
3. The limited logins can't change the rules that hold them.
4. Every route gated to reviewers or admins outside /api/admin refuses a member (and the admin
   ones a reviewer), found from the routes' own dependencies, so a new route is walked too.
5. A private story is its writer's only, even to their organisation's admin.
"""

from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from psycopg import sql
from sqlalchemy import text

from kaagaz.api.auth import require_admin_session, require_admin_with_two_step, require_delete_forever
from kaagaz.db.engine import Database
from tests import test_isolation_logins as limited
from tests.db_support import APP_ROLE, insert_folder, rule_on_everywhere
from tests.test_api_chat_clarify import _clarify
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint
from tests.test_api_folder_uploads import item, plan, register
from tests.test_api_my_story import delete, get, put
from tests.test_api_story_matches import QUESTION, STORY, matches
from tests.test_api_support import ANY_ID, CSRF, PDF_BYTES, Api, Signed, answer_json
from tests.test_indexing_db import FakeModel

pytestmark = pytest.mark.db

logins = limited.logins  # the fixture, by its name: each limited login's URL
SESSIONS = "/api/documents/upload-sessions"
REFUSED = "42501"  # insufficient privilege


def _start(signed: Signed, data: bytes = PDF_BYTES, **extra: Any) -> Any:
    body = {
        "file_name": "big.pdf",
        "file_size": len(data),
        "fingerprint": _fingerprint(data),
        "user_id": signed.user_id,
        **extra,
    }
    return signed.client.post(SESSIONS, json=body, headers=CSRF)


def _error(reply: Any) -> tuple[int, str]:
    return reply.status_code, reply.json()["error"]["code"]


def _department(db: Database, signed: Signed, name: str) -> str:
    with db.customer(signed.customer_id) as conn:
        folder = insert_folder(conn, signed.customer_id, signed.user_id, name)
        conn.execute(text("UPDATE folders SET kind = 'department' WHERE id = :f"), {"f": folder})
    return folder


def _sessions(db: Database, customer: str) -> int:
    with db.customer(customer) as conn:
        return int(conn.execute(text("SELECT count(*) FROM upload_sessions")).scalar_one())


# --- 1. the id-taking routes the isolation suite missed ------------------------------------------


def test_a_resumable_start_takes_no_place_document_session_or_item_of_another_organisation(
    db: Database,
) -> None:
    storage = ResumableStorage()
    api = Api(db, storage=storage)
    a, b = api.new_admin("Sharma Traders"), api.new_admin("Mehta Textiles")
    b_folder = api.add_folder(b.customer_id, b.user_id, "mehta folder")
    b_department = _department(db, b, "Mehta Accounts")
    b_document = api.add_document(b.customer_id, name="mehta.pdf", uploaded_by=b.user_id)
    b_session = _start(b).json()["id"]
    b_plan = plan(b).json()
    b_item = register(b, b_plan["id"], [item("A/x.pdf", b_plan["folder_ids"][0], len(PDF_BYTES))])
    b_item_id = b_item.json()["items"][0]["id"]

    for extra in (
        {"folder_id": b_folder},
        {"department_id": b_department},
        {"version_document_id": b_document},
        {"resume_session_id": b_session},
        {"folder_upload_item_id": b_item_id},
    ):
        assert _error(_start(a, **extra)) == (404, "not_found"), extra
    a_plan = plan(a).json()
    a_item = register(a, a_plan["id"], [item("A/x.pdf", a_plan["folder_ids"][0], len(PDF_BYTES))])
    own_item = {"folder_upload_item_id": a_item.json()["items"][0]["id"]}
    assert _error(_start(a, folder_upload_id=b_plan["id"], **own_item)) == (404, "not_found")

    assert _sessions(db, a.customer_id) == 0  # nothing was started for A
    assert list(storage.parts) == [f"{b.customer_id}/{b.user_id}/{b_session}"]
    assert _start(a, folder_id=api.add_folder(a.customer_id, a.user_id, "own")).status_code == 200  # control


def test_another_organisations_or_users_resumable_session_takes_no_part_and_never_completes(
    db: Database,
) -> None:
    storage = ResumableStorage()
    api = Api(db, storage=storage)
    a, b = api.new_signed(), api.new_signed()
    a2 = api.sign_in(a.customer_id)
    for owner, other in ((b, a), (a, a2)):
        session = _start(owner).json()["id"]
        part = f"{SESSIONS}/{session}/parts/1"
        assert _error(other.client.put(part, content=PDF_BYTES, headers=CSRF)) == (404, "not_found")
        assert _error(other.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)) == (404, "not_found")
        assert storage.parts[f"{owner.customer_id}/{owner.user_id}/{session}"] == {}  # no byte went in
        assert owner.client.put(part, content=PDF_BYTES, headers=CSRF).status_code == 204  # the control
        done = owner.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
        assert done.status_code == 201, done.text
    assert other.client.get("/api/documents").json()["total"] == 1  # a2 sees a's one document
    assert b.client.get("/api/documents").json()["total"] == 1


def test_a_list_by_another_organisations_department_or_owner_shows_nothing_of_it(db: Database) -> None:
    api = Api(db)
    a, b = api.new_admin("Sharma Traders"), api.new_admin("Mehta Textiles")
    b_department = _department(db, b, "Mehta Accounts")
    api.add_document(b.customer_id, name="mehta.pdf", folder_id=b_department, owner_id=b.user_id)
    api.add_document(a.customer_id, name="sharma.pdf", owner_id=a.user_id)

    assert _error(a.client.get("/api/documents", params={"department_id": b_department})) == (
        404,
        "not_found",
    )
    by_owner = a.client.get("/api/documents", params={"owner_id": b.user_id})
    assert by_owner.status_code == 200
    assert (by_owner.json()["items"], by_owner.json()["total"]) == ([], 0)
    assert "mehta" not in by_owner.text
    assert b.client.get("/api/documents", params={"department_id": b_department}).json()["total"] == 1


def test_an_upload_into_another_organisations_department_is_404_and_stores_nothing(db: Database) -> None:
    api = Api(db)
    a, b = api.new_admin("Sharma Traders"), api.new_admin("Mehta Textiles")
    b_department = _department(db, b, "Mehta Accounts")
    stored = api.storage.count()
    files = {"file": ("a.pdf", PDF_BYTES, "application/pdf")}
    reply = a.client.post("/api/documents", data={"department_id": b_department}, files=files, headers=CSRF)
    assert _error(reply) == (404, "not_found")
    assert api.storage.count() == stored
    assert a.client.get("/api/documents").json()["total"] == 0
    own = _department(db, a, "Sharma Accounts")  # the control
    assert (
        a.client.post("/api/documents", data={"department_id": own}, files=files, headers=CSRF).status_code
        == 201
    )


def test_a_folder_upload_never_registers_a_file_into_another_organisations_folder(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    a_folder = api.add_folder(a.customer_id, a.user_id, "sharma folder")
    b_plan = plan(b).json()
    refused = register(b, b_plan["id"], [item("A/x.pdf", a_folder, 100)])
    assert _error(refused) == (400, "bad_request")
    listed = b.client.get(f"/api/folder-uploads/{b_plan['id']}/items").json()["items"]
    assert listed == []
    assert register(b, b_plan["id"], [item("A/x.pdf", b_plan["folder_ids"][0], 100)]).status_code == 200


def test_a_clarify_reply_in_my_own_conversation_never_answers_another_organisations_clarify(
    db: Database,
) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    api.answers.reply = _clarify(api.add_document(b.customer_id, name="pune.pdf", uploaded_by=b.user_id))
    b_message = b.client.post("/api/chat", json={"question": "Land in Puny?"}, headers=CSRF).json()["answer"][
        "id"
    ]
    api.answers.reply = answer_json()
    a_conversation = a.client.post("/api/chat", json={"question": "GST bills"}, headers=CSRF).json()
    body = {
        "conversation_id": a_conversation["conversation_id"],
        "reply": {"message_id": b_message, "choice_ids": ["c1"]},
    }
    reply = a.client.post("/api/chat", json=body, headers=CSRF)
    assert _error(reply) == (409, "conflict")
    assert api.answers.resumed == []  # B's clarify was never resumed
    assert [question for _, question in api.answers.asked] == ["Land in Puny?", "GST bills"]


def test_story_matches_scoped_to_another_organisations_ids_find_nothing(db: Database) -> None:
    api = Api(db, model=FakeModel())
    a, b = api.new_signed(), api.new_signed()
    a_document = api.add_document(a.customer_id)
    b_folder = api.add_folder(b.customer_id, b.user_id, "mehta folder")
    b_tag = api.add_tag(b.customer_id, b.user_id, "mehta")
    b_document = api.add_document(b.customer_id, name="mehta.pdf", folder_id=b_folder)
    api.tag_document(b.customer_id, b_document, b_tag)
    assert put(a, a_document, STORY).status_code == 201
    assert put(b, b_document, STORY).status_code == 201

    assert [m["document_id"] for m in matches(a, QUESTION)] == [a_document]
    for scope in ({"document_ids": [b_document]}, {"folder_id": b_folder}, {"tag_ids": [b_tag]}):
        assert matches(a, QUESTION, **scope) == [], scope
        posted = a.client.post(
            "/api/me/story-matches", json={"q": QUESTION, "mode": "stories", **scope}, headers=CSRF
        )
        assert (posted.status_code, posted.json()["story_matches"]) == (200, []), scope
    assert [m["document_id"] for m in matches(b, QUESTION, folder_id=b_folder)] == [b_document]  # control


def test_a_word_never_replaces_another_organisations_word(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    b_word = b.client.post("/api/me/words", json={"word": "malak", "meaning": "boss"}, headers=CSRF)
    a_word = a.client.post("/api/me/words", json={"word": "malak", "meaning": "owner"}, headers=CSRF)
    a_id, b_id = a_word.json()["word"]["id"], b_word.json()["word"]["id"]
    body = {"word": "malak", "replace_id": b_id}
    assert _error(a.client.patch(f"/api/me/words/{a_id}", json=body, headers=CSRF)) == (404, "not_found")
    assert [(w["word"], w["meaning"]) for w in b.client.get("/api/me/words").json()["items"]] == [
        ("malak", "boss")
    ]
    assert [(w["word"], w["meaning"]) for w in a.client.get("/api/me/words").json()["items"]] == [
        ("malak", "owner")
    ]


# --- 2 and 3. row level security under the real app login -----------------------------------------


def _seeded(api: Api, model: FakeModel) -> tuple[Signed, Signed]:
    """A and B, each with a document in a tagged folder, a story, a word, a conversation and audit."""
    pair = []
    for name in ("Sharma Traders", "Mehta Textiles"):
        signed = api.new_admin(name)
        folder = api.add_folder(signed.customer_id, signed.user_id, f"{name} folder")
        document = api.add_document(signed.customer_id, folder_id=folder, uploaded_by=signed.user_id)
        api.tag_document(
            signed.customer_id, document, api.add_tag(signed.customer_id, signed.user_id, name[:5])
        )
        assert put(signed, document, STORY).status_code == 201
        signed.client.post("/api/me/words", json={"word": "malak", "meaning": "boss"}, headers=CSRF)
        signed.client.post("/api/chat", json={"question": "GST bills"}, headers=CSRF)
        pair.append(signed)
    assert model.calls  # the stories were embedded
    return pair[0], pair[1]


@pytest.fixture
def api_login(logins: dict[str, str]) -> Iterator[Callable[..., psycopg.Connection[Any]]]:
    """Connections as ``kaagaz_api`` (the server's app login), closed after the test."""
    opened: list[psycopg.Connection[Any]] = []

    def connect() -> psycopg.Connection[Any]:
        conn = psycopg.connect(logins[limited.API], autocommit=True)
        opened.append(conn)
        return conn

    yield connect
    for conn in opened:
        conn.close()


def _count(app: psycopg.Connection[Any], query: str, table: str, *params: str) -> int:
    row = app.execute(sql.SQL(query).format(sql.Identifier(table)), params).fetchone()
    assert row is not None
    return int(row[0])


def test_a_query_that_forgets_its_filter_sees_only_its_own_customer_in_every_table(
    db: Database, api_login: Callable[[], psycopg.Connection[Any]]
) -> None:
    model = FakeModel()
    api = Api(db, model=model)
    a, b = _seeded(api, model)
    seeded = ("documents", "folders", "tags", "audit_log", "user_words", "document_stories", "conversations")
    with db.engine.begin() as owner:  # the owner: every customer table, and B's rows really there
        tables = list(owner.execute(_APP_READS, {"role": APP_ROLE}).scalars())
        b_rows = {}
        for table in seeded:
            query = text(f"SELECT count(*) FROM {table} WHERE customer_id = :b")  # noqa: S608 - fixed names
            b_rows[table] = owner.execute(query, {"b": b.customer_id}).scalar_one()
    assert len(tables) > 40
    assert set(seeded) <= set(tables)
    assert all(count > 0 for count in b_rows.values()), b_rows

    app = api_login()
    app.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(APP_ROLE)))
    rule_on = rule_on_everywhere()
    for who in ("", a.user_id):  # as the customer, then also as A's user (the one-user tables)
        app.execute("SELECT set_config('kaagaz.customer_id', %s, false)", (a.customer_id,))
        app.execute("SELECT set_config('kaagaz.user_id', %s, false)", (who,))
        others = "SELECT count(*) FROM {} WHERE customer_id <> %s"
        leaked = {table: n for table in tables if (n := _count(app, others, table, a.customer_id))}
        assert leaked == {}, who
        # A's own, with no WHERE at all. Changed on purpose (PR 9a, #606): with the database rule
        # on, the API role
        # with no person sees no file.
        assert _count(app, "SELECT count(*) FROM {}", "documents") == (0 if not who and rule_on else 1)
    app.execute("SELECT set_config('kaagaz.customer_id', '', false)")
    for table in seeded:  # no customer: nothing at all
        assert _count(app, "SELECT count(*) FROM {}", table) == 0, table


_APP_READS = text(
    "SELECT c.relname FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid"
    " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')"
    " AND a.attname = 'customer_id' AND NOT a.attisdropped"
    " AND has_table_privilege(:role, c.oid, 'SELECT') ORDER BY 1"
)
ATTACKS = (
    "ALTER TABLE documents DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE documents NO FORCE ROW LEVEL SECURITY",
    "DROP POLICY customer_only ON documents",
    "ALTER POLICY customer_only ON documents USING (true)",
    "CREATE POLICY open_door ON documents FOR SELECT USING (true)",
    "TRUNCATE documents",
    "DROP TABLE documents",
    "ALTER TABLE documents ADD COLUMN stolen text",
    "ALTER ROLE kaagaz_app BYPASSRLS",
    "ALTER ROLE kaagaz_api BYPASSRLS",
    "GRANT kaagaz_operator TO kaagaz_api",
    "SET row_security = off; SELECT count(*) FROM documents",
)


@pytest.mark.parametrize("statement", ATTACKS)
def test_the_app_login_cannot_change_or_step_round_the_rules_that_hold_it(
    db: Database, api_login: Callable[[], psycopg.Connection[Any]], statement: str
) -> None:
    customer, _ = Api(db).customer()
    app = api_login()
    app.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(APP_ROLE)))
    app.execute("SELECT set_config('kaagaz.customer_id', %s, false)", (customer,))
    with pytest.raises(psycopg.Error) as refused:
        for part in statement.split("; "):
            app.execute(sql.SQL(part))  # the fixed statements above
    assert refused.value.sqlstate == REFUSED, statement
    with db.engine.begin() as owner:  # nothing changed: the rule still holds
        rls = owner.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'documents'")
        ).one()
        bypass = owner.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
        ).scalar_one()
    assert (rls[0], bypass) == (True, False)


def test_the_operator_login_reads_no_customer_table_even_with_a_customer_set(
    db: Database, logins: dict[str, str]
) -> None:
    customer, _ = Api(db).customer()
    with psycopg.connect(logins[limited.OPS], autocommit=True) as ops:
        ops.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(limited.OPERATOR)))
        ops.execute("SELECT set_config('kaagaz.customer_id', %s, false)", (customer,))
        for table in ("documents", "users", "audit_log", "document_stories"):
            with pytest.raises(psycopg.Error) as refused:
                _count(ops, "SELECT count(*) FROM {}", table)
            assert refused.value.sqlstate == REFUSED, table


# --- 4. role gates outside /api/admin ----------------------------------------------------------


def _gate(call: Callable[..., Any]) -> frozenset[str] | None:
    """The roles a dependency lets through, None when it is not a role gate."""
    if call in (require_admin_session, require_admin_with_two_step):
        return frozenset({"admin"})
    if call is require_delete_forever:
        return frozenset({"admin", "granted"})
    if getattr(call, "__qualname__", "") == "require_role.<locals>.dependency" and call.__closure__:
        allowed = call.__closure__[0].cell_contents
        return frozenset(allowed) if isinstance(allowed, frozenset) else None
    return None


def _gated_calls(app: FastAPI) -> dict[tuple[str, str], frozenset[str]]:
    """Every (method, filled path) whose dependencies hold a role gate, with the narrowest one."""
    found: dict[tuple[str, str], frozenset[str]] = {}
    for context in iter_route_contexts(app.routes):
        route, path = context.original_route, context.path
        if not isinstance(route, APIRoute) or path is None:
            continue
        gates, stack = [], [route.dependant]
        while stack:
            dependant = stack.pop()
            stack.extend(dependant.dependencies)
            if dependant.call is not None and (gate := _gate(dependant.call)) is not None:
                gates.append(gate)
        if not gates:
            continue
        for param in route.dependant.path_params:
            path = path.replace("{" + param.name + "}", "1" if param.field_info.annotation is int else ANY_ID)
        for method in context.methods or ():
            found[(method, path)] = min(gates, key=len)
    return found


def test_every_reviewer_or_admin_route_refuses_a_member_and_every_admin_one_a_reviewer(db: Database) -> None:
    api = Api(db)
    customer, _ = api.customer()
    member, reviewer = api.sign_in(customer, "member"), api.sign_in(customer, "reviewer")
    gated = _gated_calls(api.app)
    outside_admin = {call for call in gated if not call[1].startswith("/api/admin")}
    assert {
        ("POST", f"/api/documents/{ANY_ID}/reprocess"),
        ("POST", f"/api/review/{ANY_ID}"),
        ("DELETE", f"/api/tags/{ANY_ID}"),
        ("DELETE", f"/api/documents/{ANY_ID}/permanent"),
        ("PUT", f"/api/admin/answer-quality/{ANY_ID}/handled"),
    } <= set(gated)
    assert len(outside_admin) >= 19  # 6 Nov 2026: read again, review, ensemble, word review, tags...
    for (method, path), roles in sorted(gated.items()):
        callers = [member] + ([reviewer] if "reviewer" not in roles else [])
        for signed in callers:
            body: dict[str, Any] | None = None if method in ("GET", "DELETE") else {}
            reply = signed.client.request(method, path, headers=CSRF, json=body)
            assert reply.status_code == 403, (method, path, signed.email, reply.text)
            assert reply.json()["error"]["code"] in ("forbidden", "admin_session_required"), (method, path)


# --- 5. a private story is its writer's only ------------------------------------------------------


def test_an_admin_never_reads_finds_or_deletes_a_members_private_story(db: Database) -> None:
    api = Api(db, model=FakeModel())
    member = api.new_signed()
    admin = api.admin_sign_in(member.customer_id)
    document = api.add_document(member.customer_id, uploaded_by=member.user_id)
    assert put(member, document, STORY).status_code == 201

    assert get(admin, document) is None
    assert _error(delete(admin, document)) == (404, "not_found")
    assert matches(admin, QUESTION) == []
    stories = admin.client.post(
        "/api/me/story-matches", json={"q": QUESTION, "mode": "stories"}, headers=CSRF
    )
    assert stories.json()["story_matches"] == []
    for path in ("/api/admin/audit", "/api/admin/audit/export", "/api/search?q=Daregaon"):
        reply = admin.client.get(path)
        assert reply.status_code == 200, (path, reply.text)
        assert "Daregaon" not in reply.text, path
    with db.user(member.customer_id, admin.user_id) as conn:  # the admin's own view of the table
        assert conn.execute(text("SELECT count(*) FROM document_stories")).scalar_one() == 0
    with db.customer(member.customer_id) as conn:  # the organisation's view, no user
        assert conn.execute(text("SELECT count(*) FROM document_stories")).scalar_one() == 0
    assert get(member, document)["story"] == STORY  # the control: the writer still reads it
