"""The translated second search (council 4 Oct 2026) on a real PostgreSQL, through the
real AnswerService and PgRetriever: customer separation, the scope, and library search.

Customers A and B each have one Marathi piece naming a different owner, both embedded on
one axis, so each customer's own piece is its first hit. The fake model embeds every text
on that axis, translates every question to a word only A's piece holds, and drafts the
owner named in each evidence block it is given. Each check that B gets nothing of A's is
paired with A getting its own owner (the positive control)."""

import json
from typing import Any

import pytest

from kaagaz.answering import translate
from kaagaz.answering.service import AnswerService
from kaagaz.api.app import create_app
from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING, Scope
from tests.db_support import new_customer
from tests.settings_factory import make_settings
from tests.test_answering_neighbours_db import blocks
from tests.test_api_support import ApiFakeQueue, TestClient
from tests.test_end_to_end_db import NOW, PipelineStorage, _sign_in
from tests.test_search_db import add_document, add_pieces, axis

pytestmark = pytest.mark.db

QUESTION = "Who is the cultivator?"
A_WORD = "\u0915\u0938\u0923\u093e\u0930"  # only in A's piece
A_NAME = "\u0938\u0940\u0924\u093e \u0926\u0947\u0936\u092e\u0941\u0916"
B_NAME = "\u0917\u0923\u092a\u0924 \u091c\u093e\u0927\u0935"
SETTINGS = make_settings(
    answer_translate_question=True,
    model_embed="test-embed",
    answer_whole_doc_tokens=0,  # PRD 3's search: no whole documents or aspects (PRD 5 CX5, CX6)
    query_aspects_max=0,
)


class Model:
    chat_model, embed_model = "fake-chat", "fake-embed"

    def __init__(self) -> None:
        self.translated = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [axis(0) for _ in texts]

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema: Any,
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        if system == translate.SYSTEM_PROMPT:
            self.translated += 1
            return {"queries": [{"script": "Devanagari", "text": A_WORD}]}
        found = [
            (n, name) for n, (_, body) in blocks(user).items() for name in (A_NAME, B_NAME) if name in body
        ]
        sentences = [{"text": f"{name}.", "evidence": [n]} for n, name in found]
        return {"status": "answered" if found else "not_found", "sentences": sentences}


def _world(db: Database) -> tuple[str, str, dict[str, list[str]]]:
    """A and B, each with one Marathi owner piece; A also has an unrelated Marathi note on
    another axis. Returns A, B and their piece ids (A's note last; "note": its document)."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    pieces = {}
    for customer, owner in ((a, f"{A_WORD}: {A_NAME}"), (b, f"\u092e\u093e\u0932\u0915: {B_NAME}")):
        pieces[customer] = add_pieces(db, customer, add_document(db, customer), [(owner, axis(0))])
    note = add_document(db, a, name="note.pdf")
    pieces[a] += add_pieces(db, a, note, [("\u092a\u093e\u0935\u0938 \u091d\u093e\u0932\u093e", axis(5))])
    pieces["note"] = [note]
    return a, b, pieces


def _ask(db: Database, customer: str, scope: Scope = EVERYTHING) -> tuple[dict[str, Any], Model]:
    model = Model()
    service = AnswerService(db=db, model=model, settings=SETTINGS, clock=lambda: NOW, translator=model)
    return service.answer(customer, QUESTION, scope=scope).to_json(), model


def test_b_never_gets_as_piece_through_as_word_and_a_does(db: Database) -> None:
    a, b, pieces = _world(db)

    for_a, model = _ask(db, a)
    assert model.translated == 1 and for_a["status"] == "answered"
    assert {c["ref_id"] for c in for_a["citations"]} == {pieces[a][0]}
    assert [s["text"] for s in for_a["sentences"]] == [f"{A_NAME}."]

    for_b, model = _ask(db, b)
    assert model.translated == 1
    dumped = json.dumps(for_b, ensure_ascii=False)
    assert A_NAME not in dumped and A_WORD not in dumped and pieces[a][0] not in dumped
    assert for_b["status"] == "not_found" or {c["ref_id"] for c in for_b["citations"]} == set(pieces[b])


def test_a_scope_without_the_owners_document_is_not_found(db: Database) -> None:
    a, _, pieces = _world(db)
    answer, model = _ask(db, a, Scope(document_ids=(pieces["note"][0],)))
    assert model.translated == 1  # the note in scope is Marathi: the second search ran, in scope
    assert answer["status"] == "not_found" and A_NAME not in json.dumps(answer, ensure_ascii=False)


def test_library_search_is_never_translated(db: Database) -> None:
    a, _, _ = _world(db)
    model = Model()
    answers = AnswerService(db=db, model=model, settings=SETTINGS, clock=lambda: NOW, translator=model)
    app = create_app(
        SETTINGS,
        db=db,
        storage=PipelineStorage(),
        queue=ApiFakeQueue(),
        answers=answers,
        clock=lambda: NOW,
        model=model,
    )
    client = TestClient(app)
    _sign_in(db, client, a)
    reply = client.get("/api/search", params={"q": QUESTION})
    assert reply.status_code == 200, reply.text
    assert model.translated == 0
    assert reply.json()["documents"][0]["best"]["snippet"].endswith(A_NAME)


def test_whole_document_mode_answers_the_english_question_from_marathi_text_alone(db: Database) -> None:
    """PRD 5 CX5: the same question, the scope's pieces given whole (no search, so no
    translation): it shares no word with the Marathi text and the pieces have no score, so
    only the numbers asked gate it. B still gets only its own owner."""
    a, b, pieces = _world(db)
    whole = make_settings(model_embed="test-embed", query_aspects_max=0)  # ANSWER_WHOLE_DOC_TOKENS on

    def ask(customer: str) -> tuple[dict[str, Any], Model]:
        model = Model()
        service = AnswerService(db=db, model=model, settings=whole, clock=lambda: NOW)
        return service.answer(customer, QUESTION).to_json(), model

    for_a, model = ask(a)
    assert model.translated == 0 and for_a["status"] == "answered"
    assert {c["ref_id"] for c in for_a["citations"]} == {pieces[a][0]}
    assert for_a["label"] == "needs_review"
    for_b, _ = ask(b)
    dumped = json.dumps(for_b, ensure_ascii=False)
    assert A_NAME not in dumped and pieces[a][0] not in dumped
    assert [s["text"] for s in for_b["sentences"]] == [f"{B_NAME}."]
