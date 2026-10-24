"""Your words on the page across customers: B's words never return A's boxes, and B asking
for a page of A's document with A's exact id gets "not found". The control: A asks the
same and gets the boxes, so B's 404 comes from the customer, never from an empty fixture.
"""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_page_words import add_pieces, box, sale_deed, words
from tests.test_api_support import Api

pytestmark = pytest.mark.db


def test_b_never_gets_a_boxes_and_a_document_is_not_found_for_b(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    a_deed = sale_deed(api, a.customer_id)
    b_deed = api.add_document(b.customer_id, detected_type="png", name="lease.png")
    add_pieces(db, b.customer_id, b_deed, [("Tenant: Nikhil Varde", [box(1, 0.7)])])
    path = f"/api/documents/{a_deed}/words?w=krishna&w=maval"

    assert words(a, a_deed, "krishna", "maval")["pages"] == [1, 2]  # the control

    other = b.client.get(path)
    assert other.status_code == 404
    assert other.json()["error"]["code"] == "not_found"
    assert "0.2" not in other.text and "pages" not in other.text

    # B's own document with A's words: only B's lines, never A's.
    assert words(b, b_deed, "krishna", "maval", "sohoni") == {"boxes": [], "pages": []}
    assert words(b, b_deed, "varde")["boxes"] == [box(1, 0.7)]

    assert api.client().get(path).status_code == 401
