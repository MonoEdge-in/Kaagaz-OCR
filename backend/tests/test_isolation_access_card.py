"""#701 (night meeting, 19 Nov 2026): Home's one-time card ("Everyone can see every file today.")
hides once the organisation keeps any file to some people, a private folder nobody named the admin
on included; the reply is still only yes or no. Another organisation's private folder never hides
it. With PRD 7's database rule on and off."""

import pytest

from kaagaz.db.engine import Database
from tests.db_support import insert_folder, rule_off, rule_on
from tests.test_api_support import Api, Signed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db


def _keeps(admin: Signed) -> object:
    reply = admin.client.get("/api/admin/departments")
    assert reply.status_code == 200, reply.text
    return reply.json()["keeps_files_to_some_people"]


def _private_vault(api: Api, customer_id: str, user_id: str) -> None:
    with api.db.customer(customer_id) as conn:
        vault = insert_folder(conn, customer_id, user_id, "Vault")
    _owner(api.db, "UPDATE folders SET private = true WHERE id = :f", {"f": vault})


@pytest.mark.parametrize("rule", ["on", "off"])
def test_a_private_folder_the_admin_is_not_named_on_hides_the_card(db: Database, rule: str) -> None:
    api = Api(db)
    c, first = api.customer()
    admin = api.admin_sign_in(c)
    with rule_on() if rule == "on" else rule_off():
        assert _keeps(admin) is False
        _private_vault(api, c, first)  # made by another admin, and nobody named on it

        assert _keeps(admin) is True


@pytest.mark.parametrize("rule", ["on", "off"])
def test_another_organisations_private_folder_never_hides_the_card(db: Database, rule: str) -> None:
    api = Api(db)
    admin, other = api.new_admin("Ours"), api.new_admin("Theirs")
    with rule_on() if rule == "on" else rule_off():
        _private_vault(api, other.customer_id, other.user_id)

        assert _keeps(admin) is False
        assert _keeps(other) is True
