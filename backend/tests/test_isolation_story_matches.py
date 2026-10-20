"""Story matches reach only the story's author (S3).

A1 tells a story about a document. A2 (a member of the same customer, who may open
the document) and B1 (customer B) ask with A1's own words through every route (the
search, the GET form, and the POST of "Search my stories" with mode "stories" and
limit 10, its widest) and get no match and none of the story; A1, the control,
gets a "match" from each. Below the
API, both locks hold on their own: the statement run in A2's, B1's or a
customer-only transaction with A1's ids finds nothing.
"""

from contextlib import AbstractContextManager
from typing import Any

import pytest
from sqlalchemy import Connection

from kaagaz.db.engine import Database
from kaagaz.search.story_search import read_stories
from tests.test_api_my_story import put
from tests.test_api_story_matches import MARK, QUESTION, STORY, matches
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db


def _every_route(signed: Signed) -> tuple[list[dict[str, Any]], str]:
    search = signed.client.get("/api/search", params={"q": QUESTION})
    body = {"q": QUESTION, "mode": "stories", "limit": 10}
    stories = signed.client.post("/api/me/story-matches", json=body, headers=CSRF)
    assert stories.status_code == 200, stories.text
    found = [*search.json()["story_matches"], *matches(signed, QUESTION), *stories.json()["story_matches"]]
    return found, search.text + stories.text


def test_only_the_author_gets_a_match_from_their_story(db: Database) -> None:
    api = Api(db)
    a1 = api.new_signed()
    a2 = api.sign_in(a1.customer_id)
    b1 = api.new_signed()
    document = api.add_document(a1.customer_id)
    put(a1, document, STORY)
    put(a2, document, "my own note about the shop bill")

    for other in (a2, b1):
        found, reply = _every_route(other)
        assert found == []
        assert MARK not in reply
    found, _ = _every_route(a1)  # the control
    # Changed on purpose (PRD 1, activity/search): GET /api/search lists no story matches (Q1).
    assert [(m["document_id"], m["band"]) for m in found] == [(document, "match")] * 2


def test_each_lock_holds_without_the_other(db: Database) -> None:
    api = Api(db)
    a1 = api.new_signed()
    a2 = api.sign_in(a1.customer_id)
    b1 = api.new_signed()
    put(a1, api.add_document(a1.customer_id), STORY)

    def found(transaction: AbstractContextManager[Connection]) -> int:
        """The statement with A1's ids, inside ``transaction``."""
        with transaction as conn:
            stories = read_stories(
                conn, customer_id=a1.customer_id, user_id=a1.user_id, recipe=None, embedding=None
            )
            return len(stories)

    assert found(db.user(a1.customer_id, a2.user_id)) == 0  # own_user_only: A2's transaction
    assert found(db.customer(a1.customer_id)) == 0  # no user set: no story at all
    assert found(db.user(b1.customer_id, b1.user_id)) == 0  # customer_only: B's transaction
    assert found(db.user(a1.customer_id, a1.user_id)) == 1  # the control
