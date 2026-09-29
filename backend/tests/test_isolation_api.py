"""Customer B, signed in, uses customer A's exact ids on every route and gets
"not found", never A's data. A forged cookie naming A's customer is refused.

This runs through the whole API (session, routes, SQL and row level
security), so a route that forgot the customer filter fails here. Both
customers' admins sign in on the admin screen, so the admin routes are
reached and must answer 404 (never 403) for the other customer's ids.
"""

import pytest
from sqlalchemy import text

from kaagaz.api.auth import SESSION_COOKIE, SessionClaims, sign_session
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.reading.spans import CellLocation, Span
from tests.test_api_support import CSRF, NOW, PDF_BYTES, Api, Signed

pytestmark = pytest.mark.db


class _World:
    def __init__(self, db: Database) -> None:
        self.api = Api(db)
        a_customer, _ = self.api.customer("Sharma Traders")
        b_customer, _ = self.api.customer("Mehta Textiles")
        self.a: Signed = self.api.admin_sign_in(a_customer)
        self.b: Signed = self.api.admin_sign_in(b_customer)
        api, a = self.api, self.a
        self.document = api.add_document(a.customer_id, name="sharma-secret.pdf", uploaded_by=a.user_id)
        api.add_page(a.customer_id, self.document)
        self.sheet = api.add_document(a.customer_id, detected_type="text", name="sharma-register.csv")
        api.add_page(a.customer_id, self.sheet, unit="cells", height=1)
        cell = span_to_json(Span("sharma cell", "sharma cell", CellLocation(1, 1, 1)))
        api.add_reader_output(a.customer_id, self.sheet, [cell])
        api.storage.page_images[(a.customer_id, self.document, 1)] = b"\x89PNG sharma"
        self.value = api.add_value(a.customer_id, self.document)
        chat = a.client.post("/api/chat", json={"question": "sharma question"}, headers=CSRF).json()
        self.conversation = chat["conversation_id"]
        self.answer_message = chat["answer"]["id"]


@pytest.fixture
def world(db: Database) -> _World:
    return _World(db)


def _assert_not_found(reply_status: int, body: dict[str, object]) -> None:
    assert reply_status == 404
    assert body["error"] == {"code": "not_found", "message": "We couldn't find that."}


def test_b_gets_404_on_every_document_route_of_a(world: _World) -> None:
    b, doc, sheet = world.b.client, world.document, world.sheet
    for path in (
        f"/api/documents/{doc}",
        f"/api/documents/{doc}/file",
        f"/api/documents/{doc}/pages/1/image",
        f"/api/documents/{sheet}/pages/1/cells",
    ):
        reply = b.get(path)
        _assert_not_found(reply.status_code, reply.json())
        assert "sharma" not in reply.text

    deleted = b.delete(f"/api/documents/{doc}", headers=CSRF)
    _assert_not_found(deleted.status_code, deleted.json())
    # A's document, file and page image are all still there.
    assert world.a.client.get(f"/api/documents/{doc}").status_code == 200
    assert world.api.storage.has(world.a.customer_id, doc)
    assert (world.a.customer_id, doc, 1) in world.api.storage.page_images


def test_b_cannot_open_a_conversation_or_rate_its_answer(world: _World) -> None:
    b = world.b.client
    opened = b.get(f"/api/conversations/{world.conversation}")
    _assert_not_found(opened.status_code, opened.json())
    rated = b.post(f"/api/messages/{world.answer_message}/feedback", json={"rating": "down"}, headers=CSRF)
    _assert_not_found(rated.status_code, rated.json())
    follow_up = b.post(
        "/api/chat", json={"question": "more", "conversation_id": world.conversation}, headers=CSRF
    )
    _assert_not_found(follow_up.status_code, follow_up.json())
    with world.api.db.customer(world.a.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM feedback")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM messages")).scalar_one() == 2


def test_b_cannot_resolve_a_value_of_a(world: _World) -> None:
    reply = world.b.client.post(
        f"/api/review/{world.value}", json={"choice": "typed", "value": "0", "remember": True}, headers=CSRF
    )
    _assert_not_found(reply.status_code, reply.json())
    with world.api.db.customer(world.a.customer_id) as conn:
        label = conn.execute(text("SELECT label FROM extracted_values WHERE id = :v"), {"v": world.value})
        assert label.scalar_one() == "needs_review"
        assert conn.execute(text("SELECT count(*) FROM learned_corrections")).scalar_one() == 0
    with world.api.db.customer(world.b.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM learned_corrections")).scalar_one() == 0


def test_b_gets_404_not_409_for_a_verified_value_of_a(world: _World) -> None:
    api, a = world.api, world.a
    verified = api.add_value(a.customer_id, world.document, label="verified", reason="readers_agree")
    reply = world.b.client.post(
        f"/api/review/{verified}", json={"choice": "first", "remember": False}, headers=CSRF
    )
    _assert_not_found(reply.status_code, reply.json())
    with api.db.customer(a.customer_id) as conn:
        row = conn.execute(
            text("SELECT label, label_reason FROM extracted_values WHERE id = :v"), {"v": verified}
        ).one()
    assert tuple(row) == ("verified", "readers_agree")


def test_b_cannot_change_a_user_of_a(world: _World) -> None:
    reply = world.b.client.patch(f"/api/admin/users/{world.a.user_id}", json={"disabled": True}, headers=CSRF)
    _assert_not_found(reply.status_code, reply.json())
    assert world.a.client.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("action", ["unlock", "reset-password", "reset-2fa"])
def test_b_cannot_unlock_or_reset_a_user_of_a(world: _World, action: str) -> None:
    api, a = world.api, world.a
    locked_id, locked_email = api.add_user(a.customer_id, "member")
    for _ in range(api.settings.login_max_failures):
        api.client().post(
            "/api/auth/login", json={"email": locked_email, "password": "wrong password!!"}, headers=CSRF
        )
    before = {user: api.user_state(a.customer_id, user) for user in (a.user_id, locked_id)}

    for user in (a.user_id, locked_id):
        reply = world.b.client.post(f"/api/admin/users/{user}/{action}", headers=CSRF)
        _assert_not_found(reply.status_code, reply.json())
        assert "temporary_password" not in reply.text

    assert {user: api.user_state(a.customer_id, user) for user in (a.user_id, locked_id)} == before
    assert a.client.get("/api/admin/users").status_code == 200  # A's admin session is untouched
    for customer_id in (a.customer_id, world.b.customer_id):
        assert not [entry for entry in api.audit_actions(customer_id) if entry.startswith("user.")]


def test_a_lists_never_show_up_for_b(world: _World) -> None:
    b = world.b.client
    for path in (
        "/api/documents",
        "/api/documents?q=sharma",
        "/api/conversations",
        "/api/review",
        f"/api/review?document_id={world.document}",
        "/api/admin/users",
        "/api/admin/audit",
    ):
        reply = b.get(path)
        assert reply.status_code == 200, path
        assert "sharma" not in reply.text.lower(), path
        assert world.document not in reply.text, path
        assert world.a.user_id not in reply.text, path

    # A keeps an older version and a family in Trash; B's numbers count neither.
    api, a = world.api, world.a
    api.add_document(a.customer_id, family_id=world.document, version_no=2, is_current=False)
    api.add_document(a.customer_id, name="sharma-binned.pdf", deleted_at=NOW, deleted_by=a.user_id)
    stats = b.get("/api/admin/stats").json()
    assert stats["documents_by_status"] == {}
    assert stats["questions_total"] == 0
    assert stats["open_review_items"] == 0
    assert (stats["older_versions"], stats["in_trash"]) == (0, 0)
    a_stats = a.client.get("/api/admin/stats").json()
    assert (a_stats["older_versions"], a_stats["in_trash"]) == (1, 1)
    assert b.get("/api/admin/settings").json()["customer_name"] == "Mehta Textiles"


def test_b_upload_of_a_same_file_is_not_a_duplicate_of_a(world: _World) -> None:
    reply = world.b.client.post(
        "/api/documents", files={"file": ("copy.pdf", PDF_BYTES, "application/pdf")}, headers=CSRF
    )
    assert reply.status_code == 201
    assert reply.json()["duplicate"] is False
    assert reply.json()["document"]["id"] != world.document


def test_answers_are_asked_for_the_session_customer_only(world: _World) -> None:
    world.b.client.post("/api/chat", json={"question": "anything"}, headers=CSRF)
    assert [customer for customer, _ in world.api.answers.asked] == [world.a.customer_id, world.b.customer_id]


def test_a_forged_cookie_naming_a_is_refused(world: _World) -> None:
    client = world.api.client()
    claims = SessionClaims(
        user_id=world.a.user_id,
        customer_id=world.a.customer_id,
        role="admin",
        scope="admin",
        session_version=1,
        expires_at=int(NOW.timestamp()) + 3600,
    )
    client.cookies.set(SESSION_COOKIE, sign_session(claims, "not-the-real-secret-but-long-enough-0000"))
    for path in ("/api/auth/me", f"/api/documents/{world.document}", "/api/admin/users"):
        reply = client.get(path)
        assert reply.status_code == 401
        assert reply.json()["error"]["code"] == "not_signed_in"


def test_b_session_with_a_customer_id_swapped_in_is_refused(world: _World) -> None:
    """A genuine token for B's user but naming A's customer (as if the secret had
    leaked) still finds no such user in A, so it is refused."""
    client = world.api.client()
    claims = SessionClaims(
        user_id=world.b.user_id,
        customer_id=world.a.customer_id,
        role="admin",
        scope="admin",
        session_version=1,
        expires_at=int(NOW.timestamp()) + 3600,
    )
    client.cookies.set(SESSION_COOKIE, sign_session(claims, world.api.settings.session_secret))
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/admin/users").status_code == 401
