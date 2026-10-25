"""Verify all across customers: customer A's Verify all never touches customer B's values.

A's reviewer runs Verify all, with no filter and with B's document id: only A's values
are verified, and B's values, corrections and audit log are exactly as they were. The
control: B's own reviewer then verifies B's value, so B's rows were reachable.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api

pytestmark = pytest.mark.db


def _snapshot(api: Api, customer_id: str) -> tuple[object, ...]:
    with api.db.customer(customer_id) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT (SELECT string_agg(v.id::text || v.value_text || v.label, ',' ORDER BY v.id)"
                    " FROM extracted_values v WHERE v.customer_id = :c),"
                    " (SELECT count(*) FROM corrections WHERE customer_id = :c),"
                    " (SELECT count(*) FROM audit_log WHERE customer_id = :c)"
                ),
                {"c": customer_id},
            ).one()
        )


@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_as_verify_all_never_touches_bs_values(db: Database, role: str) -> None:
    api = Api(db)
    b = api.new_signed("reviewer")
    b_doc = api.add_document(b.customer_id, name="mehta-secret.pdf")
    api.add_page(b.customer_id, b_doc)
    b_value = api.add_value(b.customer_id, b_doc)
    a = api.new_signed(role)
    a_doc = api.add_document(a.customer_id, name="sharma.pdf")
    api.add_page(a.customer_id, a_doc)
    api.add_value(a.customer_id, a_doc)
    before = _snapshot(api, b.customer_id)

    with_bs_document = a.client.post("/api/review/verify-all", json={"document_id": b_doc}, headers=CSRF)
    everything = a.client.post("/api/review/verify-all", json={}, headers=CSRF)

    assert with_bs_document.json() == {"verified": 0, "skipped": [], "left": 0}
    assert everything.json() == {"verified": 1, "skipped": [], "left": 0}
    assert _snapshot(api, b.customer_id) == before

    own = b.client.post("/api/review/verify-all", json={}, headers=CSRF)
    assert own.json() == {"verified": 1, "skipped": [], "left": 0}
    with api.db.customer(b.customer_id) as conn:
        label = conn.execute(
            text("SELECT label FROM extracted_values WHERE id = :v"), {"v": b_value}
        ).scalar_one()
    assert label == "verified"
