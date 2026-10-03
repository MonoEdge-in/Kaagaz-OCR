"""The story routes (kaagaz/api/routers/stories.py) against a real database.

The SQL rules are tested on the helpers (test_document_stories_db.py); these
tests check every flow through the API: the replies and their codes, the text
and session checks, Trash, the audit rows (ids only), the inline embed with a
fake model (the story recipe, a model that is down or gives a bad vector, an
edit while the model works), and that the story never reaches a log line.
"""

import json
import logging
import math
import threading
import time
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api.routers import stories as story_routes
from kaagaz.db import document_stories as stories
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed
from tests.test_indexing_db import FakeModel

pytestmark = pytest.mark.db

UNKNOWN = "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"
STORY = "Papa ne 2018 me jo Daregaon wali जमीन ka 7/12 diya tha, bank loan ke liye"
TAG = stories.recipe_tag(FakeModel.embed_model)
_TAG_OF = text("SELECT embedding_model FROM document_stories WHERE customer_id = :c AND family_id = :f")


class EditingModel(FakeModel):
    """While it embeds, ``signed`` saves ``edit`` on ``document`` in another tab."""

    def __init__(self, db: Database, edit: str) -> None:
        super().__init__()
        self.db, self.edit = db, edit
        self.target: tuple[Signed, str] | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        assert self.target is not None
        signed, document = self.target
        with self.db.user(signed.customer_id, signed.user_id) as conn:
            checked = stories.check_story(self.edit)
            stories.put_story(conn, signed.customer_id, signed.user_id, document, checked)
        return super().embed(texts)


class SlowModel(FakeModel):
    """Embeds only once ``go`` is set, or after ``most`` seconds: a model that is
    loading, or busy with another request."""

    def __init__(self, most: float) -> None:
        super().__init__()
        self.go = threading.Event()
        self.most = most

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.go.wait(self.most)
        return super().embed(texts)


class BadVectorModel(FakeModel):
    """Replies with vectors of ``value``, which halfvec can't hold."""

    def __init__(self, value: float) -> None:
        super().__init__()
        self.value = value

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[self.value] * stories.VECTOR_DIM for _ in texts]


def path(document: str) -> str:
    return f"/api/documents/{document}/my-story"


def put(signed: Signed, document: str, story: str = STORY, **body: Any) -> httpx.Response:
    reply: httpx.Response = signed.client.put(path(document), json={"story": story, **body}, headers=CSRF)
    return reply


def get(signed: Signed, document: str) -> Any:
    reply = signed.client.get(path(document))
    assert reply.status_code == 200, reply.text
    return reply.json()["story"]


def delete(signed: Signed, document: str, **query: str) -> httpx.Response:
    reply: httpx.Response = signed.client.delete(path(document), params=query, headers=CSRF)
    return reply


def error(reply: httpx.Response) -> tuple[int, str]:
    return reply.status_code, reply.json()["error"]["code"]


def tag_of(db: Database, signed: Signed, family: str) -> str | None:
    with db.user(signed.customer_id, signed.user_id) as conn:
        value = conn.execute(_TAG_OF, {"c": signed.customer_id, "f": family}).scalar_one()
    return None if value is None else str(value)


def family(api: Api, customer: str) -> tuple[str, str]:
    """A family of two versions: (first, second); the second is current."""
    first = api.add_document(customer, is_current=False)
    return first, api.add_document(customer, family_id=first, version_no=2)


def test_a_story_is_saved_read_replaced_and_deleted_with_ids_only_in_the_audit(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first, second = family(api, signed.customer_id)
    assert get(signed, second) is None

    created = put(signed, second, f"  {STORY}  ")
    replaced = put(signed, first, "the land paper dad gave")

    assert created.status_code == 201
    body = created.json()["story"]
    assert set(body) == {"family_id", "story", "embedded", "created_at", "updated_at"}
    assert (body["family_id"], body["story"], body["embedded"]) == (first, STORY, False)
    assert replaced.status_code == 200
    assert get(signed, second) == get(signed, first) == replaced.json()["story"]
    assert get(signed, first)["story"] == "the land paper dad gave"
    assert delete(signed, first, user_id=signed.user_id).status_code == 204
    assert error(delete(signed, first)) == (404, "not_found")  # no story any more
    assert get(signed, second) is None
    rows = [row for row in api.audit_rows(signed.customer_id) if row[0].startswith("document_story.")]
    assert rows == [
        ("document_story.saved", "document_story", first, {"document_id": second}),
        ("document_story.saved", "document_story", first, {"document_id": first}),
        ("document_story.deleted", "document_story", first, {"document_id": first}),
    ]
    assert "Daregaon" not in json.dumps(api.audit_rows(signed.customer_id))


@pytest.mark.parametrize(
    ("story", "message"),
    [
        (" ​\t ", "Write the story first."),
        ("x" * 1001, "A story can be at most 1000 characters."),
        ("!!! ...", "The story needs at least one letter or digit."),
        ("x" * 4001, None),  # refused before it is tidied: the generic text
    ],
    ids=["empty", "over_1000", "no_letter", "over_4000"],
)
def test_bad_text_is_400_saying_why_and_saves_nothing(db: Database, story: str, message: str | None) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)

    reply = put(signed, document, story)

    assert error(reply) == (400, "bad_request")
    assert message is None or reply.json()["error"]["message"] == message
    assert story not in reply.text
    assert get(signed, document) is None


def test_unknown_documents_other_sessions_and_trash_are_refused(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
    assert put(signed, document).status_code == 201
    other = "5d3c9a47-0c1e-4f7e-9b60-2f3f0f0f0f0f"

    assert error(put(signed, UNKNOWN)) == (404, "not_found")
    assert error(delete(signed, UNKNOWN)) == (404, "not_found")
    assert error(signed.client.get(path(UNKNOWN))) == (404, "not_found")
    assert error(signed.client.get(path("not-an-id"))) == (400, "bad_request")
    assert error(put(signed, document, user_id=other)) == (409, "session_changed")
    assert put(signed, document, user_id=other).json()["error"]["message"].startswith("Not saved.")
    assert error(delete(signed, document, user_id=other)) == (409, "session_changed")
    assert error(put(signed, document, mood="sad")) == (400, "bad_request")  # no other field
    trash = text("UPDATE documents SET deleted_at = now() WHERE customer_id = :c AND id = :d")
    with db.customer(signed.customer_id) as conn:
        conn.execute(trash, {"c": signed.customer_id, "d": document})
    assert error(signed.client.get(path(document))) == (404, "not_found")
    assert error(put(signed, document, "another story")) == (404, "not_found")
    assert error(delete(signed, document)) == (404, "not_found")
    with db.customer(signed.customer_id) as conn:  # restored: the story is back, as it was
        conn.execute(text("UPDATE documents SET deleted_at = NULL WHERE id = :d"), {"d": document})
    assert get(signed, document)["story"] == STORY


def test_the_saved_story_is_embedded_with_the_story_recipe(db: Database) -> None:
    model = FakeModel()
    api = Api(db, model=model)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)

    created = put(signed, document, f" {STORY} ")
    again = put(signed, document)  # the same text: its vector is current, no model call

    assert (created.status_code, created.json()["story"]["embedded"]) == (201, True)
    assert (again.status_code, again.json()["story"]["embedded"]) == (200, True)
    assert model.calls == [[STORY]]  # the text as saved, with no instruction
    assert tag_of(db, signed, document) == TAG == "fake-embed#story1"
    assert get(signed, document)["embedded"] is True
    assert put(signed, document, "the land paper").json()["story"]["embedded"] is True
    assert model.calls[-1] == ["the land paper"]


@pytest.mark.parametrize(
    ("model", "code"),
    [
        (FakeModel(fail=True), "ModelUnavailable"),
        (FakeModel(drop_one=True), "ModelOutputError"),
        (BadVectorModel(math.nan), "embedding_values"),
        (BadVectorModel(70000.0), "embedding_values"),
    ],
    ids=["down", "no_vector", "nan", "beyond_halfvec"],
)
def test_a_model_that_fails_never_fails_the_save(
    db: Database, caplog: pytest.LogCaptureFixture, model: FakeModel, code: str
) -> None:
    api = Api(db, model=model)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)

    with caplog.at_level(logging.WARNING, logger="kaagaz"):
        reply = put(signed, document)

    assert (reply.status_code, reply.json()["story"]["embedded"]) == (201, False)
    assert tag_of(db, signed, document) is None
    assert get(signed, document) == {**reply.json()["story"], "embedded": False}
    assert code in [record.__dict__.get("code") for record in caplog.records]


def test_a_slow_model_never_holds_the_save(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """3 Oct 2026: the PUT waited for the model client's own timeout (60 s), the
    site's proxy gave up after 30 s, and a saved story showed "Not saved". The
    reply now waits EMBED_WAIT_SECONDS at most; the vector lands later."""
    monkeypatch.setattr(story_routes, "EMBED_WAIT_SECONDS", 0.2)
    model = SlowModel(most=8.0)
    api = Api(db, model=model)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)

    started = time.monotonic()
    reply = put(signed, document)
    waited = time.monotonic() - started
    model.go.set()

    assert waited < 4.0
    assert (reply.status_code, reply.json()["story"]["embedded"]) == (201, False)
    assert get(signed, document)["story"] == STORY  # saved, whatever the model does
    deadline = time.monotonic() + 10.0
    while tag_of(db, signed, document) is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert tag_of(db, signed, document) == TAG  # the embed went on and wrote it
    assert get(signed, document)["embedded"] is True


def test_an_edit_while_the_model_works_leaves_no_stale_vector(db: Database) -> None:
    model = EditingModel(db, "the land paper dad gave")
    api = Api(db, model=model)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
    model.target = (signed, document)

    reply = put(signed, document)

    assert (reply.status_code, reply.json()["story"]["embedded"]) == (201, False)
    assert model.calls == [[STORY]]
    assert tag_of(db, signed, document) is None  # the vector of the older text was dropped
    with db.user(signed.customer_id, signed.user_id) as conn:
        row = stories.get_story(conn, signed.customer_id, signed.user_id, document)
    assert row is not None
    assert row.story == "the land paper dad gave"


def test_the_story_never_reaches_a_log_line(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    """Every route, a refusal, a model that works and one that is down, at DEBUG."""
    caplog.set_level(logging.DEBUG, logger="kaagaz")
    for model in (FakeModel(), FakeModel(fail=True)):
        api = Api(db, model=model)
        signed = api.new_signed()
        document = api.add_document(signed.customer_id)
        assert put(signed, document).status_code == 201
        assert get(signed, document)["story"] == STORY
        assert put(signed, document, STORY + " " + "x" * 1000).status_code == 400
        assert delete(signed, document).status_code == 204
    assert caplog.records  # the access log, at least
    for record in caplog.records:
        line = record.getMessage() + json.dumps(record.__dict__, default=str)
        assert "Daregaon" not in line
        assert "जमीन" not in line
