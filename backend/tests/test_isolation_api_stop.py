"""Stop across customers (#713): another customer's id is not found.

Customer B's reviewer, using customer A's document id in every state, always gets 404, never 409,
and nothing is changed or audited. The control: A's own admin then gets the route's real answer
(stopped, or 409 for a state that can't be), so B's 404 comes from the customer, never from a
broken route or id.
"""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from tests.test_api_stop import stop, stored
from tests.test_api_support import Api

pytestmark = pytest.mark.db

STATES: list[dict[str, Any]] = [
    {"status": "received"},
    {"status": "clean"},
    {"status": "ready"},
    {"status": "failed", "failure_code": "corrupt_file"},
    {"status": "clean", "in_trash": True},
]


@pytest.mark.parametrize("state", STATES)
def test_b_stopping_a_document_of_a_is_404_in_every_state(db: Database, state: dict[str, Any]) -> None:
    api = Api(db)
    a_admin = api.new_signed("admin")
    b_reviewer = api.new_signed("reviewer")
    columns = {key: value for key, value in state.items() if key != "in_trash"}
    if state.get("in_trash"):
        columns.update(deleted_at=api.clock(), deleted_by=a_admin.user_id)
    doc = api.add_document(a_admin.customer_id, **columns)
    before = stored(api, a_admin.customer_id, doc)

    reply = stop(b_reviewer, doc)

    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert stored(api, a_admin.customer_id, doc) == before
    assert "document.stop" not in api.audit_actions(b_reviewer.customer_id)
    assert "document.stop" not in api.audit_actions(a_admin.customer_id)

    own = stop(a_admin, doc)
    if state["status"] in ("received", "clean") and not state.get("in_trash"):
        assert own.status_code == 200, own.text
        assert stored(api, a_admin.customer_id, doc)[:2] == ("failed", "stopped")
    else:
        assert own.status_code == 409, own.text
