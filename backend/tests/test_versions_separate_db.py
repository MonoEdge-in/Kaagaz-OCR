"""Make it a separate file (#521, Tushar, 6 Nov 2026): any version of a family taken out as its own
document, in the family's folder, under a new name; its reading, values and history stay (no read
again, no AI). The current one leaving makes the highest ready one left current; the pair is
never suggested again (the keep-separate row); one ``document.version_separated`` audit row of
ids. Undo puts it back through Accept. Any user of the customer may, as Accept; another
organisation's id is 404. The pure name rule: frontend tests (versions/words.ts)."""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import versions as versions_sql
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed
from tests.test_versions import _SATBARA
from tests.test_versions_db import _doc, _family, _old

pytestmark = pytest.mark.db
_ROW = (
    "SELECT display_name, family_id::text, version_no, is_current, folder_id::text FROM documents"
    " WHERE id = :d"
)


def _row(db: Database, customer: str, document: str) -> tuple[Any, ...]:
    with db.customer(customer) as conn:
        return tuple(conn.execute(text(_ROW), {"d": document}).one())


def _separate(signed: Signed, document: str, name: str) -> Any:
    return signed.client.post(f"/api/documents/{document}/separate", json={"name": name}, headers=CSRF)


def _family_of_three(api: Api, signed: Signed) -> tuple[str, str, str, str]:
    """Lease.pdf: version 1 and 2 ready and not current, version 3 current, in one folder."""
    customer, user = signed.customer_id, signed.user_id
    folder = api.add_folder(customer, user, "Leases")
    first = api.add_document(customer, uploaded_by=user, name="Lease.pdf", is_current=False, folder_id=folder)
    second = api.add_document(
        customer, uploaded_by=user, name="Lease draft.pdf", family_id=first, version_no=2, is_current=False,
        folder_id=folder,
    )  # fmt: skip
    third = api.add_document(
        customer, uploaded_by=user, name="Lease.pdf", family_id=first, version_no=3, folder_id=folder
    )
    return folder, first, second, third


def test_the_current_version_made_separate_leaves_the_highest_ready_one_current(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()  # a member: as Accept, any user of the customer
    customer = signed.customer_id
    folder, first, second, third = _family_of_three(api, signed)
    jobs = len(api.queue.jobs)
    reply = _separate(signed, third, "Lease Patil.pdf")
    assert reply.status_code == 200, reply.text
    assert (reply.json()["document"]["id"], reply.json()["family_id"]) == (third, first)
    assert _row(db, customer, third) == ("Lease Patil.pdf", third, 1, True, folder)
    assert _row(db, customer, second)[1:4] == (first, 2, True)  # the next is current
    assert _row(db, customer, first)[3] is False
    assert len(api.queue.jobs) == jobs  # no read again, so no AI
    separated = [row for row in api.audit_rows(customer) if row[0] == "document.version_separated"]
    assert separated == [
        ("document.version_separated", "document", third, {"family_id": first, "version_no": 3})
    ]


def test_version_1_made_separate_leaves_the_rest_one_family_under_version_2(db: Database) -> None:
    """Version 1's id is its family's: the versions left take version 2's id as theirs."""
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    folder, first, second, third = _family_of_three(api, signed)
    reply = _separate(signed, first, "Lease 2025.pdf")
    assert reply.status_code == 200, reply.text
    assert reply.json()["family_id"] == second
    assert _row(db, customer, first) == ("Lease 2025.pdf", first, 1, True, folder)
    assert _row(db, customer, second)[1:4] == (second, 2, False)
    assert _row(db, customer, third)[1:4] == (second, 3, True)  # still current


def test_a_name_clash_a_bad_name_and_a_single_version_are_refused(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, first, _, third = _family_of_three(api, signed)
    clash = _separate(signed, third, "Lease.pdf")  # version 1 has it, in the same folder
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "name_taken"
    assert clash.json()["error"]["message"] == 'A file named "Lease.pdf" is already in this folder.'
    for bad in ("", "   ", "Lease‮gnp.pdf", "x" * 256):
        refused = _separate(signed, third, bad)
        assert (refused.status_code, refused.json()["error"]["code"]) == (400, "bad_request"), bad
    alone = api.add_document(signed.customer_id, uploaded_by=signed.user_id, name="Alone.pdf")
    only = _separate(signed, alone, "Alone 2.pdf")
    assert (only.status_code, only.json()["error"]["code"]) == (409, "only_version")
    assert _row(db, signed.customer_id, third)[1:3] == (first, 3)  # nothing changed


def test_the_pair_is_never_suggested_again_and_undo_puts_it_back_as_the_newest(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    first = _doc(api, signed, list(_SATBARA), "Satbara.pdf", hours_ago=48)
    _old(db, customer, first)
    second = _doc(
        api,
        signed,
        list(_SATBARA),
        "Satbara 2026.pdf",
        hours_ago=0,
        family_id=first,
        version_no=2,
        is_current=False,
    )
    _old(db, customer, second)
    reply = _separate(signed, second, "Satbara Patil.pdf")
    assert reply.status_code == 200, reply.text
    with db.customer(customer) as conn:  # the same text, alone, first ready: never suggested
        assert versions_sql.spot(conn, customer, second) == "none"
    undone = signed.client.post(
        f"/api/documents/{second}/separate/undo", json={"family_id": first}, headers=CSRF
    )
    assert undone.status_code == 200, undone.text
    assert _family(db, customer, second)[:3] == (first, 2, True)
    again = signed.client.post(
        f"/api/documents/{second}/separate/undo", json={"family_id": first}, headers=CSRF
    )
    assert (again.status_code, again.json()["error"]["code"]) == (404, "not_found")


def test_another_organisations_version_is_404_and_unchanged(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    _, first, _, third = _family_of_three(api, a)
    reply = _separate(b, third, "Mine.pdf")
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    undo = b.client.post(f"/api/documents/{third}/separate/undo", json={"family_id": first}, headers=CSRF)
    assert undo.status_code == 404
    assert _row(db, a.customer_id, third)[1:4] == (first, 3, True)
