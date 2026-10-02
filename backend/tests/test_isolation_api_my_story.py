"""A story through the whole API and the sweep: nobody but its author reaches it.

A1 has an embedded story on a family of two versions. A2 (a member of the same
customer) may open the document, but its GET finds no story, its DELETE is
404, and its PUT writes A2's own row, leaving A1's story and vector as they
were. B1 (customer B) gets 404 for every route. The control: A1 itself then
reads and deletes with the same ids. The owner-rights sweep function gives
ids only, and a pass writes each vector as the story's own user.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.search.story_vectors import embed_missing_stories
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


def test_the_sweep_gives_ids_only_and_writes_as_each_author(db: Database) -> None:
    api = Api(db)  # no model: both stories are saved without a vector
    a1 = api.new_signed()
    a2 = api.sign_in(a1.customer_id)
    document = api.add_document(a1.customer_id)
    put(a1, document)
    put(a2, document, "the land paper dad gave")
    sweep = text("SELECT * FROM kaagaz_stories_to_embed(NULL, :c, 10)")

    with db.system() as conn:  # no customer, no user: the table itself shows nothing
        assert conn.execute(text("SELECT count(*) FROM document_stories")).scalar_one() == 0
        result = conn.execute(sweep, {"c": a1.customer_id})
        rows = {tuple(str(value) for value in row) for row in result}
    assert list(result.keys()) == ["customer_id", "user_id", "family_id"]
    assert rows == {(a1.customer_id, a1.user_id, document), (a1.customer_id, a2.user_id, document)}

    done = embed_missing_stories(db, FakeModel(), limit=32, customer_id=a1.customer_id)

    assert (done.found, done.written) == (2, 2)
    assert tag_of(db, a1, document) == tag_of(db, a2, document) == TAG
    assert get(a1, document)["story"] == STORY  # each kept its own text
    assert get(a2, document)["story"] == "the land paper dad gave"
