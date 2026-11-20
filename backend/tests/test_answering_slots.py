"""Ask's evidence slots: spread over pages (#722), and how many by the question's kind (#723)."""

import dataclasses

import pytest

from kaagaz.answering import draft
from kaagaz.answering import service as answering
from kaagaz.answering.expansion import NO_EXPANSION
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.service import AnswerService
from kaagaz.answering.slots import POOL_PER_SLOT, Kind, evidence_slots, question_kind, spread
from kaagaz.search.hybrid import MAX_LIMIT
from kaagaz.search.scope import EVERYTHING
from tests.test_answering_context import (
    SEARCH_ONLY,
    A,
    FakeContext,
    Fused,
    Routed,
    answers,
    blocks,
    cites,
    page,
)
from tests.test_answering_golden import NOW, UNUSED_DB, FakeFixedQueries, FakeHit, FakeModel, FakeUnchecked

A_PDF, B_PDF, C_PDF = "a.pdf", "b.pdf", "c.pdf"


def pages(document: str, *numbers: int) -> list[FakeHit]:
    """One piece a page number given, in that order (a number twice: two pieces of that page)."""
    return [page(n, f"{document} piece {at}", document=document, seq=at) for at, n in enumerate(numbers)]


def test_one_page_keeps_two_slots_and_the_next_hits_take_the_rest() -> None:
    same = pages(A_PDF, 4, 4, 4, 4)
    others = [*pages(B_PDF, 1), *pages(C_PDF, 7)]

    kept = spread([*same, *others], 4)

    assert kept == [same[0], same[1], others[0], others[1]]


def test_one_document_keeps_every_slot_its_pages_fill() -> None:
    """Principal review: "List the conditions in the Sharma lease" keeps all the lease's pages,
    never three of them and other leases."""
    lease = pages(A_PDF, 1, 2, 3, 4, 5, 6)
    others = pages(B_PDF, 1, 2, 3)

    assert spread([*lease, *others], 7) == [*lease, others[0]]


def test_capped_hits_fill_the_slots_no_other_hit_can_after_the_rest() -> None:
    mine = pages(A_PDF, 4, 4, 4, 5)
    other = pages(B_PDF, 2)

    kept = spread([*mine, *other], 5)

    assert kept == [mine[0], mine[1], mine[3], other[0], mine[2]]  # a third piece of page 4 is a repeat


def test_fewer_hits_than_slots_keeps_them_all_in_order() -> None:
    hits = pages(A_PDF, 1, 1, 1)

    assert spread(hits, 8) == hits
    assert spread([], 8) == []


def test_the_service_spreads_the_hits_then_adds_topic_pieces_and_neighbours() -> None:
    """The search is asked for POOL_PER_SLOT hits a slot; the draft reads the spread hits, then the
    topic pieces, then the neighbours, each numbered after them (CX6 and Q4 unchanged)."""
    settings = dataclasses.replace(SEARCH_ONLY, answer_max_evidence=3)
    crowd = [page(9, f"Casual leave days, rule {at}.", seq=at) for at in range(5)]  # one page, best first
    theirs = page(2, "Casual leave: 10 a year.", document="staff-rules.pdf")  # ranked 6th
    topic = page(18, "Maternity leave: 26 weeks.")
    near = page(10, "Casual leave may be carried over.")
    retriever = Fused([*crowd, theirs])
    retriever.near = [near]
    context = FakeContext([*crowd, topic], topics=[("leave", ("maternity leave",), [crowd[0], topic])])
    model = Routed(cites(("Casual leave is 10 days a year.", theirs.text)))

    answers(retriever, context, model, settings).answer(A, "Casual leave days?")

    assert {call[3] for call in retriever.calls} == {POOL_PER_SLOT * 3}
    order = [crowd[0], crowd[1], theirs, topic, near]
    assert blocks(model.drafted[0]) == [hit.text for hit in draft.edges(order)]


@pytest.mark.parametrize(("exclude", "read"), [(False, 4), (True, 12)])
def test_translation_reads_the_first_slots_hits_or_every_hit_on_the_other_documents_route(
    exclude: bool, read: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Principal review: the script to translate into comes from the first hits as before #722's
    larger search; on the "any other document" route (``exclude``) from every hit, as before."""
    settings = dataclasses.replace(SEARCH_ONLY, answer_max_evidence=4, answer_translate_question=True)
    sellers = [page(n, f"Seller {n}", document=f"deed-{n}.pdf") for n in range(1, 13)]
    retriever = Fused(sellers)
    texts: list[int] = []

    def scripts(question: str, given: list[str]) -> list[str]:
        texts.append(len(given))
        return []

    monkeypatch.setattr(answering, "target_scripts", scripts)
    service = AnswerService(
        db=UNUSED_DB,
        model=Routed(cites()),
        settings=settings,
        clock=lambda: NOW,
        retriever=retriever,
        translated_search=retriever,
        fixed_queries=FakeFixedQueries({}),
        unchecked=FakeUnchecked(),
        translator=FakeModel(),
    )
    left_out = "elsewhere" if exclude else None  # no hit is in it: every one is searched and read

    service._search_answer(
        A, None, "Seller?", normalise("Seller?"), "Seller?", EVERYTHING, NO_EXPANSION, exclude=left_out
    )

    assert texts == [read]


# --- #723: the number of slots from the question's kind ---------------------------------------


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("Who is the buyer?", "unknown"),  # a single fact keeps the setting (principal review)
        ("Who are the owners of Gat 12?", "unknown"),
        ("List all the invoices from Sharma Traders", "list"),
        ("How many invoices came from Sharma Traders?", "list"),
        ("Compare the rent in the two leases", "compare"),
        ("Which vendor had the highest invoice?", "compare"),
        ("Kiska profit sabse jyada tha last month?", "compare"),  # Hinglish: sabse jyada is highest
        ("Give me a summary of the lease", "summary"),
        ("Explain the leave policy", "summary"),
        ("What is the grand total of INV-1042?", "unknown"),
        ("Rate per bag?", "unknown"),
    ],
)
def test_the_kind_comes_from_the_question_s_words(question: str, kind: str) -> None:
    assert question_kind(question) == kind


@pytest.mark.parametrize(
    ("kind", "most", "slots"),
    [
        ("unknown", 8, 8),
        ("list", 8, 10),
        ("compare", 8, 10),
        ("summary", 8, 10),
        ("list", 1, 1),  # never none
        ("unknown", 0, 1),
        ("summary", 90, MAX_LIMIT),  # never past the search's limit
    ],
)
def test_the_slots_scale_the_setting_by_kind_within_bounds(kind: Kind, most: int, slots: int) -> None:
    assert evidence_slots(kind, most) == slots


@pytest.mark.parametrize(("question", "slots"), [("Who is the seller?", 8), ("List every seller", 10)])
def test_the_service_searches_and_keeps_the_kind_s_slots(question: str, slots: int) -> None:
    settings = dataclasses.replace(SEARCH_ONLY, answer_max_evidence=8)
    sellers = [page(n, f"Seller {n}", document=f"deed-{n}.pdf") for n in range(1, 41)]
    retriever = Fused(sellers)
    model = Routed(cites())

    answers(retriever, FakeContext(sellers), model, settings).answer(A, question)

    assert {call[3] for call in retriever.calls} == {POOL_PER_SLOT * slots}
    assert len(blocks(model.drafted[0])) == slots


def test_a_which_documents_question_keeps_its_own_count() -> None:
    settings = dataclasses.replace(SEARCH_ONLY, answer_max_evidence=8)
    sellers = [page(n, f"Seller {n}", document=f"deed-{n}.pdf") for n in range(1, 21)]
    retriever = Fused(sellers)
    model = Routed(cites())

    answers(retriever, FakeContext(sellers), model, settings).answer(A, "Which documents name a seller?")

    assert {call[3] for call in retriever.calls} == {MAX_LIMIT}
    assert len(blocks(model.drafted[0])) == 8
