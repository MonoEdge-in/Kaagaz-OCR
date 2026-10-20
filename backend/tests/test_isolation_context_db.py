"""Customer B never gets customer A's whole documents, topic or section pieces, or cards
(PRD 5 CX5, CX6, M2; real PostgreSQL), through the answer service's own port
(``PgRetriever``) and through ``AnswerService`` with its default settings (whole-document
mode and aspects on), even with A's exact document ids in the scope and A's piece as a
hit; nor A's saved sections and topics (CX4). Each is paired with A's own call as the
positive control. Marked ``isolation`` by its file name.
"""

import json
import re
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import rewrite
from kaagaz.answering.evidence import PgRetriever
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.search.scope import EVERYTHING, Scope
from tests.db_support import new_customer
from tests.fakes import FakeChat
from tests.settings_factory import make_settings
from tests.test_answering_neighbours_db import blocks
from tests.test_context_db import OFFER, _offer, _section, _topic
from tests.test_end_to_end_db import NOW
from tests.test_pipeline_store_db import new_document
from tests.test_pipeline_store_sections_db import MAP_REPLY, mapped_content
from tests.test_search_db import RECIPE, add_document, add_pieces, axis

pytestmark = pytest.mark.db


def _a_with_an_offer(db: Database) -> tuple[str, str, str]:
    """(A, B, A's offer letter with a leave topic, a section with a vector, and a card)."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    document, _ = _offer(db, a)
    _topic(db, a, document, "leave", ["holidays"], [1, 3, 4])
    _section(db, a, document, 0, (0, 4), "Leave and holidays.", axis(0))
    with db.customer(a) as conn:
        conn.execute(
            text("UPDATE documents SET card = CAST(:card AS jsonb) WHERE customer_id = :c AND id = :d"),
            {"c": a, "d": document, "card": json.dumps({"title": "Offer"})},
        )
    return a, b, document


def test_another_customer_gets_none_of_the_context(db: Database) -> None:
    a, b, document = _a_with_an_offer(db)
    port = PgRetriever(db, recipe=RECIPE, ef_search=40, neighbours=1, topic_pieces=12, min_score=0.6)
    theirs = Scope(document_ids=(document,))
    mine = port.whole(a, EVERYTHING, 100_000)
    assert mine is not None and [hit.text for hit in mine] == OFFER  # the control
    a_hit = mine[1]

    for customer, scope in ((b, EVERYTHING), (b, theirs)):
        assert port.whole(customer, scope, 100_000) is None
        assert port.topic_pieces(customer, ["leave", "holidays"], [a_hit], scope) == []
        assert port.section_pieces(customer, ["leave"], axis(0), [a_hit], scope) == []
        assert port.cards(customer, [document], scope) == {}

    assert {hit.seq for hit in port.topic_pieces(a, ["leave"], [], theirs)} == {1, 3, 4}
    assert [hit.seq for hit in port.section_pieces(a, ["leave"], axis(0), [], theirs)] == [0, 1, 2, 3, 4]
    assert port.cards(a, [document], theirs) == {document: ("offer.pdf", {"title": "Offer"})}


def test_cx4_another_customer_sees_no_section_or_topic(db: Database) -> None:
    customer, _ = new_customer(db)
    other, _ = new_customer(db)
    document = new_document(db, customer)
    PgPipelineStore(db).save(mapped_content(customer, document, FakeChat(MAP_REPLY)))

    seen = text(
        "SELECT (SELECT count(*) FROM document_sections WHERE document_id = :d)"
        " + (SELECT count(*) FROM document_topics WHERE document_id = :d)"
    )
    with db.customer(other) as conn:
        assert conn.execute(seen, {"d": document}).scalar_one() == 0
    with db.customer(customer) as conn:
        assert conn.execute(seen, {"d": document}).scalar_one() == 4  # the positive control


class CitesEverything:
    """Rewrites a question as asked with two aspects, and drafts one sentence for each
    evidence block, quoting it, so any piece the service is given is cited."""

    chat_model, embed_model = "fake-chat", "fake-embed"

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
        if system == rewrite.SYSTEM_PROMPT:
            asked = re.findall(r"<question>\n(.*?)\n</question>", user, re.DOTALL)[0]
            return {"question": asked, "aspects": ["holidays", "casual leave"]}
        found = [(n, body.splitlines()[-1]) for n, (_, body) in blocks(user).items()]
        sentences = [{"text": quoted, "evidence": [n]} for n, quoted in found]
        return {"status": "answered" if found else "not_found", "sentences": sentences}


def test_the_answer_service_with_default_settings_gives_b_nothing_of_as_document(db: Database) -> None:
    a, b, document = _a_with_an_offer(db)
    own = add_document(db, b, name="b-rules.pdf")
    add_pieces(db, b, own, [("Casual leave: 3 days a year.", axis(0))])
    settings = make_settings(model_embed="test-embed")  # ANSWER_WHOLE_DOC_TOKENS and QUERY_ASPECTS_MAX on
    assert settings.answer_whole_doc_tokens > 0 and settings.query_aspects_max > 0
    service = AnswerService(db=db, model=CitesEverything(), settings=settings, clock=lambda: NOW)
    theirs = Scope(document_ids=(document,))

    for_b = service.answer(b, "How many leaves do I get?", scope=theirs).to_json()
    dumped = json.dumps(for_b, ensure_ascii=False)
    assert for_b["status"] == "not_found"
    assert document not in dumped and not any(piece in dumped for piece in OFFER)

    for_a = service.answer(a, "How many leaves do I get?", scope=theirs).to_json()  # the control
    assert for_a["status"] == "answered"
    assert {c["document_id"] for c in for_a["citations"]} == {document}
