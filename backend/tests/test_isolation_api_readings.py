"""Scanned text across customers: B, signed in, asks for A's page with A's exact
ids and gets "not found" with none of A's text. The control: A asks the same and
gets the lines, so B's 404 comes from the customer, never from an empty fixture.
"""

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.reading.spans import BoxLocation, Span
from tests.test_api_support import Api

pytestmark = pytest.mark.db


def test_b_gets_404_and_no_text_for_a_page_of_a(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    document_id = api.add_document(a.customer_id, detected_type="png", name="sharma.png")
    api.add_page(a.customer_id, document_id, unit="px")
    span = span_to_json(Span("sharma line", "sharma line", BoxLocation(1, 0.1, 0.1, 0.9, 0.2), 0.95))
    api.add_reader_output(a.customer_id, document_id, [span])
    path = f"/api/documents/{document_id}/pages/1/readings"

    own = a.client.get(path)
    assert own.status_code == 200  # the control
    assert own.json()["readers"][0]["lines"][0]["text"] == "sharma line"

    other = b.client.get(path)
    assert other.status_code == 404
    assert other.json()["error"] == {"code": "not_found", "message": "We couldn't find that."}
    assert "sharma" not in other.text

    signed_out = api.client().get(path)
    assert signed_out.status_code == 401
    assert "sharma" not in signed_out.text
