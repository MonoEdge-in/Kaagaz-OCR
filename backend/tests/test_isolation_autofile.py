"""Auto-filing across customers (feature 28, council 15 Oct 2026; 0025), against PostgreSQL:
one customer's type folder is never used for another's files, filing named with the wrong
customer changes nothing, another customer's Undo is "not found", and only the platform
operator's function grants auto-filing: the app can't set the grant, can't switch it on
without it, and can't run the function. The operator's grant and stop, through its route.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import autofile
from kaagaz.db.engine import Database
from tests.db_support import new_customer, violation
from tests.test_api_support import CSRF, Api
from tests.test_autofile_db import folders, organisation, read, state, upload, view
from tests.test_operator_auth_db import _owner
from tests.test_operator_orgs_db import _signed_in

pytestmark = pytest.mark.db

ORGS = "/api/operator/orgs"
_CHANGES = (
    "SELECT action, actor_user_id, details FROM audit_log WHERE customer_id = :c"
    " AND action LIKE 'org.autofile%' ORDER BY id"
)
_PLATFORM = (
    "SELECT action, subject_customer_id::text FROM platform_audit WHERE operator_id = :o"
    " AND action LIKE 'org.autofile%' ORDER BY id"
)


def test_one_customers_type_folder_is_never_used_for_another_customers_file(db: Database) -> None:
    a, a_admin = organisation(db)
    b, b_admin = organisation(db)
    a_doc, b_doc = upload(db, a, a_admin), upload(db, b, b_admin)
    read(db, a, a_doc)
    read(db, b, b_doc)

    [(a_folder, _, _, a_key, a_owner)] = folders(db, a)
    [(b_folder, _, _, b_key, b_owner)] = folders(db, b)
    assert a_folder != b_folder and a_key == b_key == "type:invoice"  # the same key in each customer
    assert (a_owner, b_owner) == (a_admin, b_admin)
    assert (state(db, a, a_doc), state(db, b, b_doc)) == (("filed", "Invoices"),) * 2


def test_filing_named_with_the_wrong_customer_changes_nothing(db: Database) -> None:
    a, a_admin = organisation(db)
    b, _ = organisation(db)
    a_doc = upload(db, a, a_admin)
    with db.customer(a) as conn:
        conn.execute(
            text(
                "UPDATE documents SET doc_type = 'invoice', status = 'ready'"
                " WHERE customer_id = :c AND id = :d"
            ),
            {"c": a, "d": a_doc},
        )
    with db.customer(b) as conn:
        assert autofile.file_document(conn, b, a_doc) is None
        assert autofile.undo(conn, b, a_doc) is None
    assert (state(db, a, a_doc), folders(db, a), folders(db, b)) == (("pending", None), [], [])


def test_another_customers_undo_is_not_found_and_its_screens_never_show_the_filing(db: Database) -> None:
    api = Api(db)
    a, a_admin = organisation(db)
    b, _ = organisation(db)
    a_doc = upload(db, a, a_admin)
    read(db, a, a_doc)
    b_user = api.sign_in(b)

    undo = b_user.client.post(f"/api/documents/{a_doc}/autofile/undo", headers=CSRF)
    listed = b_user.client.get("/api/documents").json()["items"]

    assert undo.status_code == 404
    assert a_doc not in [item["id"] for item in listed]
    assert (state(db, a, a_doc), view(db, a, a_doc)) == (("filed", "Invoices"), "filed")
    assert b_user.client.get(f"/api/documents/{a_doc}").status_code == 404


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE customers SET autofile_granted = true WHERE id = :c",
        "SELECT * FROM kaagaz_op_set_org_autofile(gen_random_uuid(), :c, true)",
    ],
)
def test_the_app_can_neither_grant_auto_filing_nor_run_the_operators_function(
    db: Database, statement: str
) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), {"c": customer})
    assert violation(refused.value)[0] == "42501"  # insufficient_privilege
    assert _owner(db, "SELECT autofile_granted FROM customers WHERE id = :c", {"c": customer}) == [(False,)]


def test_the_app_can_not_switch_auto_filing_on_without_the_grant(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text("UPDATE customers SET autofile_on = true WHERE id = :c"), {"c": customer})
    assert violation(refused.value) == ("23514", "customers_autofile_check")


def test_the_operator_grants_and_stops_auto_filing_which_switches_it_off_and_is_audited(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    customer, _ = api.customer("Org Filing")
    path = f"{ORGS}/{customer}/autofile"

    granted = client.put(path, json={"granted": True}, headers=CSRF)
    assert granted.json() == {"organisation": {"id": customer, "autofile_granted": True, "changed": True}}
    assert client.put(path, json={"granted": True}, headers=CSRF).json()["organisation"]["changed"] is False
    _owner(db, "UPDATE customers SET autofile_on = true WHERE id = :c", {"c": customer})  # its admin's switch
    stopped = client.put(path, json={"granted": False}, headers=CSRF)
    assert stopped.json()["organisation"]["changed"] is True

    [org] = [item for item in client.get(ORGS).json()["items"] if item["id"] == customer]
    assert (org["autofile_granted"], org["autofile_on"]) == (False, False)  # stopping it switched it off
    assert _owner(db, _CHANGES, {"c": customer}) == [
        ("org.autofile_grant", None, {"by": "operator"}),
        ("org.autofile_revoke", None, {"by": "operator"}),
    ]
    assert _owner(db, _PLATFORM, {"o": operator.id}) == [
        ("org.autofile_grant", customer),
        ("org.autofile_revoke", customer),
    ]
    assert (
        client.put(f"{ORGS}/{uuid.uuid4()}/autofile", json={"granted": True}, headers=CSRF).status_code == 404
    )


def test_the_operator_can_not_grant_auto_filing_to_an_organisation_being_deleted(db: Database) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    customer, _ = api.customer("Org Leaving")
    _owner(
        db,
        "UPDATE customers SET status = 'deleting', disabled_at = now(),"
        " delete_after = now() + interval '7 days' WHERE id = :c",
        {"c": customer},
    )
    refused = client.put(f"{ORGS}/{customer}/autofile", json={"granted": True}, headers=CSRF)
    assert refused.status_code == 409
    assert _owner(db, "SELECT autofile_granted FROM customers WHERE id = :c", {"c": customer}) == [(False,)]
