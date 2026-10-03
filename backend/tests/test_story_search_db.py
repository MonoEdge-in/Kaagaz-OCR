"""Story matches against a real database (kaagaz/search/story_search.py), as the story's
own user: the current version of the family, never Trash, a purged family, a version
still being read or a document outside the scope; meaning only when calibrated; at
most three; and the Python trigrams agree with pg_trgm's.
"""

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.search import story_match
from kaagaz.search.scope import Scope
from kaagaz.search.story_match import StoryMatch
from kaagaz.search.story_search import find_story_matches
from tests.db_support import insert_document, insert_folder, new_customer
from tests.test_document_stories_db import STORY, VECTOR, embed, put

pytestmark = pytest.mark.db

ASK = {"question": "daregaon jamin papa loan", "embedding": None, "model_embed": "m", "scope": Scope()}
HOUSE = {"question": "sale deed of the house", "embedding": VECTOR}


def find(db: Database, customer: str, user: str, **asked: Any) -> list[StoryMatch]:
    return find_story_matches(db, customer_id=customer, user_id=user, **(ASK | asked))


def _family(db: Database, customer: str, **current: object) -> tuple[str, str]:
    """(first, second) versions; the second is current, named "7-12 extract.pdf"."""
    with db.customer(customer) as conn:
        first = insert_document(conn, customer, is_current=False)
        named = {"display_name": "7-12 extract.pdf", **current}
        return first, insert_document(conn, customer, family_id=first, version_no=2, **named)


def _set(db: Database, customer: str, change: str, family: str) -> None:
    with db.customer(customer) as conn:
        conn.execute(
            text(f"{change} WHERE customer_id = :c AND family_id = :f"), {"c": customer, "f": family}
        )


def test_a_story_points_at_its_familys_current_version_with_a_band_only(db: Database) -> None:
    customer, user = new_customer(db)
    first, second = _family(db, customer)
    put(db, customer, user, first)

    assert find(db, customer, user) == [StoryMatch(second, "7-12 extract.pdf", None, "match")]
    assert find(db, customer, user, question="the land paper dad gave for the bank loan")[0].band == "maybe"
    assert find(db, customer, user, question="pan card ki copy") == []


def test_trash_a_purge_and_a_version_still_being_read_are_never_suggested(db: Database) -> None:
    customer, user = new_customer(db)
    trashed, trashed_now = _family(db, customer)
    purged, purged_now = _family(db, customer)
    reading, reading_now = _family(db, customer, status="clean")
    for family in (trashed, purged, reading):
        put(db, customer, user, family)
    assert {m.document_id for m in find(db, customer, user)} == {trashed_now, purged_now}  # the control

    _set(db, customer, "UPDATE documents SET deleted_at = now()", trashed)
    _set(db, customer, "DELETE FROM documents", purged)
    assert find(db, customer, user) == []
    _set(db, customer, "UPDATE documents SET status = 'ready'", reading)

    assert find(db, customer, user) == [StoryMatch(reading_now, "7-12 extract.pdf", None, "match")]


def test_the_scope_is_respected(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder, other = (insert_folder(conn, customer, user, name) for name in ("Land", "Bills"))
    first, second = _family(db, customer, folder_id=folder)
    put(db, customer, user, first)

    for found, scope in (
        (1, Scope(folder_ids=(folder,))),
        (1, Scope(document_ids=(second,))),
        (0, Scope(folder_ids=(other,))),
        (0, Scope(document_ids=(first,))),  # an older version
        (0, Scope(doc_types=("invoice",))),
        (0, Scope(date_to=date(2000, 1, 1))),
        (0, Scope(tag_ids=())),  # matches nothing
    ):
        assert len(find(db, customer, user, scope=scope)) == found, scope


def test_meaning_counts_only_with_a_calibrated_threshold(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, user = new_customer(db)
    first, second = _family(db, customer)
    put(db, customer, user, first)
    assert embed(db, customer, user, first, STORY)  # tagged "m#story1"

    assert find(db, customer, user, **HOUSE) == []  # OFF
    monkeypatch.setitem(story_match.MEANING_THRESHOLDS, "m#story1", 0.8)
    assert find(db, customer, user, **HOUSE) == [StoryMatch(second, "7-12 extract.pdf", None, "match")]
    assert find(db, customer, user, **(HOUSE | {"embedding": [-x for x in VECTOR]})) == []  # cosine -1
    assert find(db, customer, user, **(HOUSE | {"model_embed": "other"})) == []  # another recipe: OFF


def test_at_most_three_of_many(db: Database) -> None:
    customer, user = new_customer(db)
    for _ in range(5):
        put(db, customer, user, _family(db, customer)[0])
    found = find(db, customer, user)
    assert len(found) == 3 and {m.band for m in found} == {"maybe"}  # five equal stories: a tie


def test_the_trigrams_are_pg_trgms(db: Database) -> None:
    for first, second in (("daregav", "daregaon"), ("kotharud", "kothrud"), ("loan", "lone"), ("ab", "abc")):
        with db.system() as conn:
            expected = conn.execute(text("SELECT similarity(:a, :b)"), {"a": first, "b": second}).scalar_one()
        assert story_match.similarity(first, second) == pytest.approx(float(expected), abs=1e-6)
