"""A Keycloak sign-in (#435) gives a session for the signed-in user's own organisation only:
the organisation comes from that user's row, never from anything the request carries, so
customer A's user can never get a session for customer B, nor reach B's documents."""

import pytest

from kaagaz.db.engine import Database
from tests.keycloak_fake import FakeRealm, claims_for, keycloak_settings, sign, use_realm
from tests.test_api_keycloak import CALLBACK, NOW_UNIX, start
from tests.test_api_support import Api

pytestmark = pytest.mark.db


def test_a_users_keycloak_sign_in_opens_their_own_organisation_only(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    realm: FakeRealm = use_realm(monkeypatch)
    api = Api(db, settings=keycloak_settings())
    a_customer, _ = api.customer("Sharma Traders")
    b_customer, b_admin = api.customer("Mehta Textiles")
    a_user, a_email = api.add_user(a_customer, "admin")
    b_document = api.add_document(b_customer, name="b-invoice.pdf")
    b_audit = api.audit_rows(b_customer)

    client = api.client()
    sent = start(client, f"/documents/{b_document}")
    code = realm.issue(sign(claims_for(a_email, sent["nonce"], NOW_UNIX)), sent["code_challenge"])
    # Anything naming B on the way back is ignored.
    params = {"state": sent["state"], "code": code, "customer_id": b_customer, "cid": b_customer}
    reply = client.get(CALLBACK, params=params, follow_redirects=False)
    assert reply.status_code == 303, reply.text

    me = client.get("/api/auth/me").json()
    assert (me["user"]["id"], me["customer"]["id"]) == (a_user, a_customer)
    assert client.get(f"/api/documents/{b_document}").status_code == 404
    assert b_document not in client.get("/api/documents").text
    users = {row["id"] for row in client.get("/api/admin/users").json()["items"]}
    assert a_user in users and b_admin not in users
    assert api.audit_rows(b_customer) == b_audit  # B's log never hears of A's sign-in
