"""Customer B, signed in as an admin, uses customer A's exact document ids on the
trash routes (PRD 1 F5) and gets 404, and A's documents are unchanged.

B signs in on the admin screen and proves it fresh, so "delete forever" (an admin session only)
reaches the lookup instead of stopping at the session check. A has one family
in Trash and one live document, so each route is tried on the state it would
act on. This runs through the whole API (session, routes, SQL and row level
security). The control: A's own admin then gets through each of the three routes
with those ids, so B's 404s come from the customer, never from a broken route or id.
"""

from datetime import datetime

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, NOW, Api

pytestmark = pytest.mark.db

NOT_FOUND = {"error": {"code": "not_found", "message": "We couldn't find that."}}


def _state(db: Database, customer_id: str) -> dict[str, datetime | None]:
    """Each of the customer's documents and its deleted_at."""
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text("SELECT id, deleted_at FROM documents WHERE customer_id = :c"), {"c": customer_id}
        )
        return {str(row[0]): row[1] for row in rows}


def test_b_gets_404_on_every_trash_route_with_a_s_ids_and_a_is_unchanged(db: Database) -> None:
    api = Api(db)
    a = api.admin_sign_in(api.customer("Sharma Traders")[0])
    b = api.admin_sign_in(api.customer("Mehta Textiles")[0])
    binned = api.add_document(a.customer_id, uploaded_by=a.user_id, deleted_at=NOW, deleted_by=a.user_id)
    older = api.add_document(
        a.customer_id, family_id=binned, version_no=2, is_current=False, deleted_at=NOW, deleted_by=a.user_id
    )
    live = api.add_document(a.customer_id, uploaded_by=a.user_id)
    before = _state(db, a.customer_id)

    # Changed on purpose (#737, 21 Nov 2026): delete forever needs a fresh sign-in proof first; B has
    # it, so B still reaches the lookup and gets 404.
    replies = [
        b.client.delete(f"/api/documents/{live}", headers=CSRF),
        b.client.post(f"/api/documents/{binned}/restore", headers=CSRF),
    ]
    for document in (binned, older):
        api.prove(b, "delete_forever")
        replies.append(b.client.delete(f"/api/documents/{document}/permanent", headers=CSRF))

    assert [(reply.status_code, reply.json()) for reply in replies] == [(404, NOT_FOUND)] * 4
    assert _state(db, a.customer_id) == before
    assert all(api.storage.has(a.customer_id, document) for document in (binned, older, live))
    assert _state(db, b.customer_id) == {}

    assert a.client.post(f"/api/documents/{binned}/restore", headers=CSRF).status_code == 200
    assert a.client.delete(f"/api/documents/{live}", headers=CSRF).status_code == 204
    # Changed on purpose (#737, 21 Nov 2026): the control also needs the proof.
    api.prove(a, "delete_forever")
    assert a.client.delete(f"/api/documents/{live}/permanent", headers=CSRF).status_code == 204
    assert _state(db, a.customer_id) == {binned: None, older: None}
    assert not api.storage.has(a.customer_id, live)
