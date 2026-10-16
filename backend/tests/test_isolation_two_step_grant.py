"""Two-step sign-in by grant across customers (0029), against PostgreSQL: the operator's
grant for one organisation, through its route, never changes how another's admins sign in,
nor its audit log; only the operator's function writes the grant: the app can neither set
it nor run it.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import new_customer, violation
from tests.test_api_support import CSRF, Api
from tests.test_operator_auth_db import _owner
from tests.test_operator_orgs_db import _signed_in

pytestmark = pytest.mark.db

_GRANTED = "SELECT two_step_granted FROM customers WHERE id = :c"
_AUDIT = (
    "SELECT action, details FROM audit_log WHERE customer_id = :c AND action LIKE 'org.two_step%' ORDER BY id"
)
_PLATFORM = (
    "SELECT action, subject_customer_id::text FROM platform_audit WHERE operator_id = :o"
    " AND action LIKE 'org.two_step%' ORDER BY id"
)


def test_the_operators_grant_for_one_organisation_never_changes_another(db: Database) -> None:
    """Allowed (audited in both logs), A's admin is asked for the code stored before; B's
    admin still signs in with the password alone. Stopped, A's password alone again."""
    api = Api(db)
    operator, client = _signed_in(api)
    a, _ = api.customer("Sharma Traders", two_step=False)
    b, _ = api.customer("Mehta Textiles", two_step=False)
    a_admin, b_admin = api.enrol_admin(a), api.enrol_admin(b)  # both turned it on earlier
    api.grant_two_step(a, False)
    api.grant_two_step(b, False)
    path = f"/api/operator/orgs/{a}/two-step"

    granted = client.put(path, json={"granted": True}, headers=CSRF)
    assert granted.json() == {"organisation": {"id": a, "two_step_granted": True, "changed": True}}
    assert client.put(path, json={"granted": True}, headers=CSRF).json()["organisation"]["changed"] is False
    assert api.admin_login(api.client(), a_admin.email).json()["error"]["code"] == "code_required"
    b_reply = api.admin_login(api.client(), b_admin.email)
    assert b_reply.status_code == 200, b_reply.text
    assert b_reply.json()["user"]["two_step_allowed"] is False
    orgs = {item["id"]: item["two_step_granted"] for item in client.get("/api/operator/orgs").json()["items"]}
    assert (orgs[a], orgs[b]) == (True, False)

    assert client.put(path, json={"granted": False}, headers=CSRF).json()["organisation"]["changed"] is True
    assert api.admin_login(api.client(), a_admin.email).status_code == 200
    by = {"by": "operator"}
    assert _owner(db, _AUDIT, {"c": a}) == [("org.two_step_grant", by), ("org.two_step_revoke", by)]
    assert _owner(db, _AUDIT, {"c": b}) == []
    assert _owner(db, _PLATFORM, {"o": operator.id}) == [
        ("org.two_step_grant", a),
        ("org.two_step_revoke", a),
    ]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE customers SET two_step_granted = true WHERE id = :c",
        "SELECT * FROM kaagaz_op_set_org_two_step(gen_random_uuid(), :c, true)",
    ],
)
def test_the_app_can_neither_grant_two_step_nor_run_the_operators_function(
    db: Database, statement: str
) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), {"c": customer})
    assert violation(refused.value)[0] == "42501"  # insufficient_privilege
    assert _owner(db, _GRANTED, {"c": customer}) == [(False,)]
