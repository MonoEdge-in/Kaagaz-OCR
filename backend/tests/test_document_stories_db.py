"""Story of this PDF in the database (kaagaz.db.document_stories), run as the story's
own user (``Database.user``): create, replace, read and delete by any version, the
vector cleared by an edit, the guarded vector write, and the cascades.
"""

import pytest
from sqlalchemy import text

from kaagaz.db import document_stories as stories
from kaagaz.db.document_stories import StoryRow, check_story, story_hash
from kaagaz.db.engine import Database
from tests.db_support import count_rows, insert_document, new_customer
from tests.test_user_words_db import new_member

pytestmark = pytest.mark.db

VECTOR = [0.25] * stories.VECTOR_DIM
STORY = "Papa ne 2018 me jo Daregaon wali जमीन ka 7/12 diya tha, bank loan ke liye"
_STORIES_OF = "SELECT count(*) FROM document_stories WHERE customer_id = :c"
_VECTOR_OF = "SELECT embedding IS NOT NULL FROM document_stories WHERE family_id = :f"


def _family(db: Database, customer: str) -> tuple[str, str]:
    """A family of two versions: (first, second); the second is current."""
    with db.customer(customer) as conn:
        first = insert_document(conn, customer, is_current=False)
        second = insert_document(conn, customer, family_id=first, version_no=2)
    return first, second


def put(db: Database, customer: str, user: str, document: str, story: str = STORY) -> StoryRow | None:
    with db.user(customer, user) as conn:
        return stories.put_story(conn, customer, user, document, check_story(story))


def get(db: Database, customer: str, user: str, document: str) -> StoryRow | None:
    with db.user(customer, user) as conn:
        return stories.get_story(conn, customer, user, document)


def embed(db: Database, customer: str, user: str, family: str, story: str) -> bool:
    with db.user(customer, user) as conn:
        return stories.set_embedding(
            conn, customer, user, family, expected_hash=story_hash(story), vector=VECTOR, model_embed="m"
        )


def test_a_story_is_kept_on_the_family_and_found_by_any_version(db: Database) -> None:
    customer, user = new_customer(db)
    first, second = _family(db, customer)
    created = put(db, customer, user, second)
    assert created is not None
    assert (created.family_id, created.story, created.embedding_model) == (first, STORY, None)
    assert get(db, customer, user, first) == get(db, customer, user, second) == created

    replaced = put(db, customer, user, first, "  the land paper   dad gave ")
    assert replaced is not None
    assert (replaced.story, replaced.created_at) == ("the land paper dad gave", created.created_at)
    assert replaced.updated_at > created.updated_at  # the touch trigger
    with db.user(customer, user) as conn:
        assert count_rows(conn, "SELECT count(*) FROM document_stories") == 1
        latin = conn.execute(text("SELECT story_latin FROM document_stories")).scalar_one()
        assert stories.delete_story(conn, customer, user, second) is True
        assert stories.delete_story(conn, customer, user, second) is False
    assert latin == "the land paper dad gave"
    assert get(db, customer, user, first) is None


def test_no_document_no_story(db: Database) -> None:
    customer, user = new_customer(db)
    missing = "00000000-0000-4000-8000-000000000000"
    assert put(db, customer, user, missing) is None
    assert get(db, customer, user, missing) is None
    with db.user(customer, user) as conn:
        assert stories.delete_story(conn, customer, user, missing) is False


def test_an_edit_clears_the_vector_and_the_same_text_keeps_it(db: Database) -> None:
    customer, user = new_customer(db)
    first, _ = _family(db, customer)
    put(db, customer, user, first)
    assert embed(db, customer, user, first, STORY) is True
    kept = put(db, customer, user, first, f" {STORY} ")  # the same text once tidied
    assert kept is not None
    assert kept.embedding_model == "m#story1"
    edited = put(db, customer, user, first, "another story")
    assert edited is not None
    assert edited.embedding_model is None
    with db.user(customer, user) as conn:
        assert conn.execute(text(_VECTOR_OF), {"f": first}).scalar_one() is False


def test_a_vector_of_older_text_is_never_written(db: Database) -> None:
    """The embed read the story, then the user edited it: that vector is dropped."""
    customer, user = new_customer(db)
    first, _ = _family(db, customer)
    put(db, customer, user, first)
    put(db, customer, user, first, "the land paper dad gave")
    assert embed(db, customer, user, first, STORY) is False
    row = get(db, customer, user, first)
    assert row is not None
    assert row.embedding_model is None
    assert embed(db, customer, user, first, "the land paper dad gave") is True
    with db.user(customer, user) as conn:
        assert stories.delete_story(conn, customer, user, first)
    assert embed(db, customer, user, first, "the land paper dad gave") is False  # gone: nothing written
    with db.user(customer, user) as conn, pytest.raises(ValueError, match="1024"):
        stories.set_embedding(conn, customer, user, first, expected_hash="x", vector=[0.1], model_embed="m")


def test_the_story_goes_with_the_family_and_with_the_user(db: Database) -> None:
    customer, admin = new_customer(db)
    member = new_member(db, customer)
    first, second = _family(db, customer)
    other, _ = _family(db, customer)
    for user in (admin, member):
        put(db, customer, user, first)
        put(db, customer, user, other)
    delete = text("DELETE FROM documents WHERE customer_id = :c AND id = :d")
    with db.customer(customer) as conn:  # no user set: it can't see the stories, yet they go
        conn.execute(delete, {"c": customer, "d": second})
    assert get(db, customer, admin, first) is not None  # a later version alone: the story stays
    with db.customer(customer) as conn:
        conn.execute(delete, {"c": customer, "d": first})
    assert get(db, customer, admin, first) is None
    assert get(db, customer, member, first) is None
    with db.engine.begin() as conn:  # no SET ROLE: the owner (the app never deletes a user)
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": member})
    assert get(db, customer, admin, other) is not None
    with db.engine.begin() as conn:
        left = count_rows(conn, _STORIES_OF, {"c": customer})
    assert left == 1  # the admin's story of the other family
