"""The story vector sweep (kaagaz/search/story_vectors.py and migration 0010) with a fake
model, against a real PostgreSQL: what the owner-rights function gives (ids only, the
limit and its cap, the cursor, the tag, the customer), a pass that embeds as each
story's own user, an edit or a bad vector during a pass, and the worker's StoryVectors
wrapper around real passes, a model that goes down included.

Other tests leave stories in the same database, so every call here names the customer.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.ai.client import ModelUnavailable
from kaagaz.db import document_stories as stories
from kaagaz.db.engine import Database
from kaagaz.search.story_vectors import StoryKey, StoryPass, embed_missing_stories, stories_to_embed
from kaagaz.worker_main import STORY_EVERY, JsonFormatter, StoryVectors
from tests.db_support import insert_document, new_customer
from tests.test_document_stories_db import put
from tests.test_indexing_db import FakeModel
from tests.test_user_words_db import new_member

pytestmark = pytest.mark.db

TAG = stories.recipe_tag(FakeModel.embed_model)
_TAGS = text("SELECT embedding_model FROM document_stories ORDER BY family_id")
_STORY = text(
    "INSERT INTO document_stories (customer_id, user_id, family_id, story, story_latin)"
    " VALUES (:c, :u, :f, 'x', 'x')"
)


def _three(db: Database) -> tuple[str, dict[StoryKey, str]]:
    """A customer with three stories of two users, none embedded: {key: text}, in key order."""
    customer, admin = new_customer(db)
    member = new_member(db, customer)
    texts: dict[StoryKey, str] = {}
    for user in (admin, member, admin):
        with db.customer(customer) as conn:
            document = insert_document(conn, customer)
        words = f"Daregaon paper {len(texts)}"
        put(db, customer, user, document, words)
        texts[(customer, user, document)] = words
    return customer, dict(sorted(texts.items()))


def _ids(
    db: Database, customer: str, limit: int, tag: str | None = None, after: StoryKey | None = None
) -> list[StoryKey]:
    with db.system() as conn:
        return stories_to_embed(conn, limit=limit, tag=tag, customer_id=customer, after=after)


def _tags(db: Database, customer: str, user: str) -> list[str | None]:
    with db.user(customer, user) as conn:
        return [row[0] for row in conn.execute(_TAGS)]


def _every_tag(db: Database, texts: dict[StoryKey, str]) -> list[str | None]:
    users = sorted({(key[0], key[1]) for key in texts})
    return [tag for customer, user in users for tag in _tags(db, customer, user)]


def test_the_function_gives_ids_only_in_key_order_after_the_cursor(db: Database) -> None:
    customer, texts = _three(db)
    keys = list(texts)
    old = keys[1]
    with db.user(customer, old[1]) as conn:
        written = stories.set_embedding(
            conn, *old, expected_hash=stories.story_hash(texts[old]), vector=[0.25] * 1024, model_embed="old"
        )
    assert written
    missing = [key for key in keys if key != old]

    assert _ids(db, customer, 10) == missing  # no vector only (the worker)
    assert _ids(db, customer, 10, TAG) == keys  # or another tag (reembed)
    assert _ids(db, customer, 1) == missing[:1]
    assert _ids(db, customer, 10, TAG, after=keys[0]) == keys[1:]
    assert _ids(db, customer, 10, after=missing[-1]) == []
    assert _ids(db, customer, 0) == _ids(db, customer, -5) == []
    with db.system() as conn:
        result = conn.execute(
            text("SELECT * FROM kaagaz_stories_to_embed(NULL, :c, 10, NULL, NULL, NULL)"), {"c": customer}
        )
        assert list(result.keys()) == ["customer_id", "user_id", "family_id"]  # never the text
        assert len(result.all()) == 2


def test_the_function_gives_at_most_100(db: Database) -> None:
    customer, user = new_customer(db)
    with db.user(customer, user) as conn:
        for _ in range(101):
            conn.execute(_STORY, {"c": customer, "u": user, "f": insert_document(conn, customer)})

    first = _ids(db, customer, 1000)
    assert len(first) == 100
    assert len(_ids(db, customer, 1000, after=first[-1])) == 1


def test_a_pass_embeds_each_story_as_its_own_user_and_hands_on_its_cursor(db: Database) -> None:
    customer, texts = _three(db)
    keys = list(texts)
    model = FakeModel()

    first = embed_missing_stories(db, model, limit=2, customer_id=customer)
    second = embed_missing_stories(db, model, limit=2, customer_id=customer, after=first.after)
    third = embed_missing_stories(db, model, limit=2, customer_id=customer, after=second.after)

    assert (first, second, third) == (StoryPass(2, 2, keys[1]), StoryPass(1, 1, None), StoryPass(0, 0, None))
    assert model.calls == [[texts[keys[0]], texts[keys[1]]], [texts[keys[2]]]]  # one call a pass
    assert _every_tag(db, texts) == [TAG, TAG, TAG]


class EditingModel(FakeModel):
    """Before it embeds, the author edits the story ``key``."""

    def __init__(self, db: Database, key: StoryKey) -> None:
        super().__init__()
        self.db, self.key = db, key

    def embed(self, texts: list[str]) -> list[list[float]]:
        put(self.db, *self.key, "edited meanwhile")
        return super().embed(texts)


class NanModel(FakeModel):
    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[math.nan] * stories.VECTOR_DIM for _ in texts]


def test_an_edit_during_a_pass_leaves_no_stale_vector(db: Database) -> None:
    customer, texts = _three(db)
    first = next(iter(texts))

    edited = embed_missing_stories(db, EditingModel(db, first), limit=1, customer_id=customer)

    assert (edited.found, edited.written) == (1, 0)
    assert _every_tag(db, texts) == [None, None, None]  # no vector of the older text
    again = FakeModel()
    assert embed_missing_stories(db, again, limit=1, customer_id=customer).written == 1
    assert again.calls == [["edited meanwhile"]]  # the next pass embeds the new text


def test_a_refused_vector_is_logged_by_code_and_passed_over(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    customer, texts = _three(db)
    keys = list(texts)

    with caplog.at_level("DEBUG", logger="kaagaz"):
        refused = embed_missing_stories(db, NanModel(), limit=1, customer_id=customer)
    after = embed_missing_stories(db, FakeModel(), limit=1, customer_id=customer, after=refused.after)

    assert refused == StoryPass(1, 0, keys[0])
    assert after == StoryPass(1, 1, keys[1])  # the refused story does not block the next
    assert _ids(db, customer, 10) == [keys[0], keys[2]]
    assert [record.__dict__.get("code") for record in caplog.records] == ["embedding_values"]
    assert "Daregaon" not in caplog.text


class _Jobs:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> bool:
        self.calls += 1
        return True


class _Flaky(FakeModel):
    """Down while ``down`` is true."""

    down = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.down:
            self.calls.append(list(texts))
            raise ModelUnavailable("unreachable")
        return super().embed(texts)


def test_the_worker_wrapper_embeds_the_missing_stories_and_waits_out_a_model_that_is_down(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    customer, texts = _three(db)
    now = [datetime(2026, 10, 3, 12, 0, tzinfo=UTC)]
    model = _Flaky()
    jobs = _Jobs()

    def sweep(after: StoryKey | None) -> StoryPass:
        return embed_missing_stories(db, model, limit=2, customer_id=customer, after=after)

    worker = StoryVectors(jobs, sweep, lambda: now[0])

    with caplog.at_level("DEBUG", logger="kaagaz"):
        model.down = True
        worker.run_once()  # down: the next pass in STORY_EVERY, then twice that
        now[0] += STORY_EVERY
        worker.run_once()  # down again
        model.down = False
        now[0] += 2 * STORY_EVERY - timedelta(seconds=1)
        worker.run_once()  # too soon: no pass
        assert len(model.calls) == 2
        now[0] += timedelta(seconds=1)
        worker.run_once()  # up: two stories
        now[0] += STORY_EVERY
        worker.run_once()  # the third, after the cursor

    assert jobs.calls == 5  # the jobs ran on every turn
    assert len(model.calls) == 4
    assert _every_tag(db, texts) == [TAG, TAG, TAG]
    codes = [record.__dict__.get("code") for record in caplog.records if record.levelname == "WARNING"]
    assert codes == ["unreachable", "unreachable"]
    for record in caplog.records:
        assert "Daregaon" not in JsonFormatter().format(record)
