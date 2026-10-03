"""My words through the whole API: another user, of the same customer or another
one, never reaches A1's words or spots.

A1 has a word with a second meaning and a spot on page 2 of a document. A2 (a
member of the same customer) and B1 (customer B) list, change, delete and replace
with A1's exact ids, and ask for A1's spots: they get nothing (an empty list, or
404), and A1's words and spots are exactly as they were. The control: A1 itself
then does each of those with the same ids and gets through, so the 404s come
from the user, never from a broken route or id.
"""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from tests.test_api_my_words import BOX, error, listed, post
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db


def _spots(signed: Signed, document: str) -> Any:
    return signed.client.get(f"/api/documents/{document}/my-words?page=2")


def _reaches_nothing(other: Signed, word_id: str, document: str, *, same_customer: bool) -> None:
    path = f"/api/me/words/{word_id}"
    assert listed(other) == []
    assert error(other.client.patch(path, json={"meaning": "thief"}, headers=CSRF)) == (404, "not_found")
    assert error(other.client.delete(path, headers=CSRF)) == (404, "not_found")
    assert error(post(other, "malak", "thief", replace_id=word_id)) == (404, "not_found")
    if same_customer:  # the document is A's, so A2 may open it, but sees none of A1's spots
        assert _spots(other, document).json() == {"items": []}
    else:  # another customer's document is not found at all
        assert error(_spots(other, document)) == (404, "not_found")
        spot = {"document_id": document, "page_no": 2, "region": BOX}
        assert error(post(other, "malak", "owner", document_ids=[document])) == (404, "not_found")
        assert error(post(other, "malak", "owner", spot=spot)) == (404, "not_found")
        assert listed(other) == []


def test_a2_and_b1_reach_none_of_a1s_words_and_a1_still_does(db: Database) -> None:
    api = Api(db)
    a1 = api.new_signed()
    a2 = api.sign_in(a1.customer_id)
    b1 = api.new_signed()
    document = api.add_document(a1.customer_id, page_count=3)
    spot = {"document_id": document, "page_no": 2, "region": BOX}
    word_id = post(a1, "malak", "owner", spot=spot).json()["word"]["id"]
    assert post(a1, "malak", "boss", confirm_another=True).status_code == 201
    before = (listed(a1), _spots(a1, document).json())

    _reaches_nothing(a2, word_id, document, same_customer=True)
    _reaches_nothing(b1, word_id, document, same_customer=False)
    # A2's own "malak" asks nothing about A1's meanings: the words are per user.
    assert post(a2, "malak", "thief").status_code == 201

    assert (listed(a1), _spots(a1, document).json()) == before
    assert [item["word_id"] for item in before[1]["items"]] == [word_id]
    path = f"/api/me/words/{word_id}"  # the control: A1 gets through with the same ids
    assert post(a1, "malak", "landlord", replace_id=word_id).json()["outcome"] == "replaced"
    assert a1.client.patch(path, json={"meaning": "owner"}, headers=CSRF).status_code == 200
    assert a1.client.delete(path, headers=CSRF).status_code == 204
    assert listed(a1) == [("malak", "boss")]
    assert listed(a2) == [("malak", "thief")]
