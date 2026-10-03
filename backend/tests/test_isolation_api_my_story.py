"""A story through the whole API: nobody but its author reaches it.

A1 has an embedded story on a family of two versions. A2 (a member of the same
customer) may open the document, but its GET finds no story, its DELETE is
404, and its PUT writes A2's own row, leaving A1's story and vector as they
were. B1 (customer B) gets 404 for every route. The control: A1 itself then
reads and deletes with the same ids.
"""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_my_story import STORY, TAG, delete, error, family, get, path, put, tag_of
from tests.test_api_support import Api, Signed
from tests.test_indexing_db import FakeModel

pytestmark = pytest.mark.db


def _reaches_nothing(other: Signed, first: str, second: str, *, same_customer: bool) -> None:
    for document in (first, second):
        if same_customer:  # A's document opens for A2, but holds none of A1's story
            assert get(other, document) is None
        else:
            assert error(other.client.get(path(document))) == (404, "not_found")
            assert error(put(other, document, "stolen")) == (404, "not_found")
        assert error(delete(other, document)) == (404, "not_found")


def test_a2_and_b1_reach_nothing_of_a1s_story_and_a1_still_does(db: Database) -> None:
    model = FakeModel()
    api = Api(db, model=model)
    a1 = api.new_signed()
    a2 = api.sign_in(a1.customer_id)
    b1 = api.new_signed()
    first, second = family(api, a1.customer_id)
    assert put(a1, second).json()["story"]["embedded"] is True
    before = get(a1, first)

    _reaches_nothing(a2, first, second, same_customer=True)
    _reaches_nothing(b1, first, second, same_customer=False)
    assert put(a2, second, "my own note").status_code == 201  # A2's own row, beside A1's

    assert get(a1, first) == get(a1, second) == before
    assert before["story"] == STORY
    assert tag_of(db, a1, first) == TAG
    assert get(a2, first)["story"] == "my own note"
    assert delete(a1, second).status_code == 204  # the control: A1 gets through
    assert get(a1, first) is None
    assert get(a2, first)["story"] == "my own note"
