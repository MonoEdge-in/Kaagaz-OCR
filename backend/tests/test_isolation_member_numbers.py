"""Each member's numbers (#386, ``/admin/members``) across organisations: an admin sees the
people of their own organisation only, with what those people did only, whatever another
organisation's people uploaded, asked or verified; nobody but an admin's session gets the
table. The numbers themselves: ``test_analytics_recount_db.py``."""

import pytest

from kaagaz.db.engine import Database
from tests.test_analytics_recount_db import THIS_MONTH, _document, _replies, expected_members, plant, windows
from tests.test_api_support import Api

pytestmark = pytest.mark.db

MEMBERS = "/api/admin/members"


def test_an_admin_sees_only_their_own_organisations_members_and_numbers(db: Database) -> None:
    api = Api(db)
    admin_a, admin_b = api.new_admin("Members A"), api.new_admin("Members B")
    a = plant(api, admin_a)
    before = {period: admin_a.client.get(MEMBERS, params={"period": period}).json() for period in windows()}
    b = plant(api, admin_b)
    _document(db, b, "m1", THIS_MONTH, [("text_layer", "text_layer")] * 9)
    _replies(db, b, "m1", THIS_MONTH, ["answered"] * 4)
    b_reply = admin_b.client.get(MEMBERS).json()
    assert {row["user_id"] for row in b_reply["members"]} == set(b.people.values())  # the control
    b_emails = [row["email"] for row in b_reply["members"]]

    for period, window in windows().items():
        reply = admin_a.client.get(MEMBERS, params={"period": period})
        assert reply.status_code == 200, reply.text
        assert reply.json() == before[period], period  # B's work changed nothing of A's
        listed = {row["user_id"] for row in reply.json()["members"]}
        assert listed == set(expected_members(db, a.customer_id, window)) == set(a.people.values())
        for hidden in (*b.people.values(), *b_emails, b.customer_id):
            assert hidden not in reply.text, (period, hidden)


def test_only_an_admins_session_gets_the_members_table(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin("Members C")
    for role in ("member", "reviewer"):
        person = api.sign_in(admin.customer_id, role)
        reply = person.client.get(MEMBERS)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "admin_session_required"), role
    assert api.client().get(MEMBERS).status_code == 401  # nobody signed in
    assert admin.client.get(MEMBERS, params={"period": "year"}).status_code in (400, 422)
