"""Read again across customers (PRD 2 X1): another customer's id is not found.

Customer B's reviewer, using customer A's document id in every state, always gets
404, never 409 or 429, and nothing is changed, audited, queued or run.
"""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api

pytestmark = pytest.mark.db

STATES: list[dict[str, Any]] = [
    {"status": "ready"},
    {"status": "failed", "failure_code": "corrupt_file"},
    {"status": "needs_reader"},
    {"status": "received"},
    {"status": "clean"},
    {"status": "rejected", "failure_code": "virus_found"},
    {"status": "ready", "in_trash": True},
]


def snapshot(api: Api, customer_id: str, document_id: str) -> tuple[object, ...]:
    with api.db.customer(customer_id) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT status, failure_code, attempts, deleted_at,"
                    " (SELECT count(*) FROM processing_runs r WHERE r.customer_id = d.customer_id"
                    " AND r.document_id = d.id),"
                    " (SELECT count(*) FROM audit_log a WHERE a.customer_id = d.customer_id"
                    " AND a.action = 'document.reprocess')"
                    " FROM documents d WHERE d.customer_id = :c AND d.id = :d"
                ),
                {"c": customer_id, "d": document_id},
            ).one()
        )


@pytest.mark.parametrize("state", STATES)
def test_b_reading_a_document_of_a_again_is_404_in_every_state(db: Database, state: dict[str, Any]) -> None:
    api = Api(db)
    a_admin = api.new_signed("admin")
    b_reviewer = api.new_signed("reviewer")
    columns = {key: value for key, value in state.items() if key != "in_trash"}
    if state.get("in_trash"):
        columns.update(deleted_at=api.clock(), deleted_by=a_admin.user_id)
    doc = api.add_document(a_admin.customer_id, attempts=2, **columns)
    before = snapshot(api, a_admin.customer_id, doc)

    reply = b_reviewer.client.post(f"/api/documents/{doc}/reprocess", headers=CSRF)

    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert snapshot(api, a_admin.customer_id, doc) == before
    assert api.queue.jobs == []
    assert "document.reprocess" not in api.audit_actions(b_reviewer.customer_id)
    assert b_reviewer.client.get(f"/api/documents/{doc}").status_code == 404  # reviewer_fixes too
