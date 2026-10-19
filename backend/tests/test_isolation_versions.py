"""Customer B uses customer A's version suggestion and document ids on the version routes
(Phase 2 PRD 1, V6, V7, V10, V11, V5's tray) and gets 404, never A's data; A's suggestion,
documents and families are unchanged afterwards. And once through db/versions.py as B, so a
statement that forgot the customer fails. B's own pair is never a candidate of A's.
"""

import uuid

import pytest
from sqlalchemy import text

from kaagaz.db import versions as versions_sql
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed
from tests.test_versions_db import _satbaras, _spot, _suggestion_id, _suggestions

pytestmark = pytest.mark.db


def _state(db: Database, customer: str) -> list[tuple[object, ...]]:
    with db.customer(customer) as conn:
        documents = conn.execute(
            text("SELECT id, family_id, version_no, is_current, folder_id FROM documents ORDER BY id")
        ).all()
    return [tuple(row) for row in documents] + _suggestions(db, customer)


def test_customer_b_can_not_accept_reject_undo_make_current_or_compare_as_customer_a(db: Database) -> None:
    api = Api(db)
    a: Signed = api.new_signed()
    b: Signed = api.new_signed()
    old, new = _satbaras(api, db, a)
    b_old, b_new = _satbaras(api, db, b)  # the same texts in B: never a candidate of A's
    _spot(db, a.customer_id, new)
    _spot(db, b.customer_id, b_new)
    assert [row[:2] for row in _suggestions(db, a.customer_id)] == [(new, old)]
    assert [row[:2] for row in _suggestions(db, b.customer_id)] == [(b_new, b_old)]
    suggestion = _suggestion_id(db, a.customer_id, new)
    before = _state(db, a.customer_id)

    for path in (
        f"/api/version-suggestions/{suggestion}/accept",
        f"/api/version-suggestions/{suggestion}/reject",
    ):
        reply = b.client.post(path, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found"), path
    for path in (f"/api/documents/{new}/version-undo", f"/api/documents/{old}/make-current"):
        assert b.client.post(path, headers=CSRF).status_code == 404, path
    assert b.client.get(f"/api/documents/{new}/compare", params={"with": old}).status_code == 404
    assert b.client.get(f"/api/documents/{b_new}/compare", params={"with": old}).status_code == 404
    assert b.client.get(f"/api/batches/{uuid.uuid4()}").status_code == 404

    with db.customer(b.customer_id) as conn:
        assert versions_sql.for_document(conn, b.customer_id, new, old) is None
        assert versions_sql.may_compare(conn, b.customer_id, new, old) is None
        for change in (versions_sql.link, versions_sql.reject):
            with pytest.raises(versions_sql.Refused):
                change(conn, b.customer_id, suggestion, by=b.user_id)
    assert _state(db, a.customer_id) == before

    accepted = a.client.post(f"/api/version-suggestions/{suggestion}/accept", headers=CSRF)
    assert accepted.status_code == 200, accepted.text  # A's own still works
    rejected = b.client.post(f"/api/version-suggestions/{_suggestion_id(db, b.customer_id, b_new)}/reject",
                             headers=CSRF)  # fmt: skip
    assert rejected.status_code == 200
    assert _suggestions(db, a.customer_id)[0][3] == "accepted"
