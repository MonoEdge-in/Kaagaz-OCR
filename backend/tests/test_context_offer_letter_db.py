"""PRD 5 CX7 end to end on PostgreSQL: the made-up 20-page offer letter (tests/exit_fixtures.py)
read to the end by the real pipeline and stores (``processor`` from
tests/test_context_offer_letter.py), embedded, then asked through the real AnswerService,
whose retriever and context are the PostgreSQL ones (whole documents, aspects fused,
_TOPIC_PIECES, section pieces and cards). Each reading is a new customer with the letter
only: once as a digital PDF, once as scans the Gemini reader reads through its fake client
(OCR_ENGINE gemini's reader, no network). The model is ``OfferModel``: no model is called.
The same questions without a database are in tests/test_context_offer_letter.py.
"""

import io
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering.service import AnswerService, topic_terms
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.page_reads import PgPageReads
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.runs import PgRuns
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.scanning.gate import ScanGate, Verdict
from kaagaz.search import hybrid
from kaagaz.search.indexing import embed_pieces
from kaagaz.settings import Settings
from tests.db_support import new_customer
from tests.exit_fixtures import (
    NOTICE_END,
    NOTICE_START,
    OFFER_LEAVE_PAGES,
    OFFER_NOTICE_PAGES,
    offer_letter_pdf,
    offer_letter_scan_pdf,
)
from tests.fakes import FakeScanner, MemoryQueue, MemoryStorage
from tests.test_answering_context import blocks
from tests.test_answering_golden import NOW
from tests.test_context_offer_letter import (
    ASPECTS,
    CARD,
    CARD_CLAIM,
    CARD_LINE,
    FOOTER,
    LEAVES,
    MODES,
    NOTICE,
    POLICY,
    QUESTIONS,
    QUOTES,
    SEARCH_ONLY,
    SETTINGS,
    OfferModel,
    processor,
    summary,
)
from tests.test_gemini_ocr import MODEL as GEMINI_MODEL

pytestmark = pytest.mark.db

PIECES = (
    "SELECT id, seq, text, page_from, page_to FROM pieces"
    " WHERE customer_id = :c AND document_id = :d ORDER BY seq"
)


@dataclass
class World:
    db: Database
    customer: str
    document: str

    def ask(self, question: str, settings: Settings) -> tuple[dict[str, Any], OfferModel]:
        """``question`` through the real AnswerService on PostgreSQL, with a new model."""
        model = OfferModel()
        service = AnswerService(db=self.db, model=model, settings=settings, clock=lambda: NOW)
        return service.answer(self.customer, question).to_json(), model

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        with self.db.customer(self.customer) as conn:
            return [tuple(row) for row in conn.execute(text(sql), {"c": self.customer, "d": self.document})]

    def pieces(self) -> list[tuple[Any, ...]]:
        """(id, seq, text, page_from, page_to) of each piece, in seq order."""
        return self.rows(PIECES)


def read(db: Database, data: bytes, *, scanned: bool) -> World:
    """The letter uploaded by a new customer, read to the end and embedded."""
    customer, _ = new_customer(db)
    storage, documents, model = MemoryStorage(), PgDocuments(db), OfferModel()
    run = processor(
        model,
        scanned=scanned,
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, documents),
        statuses=documents,
        documents=documents,
        store=PgPipelineStore(db),
        runs=PgRuns(db),
        progress=documents,
        page_cache=PgPageReads(db),
        read_again=documents,
    )
    document = (
        UploadService(POLICY, documents, storage, MemoryQueue())
        .accept(customer, io.BytesIO(data))
        .document_id
    )
    run(Job(customer, document), lambda: None)
    embed_pieces(db, customer, document, model)
    return World(db, customer, document)


@pytest.fixture(scope="module")
def typed(db: Database) -> World:
    return read(db, offer_letter_pdf(), scanned=False)


@pytest.fixture(scope="module")
def scanned(db: Database) -> World:
    return read(db, offer_letter_scan_pdf(), scanned=True)


def test_cx5_whole_document_mode_cites_pages_4_9_and_18_from_every_piece_in_order(typed: World) -> None:
    answer, model = typed.ask(LEAVES, SETTINGS)

    assert (answer["status"], [s["text"] for s in answer["sentences"]]) == ("answered", list(QUOTES[LEAVES]))
    assert {c["page_no"] for c in answer["citations"]} == set(OFFER_LEAVE_PAGES.values())
    (prompt,) = model.drafts
    assert blocks(prompt) == [row[2] for row in typed.pieces()]  # every piece, in seq order
    # Changed on purpose (PRD 6 B1): asked for its plan in the rewrite's call, never rewritten.
    assert len(model.rewrites) == 1 and answer["rewritten_question"] is None


def test_cx6_with_no_whole_document_the_aspects_and_the_leave_topic_find_pages_4_9_and_18(
    typed: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    searched: list[list[str]] = []
    fused = hybrid.search_translated

    def spy(*args: Any, **kwargs: Any) -> Any:
        searched.append([each for each, _ in kwargs["translations"]])
        return fused(*args, **kwargs)

    monkeypatch.setattr(hybrid, "search_translated", spy)
    answer, model = typed.ask(LEAVES, SEARCH_ONLY)

    assert (answer["status"], [s["text"] for s in answer["sentences"]]) == ("answered", list(QUOTES[LEAVES]))
    assert {c["page_no"] for c in answer["citations"]} == set(OFFER_LEAVE_PAGES.values())
    assert len(model.rewrites) == 1 and [len(texts) for texts in searched] == [len(ASPECTS[LEAVES])]
    (prompt,) = model.drafts
    assert len(blocks(prompt)) < len(typed.pieces())  # searched, not the whole letter
    # _TOPIC_PIECES: the question's own words name the saved topic "leave", which holds the
    # three leave pieces, wherever they are in the letter.
    with typed.db.customer(typed.customer) as conn:
        found = hybrid.topic_pieces(
            conn, customer_id=typed.customer, terms=topic_terms([LEAVES]), hits=[], per_document=12
        )
    assert sorted(hit.page_from for hit in found) == sorted(OFFER_LEAVE_PAGES.values())


@pytest.mark.parametrize("mode", MODES)
def test_cx7_the_notice_period_is_one_citation_over_pages_11_and_12(typed: World, mode: str) -> None:
    answer, _ = typed.ask(NOTICE, MODES[mode])

    (citation,) = answer["citations"]
    assert (citation["page_from"], citation["page_to"]) == OFFER_NOTICE_PAGES
    assert {region["page"] for region in citation["regions"]} == set(OFFER_NOTICE_PAGES)
    assert NOTICE_START in citation["snippet"] and NOTICE_END in citation["snippet"]


@pytest.mark.parametrize("mode", MODES)
def test_m2_one_card_line_no_citation_past_the_evidence_and_no_footer_cited(typed: World, mode: str) -> None:
    texts = {str(row[0]): row[2] for row in typed.pieces()}
    for question in QUESTIONS:
        answer, model = typed.ask(question, MODES[mode])

        (prompt,) = model.drafts
        (cards,) = CARD_LINE.findall(prompt)
        assert cards.startswith("AI summary, not evidence: ") and "\n" not in cards, question
        assert f"| {CARD['title']} | letter" in cards, question
        assert CARD_CLAIM not in [s["text"] for s in answer["sentences"]]  # the card has no id
        assert answer["citations"] and all(texts[c["ref_id"]] in blocks(prompt) for c in answer["citations"])
        assert not any(FOOTER.search(c["snippet"]) for c in answer["citations"]), question


def test_cx7_the_scans_read_by_gemini_give_the_same_pieces_and_answers(typed: World, scanned: World) -> None:
    def same(world: World) -> list[tuple[Any, ...]]:
        return [row[1:] for row in world.pieces()]

    assert same(scanned) == same(typed)
    assert {
        row[0]
        for row in scanned.rows("SELECT read_by FROM pages WHERE customer_id = :c AND document_id = :d")
    } == {GEMINI_MODEL}
    for settings in MODES.values():
        for question in QUESTIONS:
            assert summary(scanned.ask(question, settings)[0]) == summary(typed.ask(question, settings)[0])
