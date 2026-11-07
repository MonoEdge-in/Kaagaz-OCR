"""PRD 5 CX7 without a database: the made-up 20-page offer letter (tests/exit_fixtures.py)
read to the end by the real pipeline with the in-memory stores, as a digital PDF and as
scans the Gemini reader reads through its fake client, then asked through the real
AnswerService over the in-memory ports of tests/test_answering_context.py (``Fused``,
``FakeContext``), built from what the pipeline saved: its pieces, its topics and its card.
The same questions on PostgreSQL are tests/test_context_offer_letter_db.py, which reads the
letter with ``processor`` and ``OfferModel`` from here.

``OfferModel`` is every model call. The card is ``CARD``; the map puts every leave piece
under the topic "leave" and the notice piece under "notice period"; the rewrite gives the
question back with its ``ASPECTS``; the draft quotes each line of ``QUOTES`` from the
evidence block that holds it, as a model that copies correctly would, and also claims the
card (``CARD_CLAIM``) with the id after the last block, which the card never has (M2).
It embeds a text as its hashed words (``ExitModel``).
"""

import dataclasses
import functools
import io
import re
from typing import Any

import pytest

from kaagaz.answering import draft, rewrite
from kaagaz.answering.service import AnswerService
from kaagaz.chunking.sections import MAP_SYSTEM_PROMPT, MapBudget
from kaagaz.extraction.card import CARD_SYSTEM_PROMPT
from kaagaz.extraction.understanding import ModelBudget
from kaagaz.ingestion import sniff
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.pipeline import DocumentContent, DocumentProcessor
from kaagaz.preparing.pages import prepare_pages
from kaagaz.reading.ocr import MeteredOcrEngine
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.scanning.gate import ScanGate, Verdict
from kaagaz.settings import Settings
from tests.exit_fixtures import (
    CASUAL_LEAVE,
    HOLIDAYS,
    MATERNITY_LEAVE,
    NOTICE_END,
    NOTICE_START,
    OFFER_LEAVE_PAGES,
    OFFER_NOTICE_PAGES,
    offer_letter_pdf,
    offer_letter_scan_pdf,
    offer_letter_scan_replies,
)
from tests.fakes import (
    FakeScanner,
    MemoryDocuments,
    MemoryPageImages,
    MemoryPipelineStore,
    MemoryProgress,
    MemoryQueue,
    MemoryRuns,
    MemoryStatuses,
    MemoryStorage,
    fake_sheet_pages,
)
from tests.settings_factory import make_settings
from tests.support import SeqIds
from tests.test_answering_context import A, FakeContext, Fused, blocks
from tests.test_answering_golden import NOW, UNUSED_DB, FakeFixedQueries, FakeHit, FakeUnchecked
from tests.test_exit_prd3_db import BLOCK, QUESTION, ExitModel
from tests.test_extraction_card import reply as card_reply
from tests.test_gemini_ocr import MODEL as GEMINI_MODEL
from tests.test_gemini_ocr import build as gemini_build
from tests.test_gemini_ocr import reply as gemini_reply
from tests.test_pipeline_gemini import T0, MemoryCache, MemoryReadAgain

# OCR_ENGINE is not gemini here, so the reader reads one page at a time (wiring.page_pool):
# the fake client's replies, scripted in page order, meet their pages.
SETTINGS = make_settings(page_image_dpi=72, ocr_dpi=72, max_pages=30)
SEARCH_ONLY = dataclasses.replace(SETTINGS, answer_whole_doc_tokens=0)
MODES = {"whole": SETTINGS, "search": SEARCH_ONLY}
POLICY = UploadPolicy(
    max_bytes=300_000,
    allowed_types=frozenset({sniff.PDF}),
    type_limits=limits_by_type(pdf=300_000, image=300_000, sheet=300_000),
)
PREPARE = functools.partial(
    prepare_pages, dpi=72, max_pages=30, max_image_pixels=40_000_000, blank_stddev=3.0, low_res_dpi=150
)

LEAVES = "How many leaves do I get?"
NOTICE = "What is the notice period?"
QUESTIONS = (LEAVES, NOTICE)
ASPECTS = {LEAVES: ("holidays", "casual leave", "maternity leave"), NOTICE: ()}
QUOTES = {LEAVES: (HOLIDAYS, CASUAL_LEAVE, MATERNITY_LEAVE), NOTICE: (f"{NOTICE_START} {NOTICE_END}",)}
CARD = card_reply(
    doc_type="letter",
    title="Offer of employment",
    summary="An offer of employment as Senior Accountant at Patil Textiles.",
)
CARD_CLAIM = "The letter is an offer of employment."
CARD_LINE = re.compile(r"<cards>\n(.*?)\n</cards>", re.DOTALL)
FOOTER = re.compile(r"Page \d+ of 20")
_PIECE = re.compile(r'<piece seq="(\d+)">\n(.*?)\n</piece>', re.DOTALL)
_SECTION = re.compile(r'<section seq="(\d+)"')


class OfferModel(ExitModel):
    """Every model call of the offer letter (the module docstring); ``drafts`` keeps each
    draft's user prompt, ``rewrites`` each rewrite's."""

    def __init__(self) -> None:
        super().__init__()
        self.drafts: list[str] = []

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
        if system == CARD_SYSTEM_PROMPT:
            return dict(CARD)
        if system == MAP_SYSTEM_PROMPT.format(topic_max=SETTINGS.topic_max_per_doc):
            return _map(user)
        asked = QUESTION.findall(user)[0]
        if system == rewrite.SYSTEM_PROMPT:
            self.rewrites.append(user)
            return {"question": asked, "aspects": list(ASPECTS[asked])}
        assert system == draft.SYSTEM_PROMPT
        self.drafts.append(user)
        bodies = {int(n): " ".join(body.split()) for n, body in BLOCK.findall(user)}
        holding = {quote: [n for n, body in bodies.items() if quote in body] for quote in QUOTES[asked]}
        sentences = [{"text": quote, "evidence": found[:1]} for quote, found in holding.items() if found]
        claim = {"text": CARD_CLAIM, "evidence": [len(bodies) + 1]}  # the card's line has no id
        return {"status": "answered" if sentences else "not_found", "sentences": [*sentences, claim]}


def _map(user: str) -> dict[str, Any]:
    """The map call's reply: a summary for each section, and the two topics with the seqs of
    every piece that discusses them."""
    pieces = {int(n): text.casefold() for n, text in _PIECE.findall(user)}
    return {
        "sections": [
            {"seq": int(n), "summary": f"Part {int(n) + 1} of the offer letter."}
            for n in _SECTION.findall(user)
        ],
        "topics": [
            {
                "topic": "leave",
                "aliases": ["leaves", "holidays", "casual leave", "maternity leave"],
                "piece_seqs": [n for n, text in pieces.items() if "leave" in text or "holiday" in text],
            },
            {
                "topic": "notice period",
                "aliases": ["notice"],
                "piece_seqs": [n for n, t in pieces.items() if "notice" in t],
            },
        ],
    }


def processor(model: OfferModel, *, scanned: bool, **stores: Any) -> DocumentProcessor:
    """The pipeline for the offer letter: ``model`` for the card and the map, and, when
    ``scanned``, the Gemini reader (OCR_MODEL) over its fake client serving
    ``offer_letter_scan_replies`` page by page; else no OCR engine. ``stores``: the gate and
    the status, document, piece, run, progress, page cache and read-again stores, in memory
    or on PostgreSQL."""
    replies = [gemini_reply(each) for each in offer_letter_scan_replies()]
    meter = MeteredOcrEngine(gemini_build(*replies).engine, timer=lambda: 0.0) if scanned else None
    return DocumentProcessor(
        socket=build_reader_socket(SETTINGS, meter),
        prepare=PREPARE,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        ocr_meter=meter,
        clock=lambda: T0,
        timer=lambda: 0.0,
        ocr_model=GEMINI_MODEL if scanned else None,
        chat=model,
        chat_model=model.chat_model,
        model_budget=ModelBudget.from_settings(SETTINGS),
        map_budget=MapBudget.from_settings(SETTINGS),
        timezone=SETTINGS.timezone,
        **stores,
    )


def summary(answer: dict[str, Any]) -> tuple[Any, ...]:
    """What two readings of the letter must answer alike: the status, the sentences and each
    citation's pages (the label may differ: a scan is never verified)."""
    cited = [(c["page_no"], c["page_from"], c["page_to"], c["snippet"]) for c in answer["citations"]]
    return answer["status"], [s["text"] for s in answer["sentences"]], cited


# --- in memory ---------------------------------------------------------------------------------


@functools.cache
def read(scanned: bool) -> DocumentContent:
    """The letter read to the end in memory, for the customer ``A``: what the pipeline saved."""
    statuses, storage = MemoryStatuses(), MemoryStorage()
    documents = MemoryDocuments(statuses)
    runs = MemoryRuns(documents)
    documents.runs = statuses.runs = runs
    store = MemoryPipelineStore(runs)
    run = processor(
        OfferModel(),
        scanned=scanned,
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, statuses),
        statuses=statuses,
        documents=documents,
        store=store,
        runs=runs,
        progress=MemoryProgress(documents, statuses),
        page_cache=MemoryCache(),
        read_again=MemoryReadAgain(documents),
    )
    data = offer_letter_scan_pdf() if scanned else offer_letter_pdf()
    upload = UploadService(POLICY, documents, storage, MemoryQueue(), new_id=SeqIds())
    job = Job(A, upload.accept(A, io.BytesIO(data)).document_id)
    run(job, lambda: None)
    content = store.content(A, job.document_id)
    assert content is not None
    return content


def ask(
    content: DocumentContent, question: str, settings: Settings
) -> tuple[dict[str, Any], OfferModel, Fused]:
    """``question`` through the real AnswerService over the saved pieces, topics and card."""
    hits = [
        FakeHit(
            piece_id=f"{content.document_id}#{seq}",
            document_id=content.document_id,
            document_name="offer-letter.pdf",
            detected_type="pdf",
            page_from=piece.page_from,
            page_to=piece.page_to,
            text=piece.text,
            regions=piece.regions(),
            label=piece.label,
            seq=seq,
            context_header=piece.context_header,
        )
        for seq, piece in enumerate(content.pieces)
    ]
    topics = [(t.topic, t.aliases, [hits[seq] for seq in t.piece_seqs]) for t in content.topics]
    context = FakeContext(
        hits, topics=topics, cards={content.document_id: ("offer-letter.pdf", content.facts.card)}
    )
    model, retriever = OfferModel(), Fused(hits)
    service = AnswerService(
        db=UNUSED_DB,
        model=model,
        settings=settings,
        clock=lambda: NOW,
        retriever=retriever,
        translated_search=retriever,
        fixed_queries=FakeFixedQueries({}),
        unchecked=FakeUnchecked(),
        context=context,
    )
    return service.answer(A, question).to_json(), model, retriever


def test_cx7_the_pipeline_saves_the_leave_and_notice_topics_and_the_card() -> None:
    content = read(False)

    pages = {t.topic: {content.pieces[seq].page_from for seq in t.piece_seqs} for t in content.topics}
    assert pages == {"leave": set(OFFER_LEAVE_PAGES.values()), "notice period": {OFFER_NOTICE_PAGES[0]}}
    assert content.facts.card is not None and content.facts.card["title"] == CARD["title"]


def test_cx5_whole_document_mode_cites_pages_4_9_and_18_from_every_piece_in_order() -> None:
    content = read(False)
    answer, model, retriever = ask(content, LEAVES, SETTINGS)

    assert [s["text"] for s in answer["sentences"]] == list(QUOTES[LEAVES])
    assert {c["page_no"] for c in answer["citations"]} == set(OFFER_LEAVE_PAGES.values())
    (prompt,) = model.drafts
    # Changed on purpose (#381, 27 Oct 2026): every piece once, ranked by one search of the question
    # and laid out with the best at both ends, never in reading order.
    assert sorted(blocks(prompt)) == sorted(piece.text for piece in content.pieces)
    assert blocks(prompt) != [piece.text for piece in content.pieces]
    assert model.rewrites == [] and len(retriever.calls) == 1  # no rewrite, one search to rank


def test_cx6_with_no_whole_document_the_aspects_and_the_leave_topic_find_pages_4_9_and_18() -> None:
    content = read(False)
    answer, model, retriever = ask(content, LEAVES, SEARCH_ONLY)

    assert [s["text"] for s in answer["sentences"]] == list(QUOTES[LEAVES])
    assert {c["page_no"] for c in answer["citations"]} == set(OFFER_LEAVE_PAGES.values())
    # Changed on purpose (#535): the early search of the question (no other text), then the aspects.
    assert len(model.rewrites) == 1 and [len(texts) for texts in retriever.fused] == [0, 3]
    (prompt,) = model.drafts
    assert len(blocks(prompt)) < len(content.pieces)  # searched, not the whole letter


@pytest.mark.parametrize("mode", MODES)
def test_cx7_the_notice_period_is_one_citation_over_pages_11_and_12(mode: str) -> None:
    answer, _, _ = ask(read(False), NOTICE, MODES[mode])

    (citation,) = answer["citations"]
    assert (citation["page_from"], citation["page_to"]) == OFFER_NOTICE_PAGES
    assert {region["page"] for region in citation["regions"]} == set(OFFER_NOTICE_PAGES)
    assert NOTICE_START in citation["snippet"] and NOTICE_END in citation["snippet"]


@pytest.mark.parametrize("mode", MODES)
def test_m2_one_card_line_no_citation_past_the_evidence_and_no_footer_cited(mode: str) -> None:
    content = read(False)
    texts = {f"{content.document_id}#{seq}": piece.text for seq, piece in enumerate(content.pieces)}
    for question in QUESTIONS:
        answer, model, _ = ask(content, question, MODES[mode])

        (prompt,) = model.drafts
        (cards,) = CARD_LINE.findall(prompt)
        assert cards == f"AI summary, not evidence: offer-letter.pdf | {CARD['title']} | letter", question
        assert CARD_CLAIM not in [s["text"] for s in answer["sentences"]]
        assert answer["citations"] and all(texts[c["ref_id"]] in blocks(prompt) for c in answer["citations"])
        assert not any(FOOTER.search(c["snippet"]) for c in answer["citations"]), question


def test_cx7_the_scans_read_by_gemini_give_the_same_pieces_topics_and_answers() -> None:
    typed, scanned = read(False), read(True)

    assert [(p.text, p.page_from, p.page_to) for p in scanned.pieces] == [
        (p.text, p.page_from, p.page_to) for p in typed.pieces
    ]
    assert {page.read_by for page in scanned.pages} == {GEMINI_MODEL}
    assert scanned.topics == typed.topics
    for settings in MODES.values():
        for question in QUESTIONS:
            assert summary(ask(scanned, question, settings)[0]) == summary(ask(typed, question, settings)[0])
