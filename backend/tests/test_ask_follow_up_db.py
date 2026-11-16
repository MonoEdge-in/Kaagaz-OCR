"""#626 (production, 15 Nov 2026): the screenshot's follow-up end to end, through the API with the
real AnswerService and a fake model, while the word list's statement times out as it did then.
An answer about a land document, then, over All documents: "Can you find me the measurement of
this if it is seen in any other document?". It once replied "Sure.", not found naming "Can" and
"find", then "Kaagaz is busy". Now the rewrite resolves "this" from the earlier answer and its
document (#621), the search looks for the measurement of that land, and nothing is a 503 (#619)."""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import rewrite
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from tests.settings_factory import make_settings
from tests.test_answering_clarify import plan
from tests.test_answering_golden import NOW, evidence_blocks
from tests.test_api_support import CSRF, Api
from tests.test_search_db import SLOW_WORD, add_pieces, axis

pytestmark = pytest.mark.db

ASKED = "Can you find me the measurement of this if it is seen in any other document?"
RESOLVED = "Find the measurement of Gat Number 61 at Wagholi in any other document"


class Model:
    """Embeds every text on one axis; the rewrite resolves the follow-up only when the history
    and its documents name the land; the draft quotes its first evidence block."""

    chat_model, embed_model = "fake-chat", "fake-embed"

    def __init__(self) -> None:
        self.histories: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [axis(0) for _ in texts]

    def chat_json(self, *, system: str, user: str, schema: Any, **_: Any) -> dict[str, Any]:
        if system == rewrite.SYSTEM_PROMPT:
            question = user.split("<question>\n", 1)[1].split("\n</question>", 1)[0]
            history = user.split("<history>\n", 1)[1].split("\n</history>", 1)[0]
            self.histories.append(history)
            named = "Gat Number 61" in history and "Documents: Sale Deed Wagholi" in history
            return {"question": RESOLVED if named else question, "aspects": [], "plan": plan()}
        n, text = next(iter(evidence_blocks(user).items()))
        return {"status": "answered", "sentences": [{"text": text, "evidence": [n]}]}


def test_the_screenshots_follow_up_is_rewritten_searched_for_the_measurement_and_never_busy(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(model_embed="test-embed", answer_whole_doc_tokens=0, query_aspects_max=0)
    model = Model()
    answers = AnswerService(db=db, model=model, settings=settings, clock=lambda: NOW)
    api = Api(db, settings=settings, model=model, answers=answers)
    customer, _ = api.customer()
    signed = api.sign_in(customer)
    deed = api.add_document(customer, name="Sale Deed Wagholi.pdf")
    extract = api.add_document(customer, name="7-12 extract Wagholi.pdf")
    add_pieces(db, customer, deed, [("Sale deed of Gat Number 61 at Wagholi, sold by Ramesh Patil", axis(0))])
    add_pieces(
        db, customer, extract, [("7/12 extract: Gat Number 61, Wagholi, measurement 0.60 hectare", axis(0))]
    )
    searched: list[str] = []
    lists = hybrid._lists

    def recorded(conn: Any, customer_id: str, query_text: str, *args: Any) -> Any:
        searched.append(query_text)
        return lists(conn, customer_id, query_text, *args)

    monkeypatch.setattr(hybrid, "_lists", recorded)

    def ask(question: str, **body: Any) -> dict[str, Any]:
        reply = signed.client.post("/api/chat", json={"question": question, **body}, headers=CSRF)
        assert reply.status_code == 200, reply.text  # never "Kaagaz is busy"
        return dict(reply.json())

    first = ask("What is this document about?", scope={"document_ids": [deed]})
    assert [c["document_id"] for c in first["answer"]["answer"]["citations"]] == [deed]

    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 100)  # production's word list timed out
    monkeypatch.setattr(hybrid, "_WORD", text(SLOW_WORD))
    searched.clear()
    second = ask(ASKED, conversation_id=first["conversation_id"], scope={})

    answer = second["answer"]["answer"]
    assert answer["rewritten_question"] == RESOLVED
    assert searched and all("measurement" in text and "61" in text for text in searched)
    assert answer["status"] == "answered" and "missing_words" not in answer
    assert extract in {c["document_id"] for c in answer["citations"]}
