"""Auto-filing across customers (feature 28, council 15 Oct 2026; 0025), against PostgreSQL:
one customer's type folder is never used for another's files, filing named with the wrong
customer changes nothing, another customer's Undo is "not found", and only the platform
operator's function grants auto-filing: the app can't set the grant, can't switch it on
without it, and can't run the function. The operator's grant and stop, through its route.
Party folders (28b): one customer's invoices never decide another's own GSTIN or folders.
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
from tests.test_autofile_party import MEHTA
from tests.test_autofile_party_db import bill, history, path
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

    # Changed on purpose (#217, 21 Oct 2026: department-first auto-filing): each customer's
    # Invoices folder is made inside its own new "Accounts and HR" department, and its key
    # names that department (type:invoice:<department id>), so the two keys differ too.
    a_department, a_invoices = folders(db, a)  # by name
    b_department, b_invoices = folders(db, b)
    for department, invoices, admin in (
        (a_department, a_invoices, a_admin),
        (b_department, b_invoices, b_admin),
    ):
        assert department[1:] == ("Accounts and HR", None, None, admin)
        assert invoices[1:] == ("Invoices", department[0], f"type:invoice:{department[0]}", admin)
    assert a_department[0] != b_department[0] and a_invoices[0] != b_invoices[0]
    assert (state(db, a, a_doc), state(db, b, b_doc)) == (("filed", "Invoices"),) * 2


def test_one_customers_invoices_and_party_folders_never_count_for_another(db: Database) -> None:
    """Party folders (28b, final polish 16 Oct 2026): A's invoices make A's own GSTIN clear
    and A has a folder for Mehta; B, with no invoices of its own, gets neither: its bill
    from Mehta stays in B's Invoices."""
    a, a_admin = organisation(db)
    b, b_admin = organisation(db)
    history(db, a)
    a_bill = bill(db, a, a_admin, ("Mehta Textiles", MEHTA))
    b_bill = bill(db, b, b_admin, ("Mehta Textiles", MEHTA))

    assert path(db, a, a_bill) == ("Invoices", "Mehta Textiles")
    # Changed on purpose (#217, 21 Oct 2026: department-first auto-filing): B's Invoices is
    # inside B's own "Accounts and HR" department; still no party folder for B.
    assert path(db, b, b_bill) == ("Accounts and HR", "Invoices")
    department, invoices = folders(db, b)  # by name: only these two, no Mehta Textiles
    assert (department[3], invoices[3]) == (None, f"type:invoice:{department[0]}")


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
