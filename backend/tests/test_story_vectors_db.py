"""The story vector sweep (kaagaz/search/story_vectors.py and migration 0010) with a fake
model, against a real PostgreSQL: what the owner-rights function gives (ids only, the
limit, the tag, the customer), a pass that embeds as each story's own user, an edit or
a bad vector during a pass, and the worker's StoryVectors wrapper around a real pass.

Other tests leave stories in the same database, so every call here names the customer.
"""

import functools
import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db import document_stories as stories
from kaagaz.db.engine import Database
from kaagaz.search.story_vectors import embed_missing_stories, stories_to_embed
from kaagaz.worker_main import STORY_EVERY, StoryVectors
from tests.db_support import insert_document, new_customer
from tests.test_document_stories_db import VECTOR, put
from tests.test_indexing_db import FakeModel
from tests.test_user_words_db import new_member

pytestmark = pytest.mark.db

TAG = stories.recipe_tag(FakeModel.embed_model)
_TAGS = text("SELECT embedding_model FROM document_stories ORDER BY family_id")


def _three(db: Database) -> tuple[str, list[tuple[str, str, str]]]:
    """A customer with three stories, oldest first: none embedded."""
    customer, admin = new_customer(db)
    member = new_member(db, customer)
    ids: list[tuple[str, str, str]] = []
    for user in (admin, member, admin):
        with db.customer(customer) as conn:
            document = insert_document(conn, customer)
        put(db, customer, user, document, f"Daregaon paper {len(ids)}")
        ids.append((customer, user, document))
    return customer, ids


def _ids(db: Database, customer: str, limit: int, tag: str | None = None) -> list[tuple[str, str, str]]:
    with db.system() as conn:
        return list(stories_to_embed(conn, limit=limit, tag=tag, customer_id=customer))


def _tags(db: Database, customer: str, user: str) -> list[str | None]:
    with db.user(customer, user) as conn:
        return [row[0] for row in conn.execute(_TAGS)]


def test_the_function_gives_ids_oldest_first_and_keeps_its_limit(db: Database) -> None:
    customer, ids = _three(db)
    old_tag = ids[1]
    with db.user(customer, old_tag[1]) as conn:
        assert stories.set_embedding(
            conn,
            *old_tag,
            expected_hash=stories.story_hash("Daregaon paper 1"),
            vector=VECTOR,
            model_embed="old",
        )

    assert _ids(db, customer, 10) == [ids[0], ids[2]]  # no vector only (the worker)
    # or another tag (reembed); writing the old vector touched updated_at, so it is newest
    assert _ids(db, customer, 10, TAG) == [ids[0], ids[2], ids[1]]
    assert _ids(db, customer, 1) == [ids[0]]
    assert _ids(db, customer, 0) == _ids(db, customer, -5) == []
    with db.system() as conn:
        hundred = conn.execute(text("SELECT count(*) FROM kaagaz_stories_to_embed(NULL, NULL, 100000)"))
        assert hundred.scalar_one() <= 100


def test_a_pass_embeds_each_story_as_its_own_user(db: Database) -> None:
    customer, ids = _three(db)
    model = FakeModel()

    first = embed_missing_stories(db, model, limit=2, customer_id=customer)
    second = embed_missing_stories(db, model, limit=2, customer_id=customer)
    third = embed_missing_stories(db, model, limit=2, customer_id=customer)

    assert [(done.found, done.written) for done in (first, second, third)] == [(2, 2), (1, 1), (0, 0)]
    assert model.calls == [["Daregaon paper 0", "Daregaon paper 1"], ["Daregaon paper 2"]]  # one call a pass
    assert _tags(db, customer, ids[0][1]) == [TAG, TAG]
    assert _tags(db, customer, ids[1][1]) == [TAG]


class EditingModel(FakeModel):
    """Before it embeds, the author edits the first story."""

    def __init__(self, db: Database, story: tuple[str, str, str]) -> None:
        super().__init__()
        self.db, self.story = db, story

    def embed(self, texts: list[str]) -> list[list[float]]:
        put(self.db, *self.story, "edited meanwhile")
        return super().embed(texts)


class NanModel(FakeModel):
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[math.nan] * stories.VECTOR_DIM for _ in texts]


def test_an_edit_or_a_bad_vector_during_a_pass_writes_nothing_stale(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    customer, ids = _three(db)

    edited = embed_missing_stories(db, EditingModel(db, ids[0]), limit=1, customer_id=customer)
    with caplog.at_level("WARNING", logger="kaagaz"):
        refused = embed_missing_stories(db, NanModel(), limit=1, customer_id=customer)

    assert (edited.found, edited.written) == (1, 0)
    assert (refused.found, refused.written) == (1, 0)
    assert _tags(db, customer, ids[0][1]) == [None, None]  # no vector of the older text
    assert _tags(db, customer, ids[1][1]) == [None]  # nor a NaN
    assert [record.__dict__.get("code") for record in caplog.records] == ["embedding_values"]
    assert "Daregaon" not in caplog.text


class _Jobs:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> bool:
        self.calls += 1
        return True


def test_the_worker_wrapper_embeds_the_missing_stories_every_ten_minutes(db: Database) -> None:
    customer, ids = _three(db)
    now = [datetime(2026, 10, 3, 12, 0, tzinfo=UTC)]
    model = FakeModel()
    sweep = functools.partial(embed_missing_stories, db, model, limit=2, customer_id=customer)
    worker = StoryVectors(_Jobs(), sweep, lambda: now[0])

    worker.run_once()
    now[0] += STORY_EVERY - timedelta(seconds=1)
    worker.run_once()  # too soon: no pass
    assert len(model.calls) == 1
    now[0] += timedelta(seconds=1)
    worker.run_once()

    assert len(model.calls) == 2
    assert _tags(db, customer, ids[0][1]) == [TAG, TAG]
    assert _tags(db, customer, ids[1][1]) == [TAG]
