"""A story through the whole API and the sweep: nobody but its author reaches it.

A1 has an embedded story on a family of two versions. A2 (a member of the same
customer) may open the document, but its GET finds no story, its DELETE is
404, and its PUT writes A2's own row, leaving A1's story and vector as they
were. B1 (customer B) gets 404 for every route. The control: A1 itself then
reads and deletes with the same ids. The owner-rights sweep function gives
ids only, and a pass writes each vector, of the right text, as the story's own user.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.search.story_vectors import embed_missing_stories
from tests.test_api_my_story import STORY, TAG, delete, error, family, get, path, put, tag_of
from tests.test_api_support import Api, Signed
from tests.test_indexing_db import FakeModel
from tests.test_search_db import axis

pytestmark = pytest.mark.db

_VECTOR = text("SELECT embedding::text FROM document_stories WHERE customer_id = :c AND family_id = :f")


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


class _ByText(FakeModel):
    """The vector of a text is axis(its place in ``texts``), whatever the order asked."""

    def __init__(self, texts: list[str]) -> None:
        super().__init__()
        self.texts = texts

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [axis(self.texts.index(words)) for words in texts]


def _vector(db: Database, signed: Signed, document: str) -> list[float] | None:
    with db.user(signed.customer_id, signed.user_id) as conn:
        value = conn.execute(_VECTOR, {"c": signed.customer_id, "f": document}).scalar_one()
    return None if value is None else [float(number) for number in str(value).strip("[]").split(",")]


def test_the_sweep_gives_ids_only_and_writes_each_vector_on_its_authors_row(db: Database) -> None:
    api = Api(db)  # no model: every story is saved without a vector
    a1 = api.new_signed()
    a2 = api.sign_in(a1.customer_id)
    b1 = api.new_signed()
    document = api.add_document(a1.customer_id)
    b_document = api.add_document(b1.customer_id)
    texts = [STORY, "the land paper dad gave", "B's own story"]
    put(a1, document)
    put(a2, document, texts[1])
    put(b1, b_document, texts[2])
    sweep = text("SELECT * FROM kaagaz_stories_to_embed(NULL, :c, 10, NULL, NULL, NULL)")

    with db.system() as conn:  # no customer, no user: the table itself shows nothing
        assert conn.execute(text("SELECT count(*) FROM document_stories")).scalar_one() == 0
        result = conn.execute(sweep, {"c": a1.customer_id})
        rows = {tuple(str(value) for value in row) for row in result}
    assert list(result.keys()) == ["customer_id", "user_id", "family_id"]
    assert rows == {(a1.customer_id, a1.user_id, document), (a1.customer_id, a2.user_id, document)}

    model = _ByText(texts)
    done = embed_missing_stories(db, model, limit=32, customer_id=a1.customer_id)

    assert (done.found, done.written) == (2, 2)
    assert sorted(model.calls[0]) == sorted(texts[:2])  # never B's story
    assert _vector(db, a1, document) == axis(0)  # each row has its own text's vector
    assert _vector(db, a2, document) == axis(1)
    assert _vector(db, b1, b_document) is None  # another customer's pass: untouched
    assert get(a1, document)["story"] == STORY
    assert get(a2, document)["story"] == texts[1]
    done = embed_missing_stories(db, model, limit=32, customer_id=b1.customer_id)  # the control
    assert (done.written, _vector(db, b1, b_document)) == (1, axis(2))
