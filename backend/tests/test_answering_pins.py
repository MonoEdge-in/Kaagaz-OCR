"""Most relevant picks as a ranking signal (Tushar, 16 Oct 2026; kaagaz/answering/pins.py).
No database: the service runs on the golden fakes; which picks a question gets (the asker's
own, live documents, the newest of each answer) is tests/test_pins_ranking_db.py."""

import dataclasses
import logging
from typing import Any

import pytest

from kaagaz.answering.evidence import Evidence, enough_evidence, number_evidence
from kaagaz.answering.pins import LIFT_OTHER, LIFT_SAME, Pin, lifts, pinned_first
from kaagaz.answering.service import AnswerService
from tests.test_answering_golden import (
    NOW,
    SETTINGS,
    UNUSED_DB,
    A,
    FakeFixedQueries,
    FakeHit,
    FakeModel,
    FakeRetriever,
    FakeUnchecked,
    piece,
)

QUESTION = "Who leased the shop on Jalna road?"
TEXT = "The shop on Jalna road is leased to Patil."
KEYWORDS = ["shop", "jalna", "road", "leased"]


def deeds() -> list[FakeHit]:
    return [piece(f"deed-{letter}.pdf", TEXT) for letter in "abcde"]


def cite_first(system: str, user: str) -> dict[str, Any]:
    return {"status": "answered", "sentences": [{"text": TEXT, "evidence": [1]}]}


def ask(pieces: list[FakeHit], pins: list[Pin], question: str = QUESTION) -> tuple[Any, FakeModel]:
    model = FakeModel(cite_first)
    service = AnswerService(
        db=UNUSED_DB,
        model=model,
        settings=SETTINGS,
        clock=lambda: NOW,
        retriever=FakeRetriever({A: pieces}),
        fixed_queries=FakeFixedQueries({A: []}),
        unchecked=FakeUnchecked(),
    )
    return service.answer(A, question, pins=pins), model


def alone(item: Evidence) -> bool:
    return enough_evidence([item], KEYWORDS, SETTINGS.answer_min_evidence_score, []) in ("score", "keywords")


def names(evidence: list[Evidence]) -> list[str]:
    return [item.hit.document_name for item in evidence]


def test_a_page_picked_in_this_conversation_moves_up_in_a_later_question() -> None:
    pieces = deeds()
    picked = pieces[3]
    answer, _ = ask(pieces, [Pin(picked.document_id, 1, same_conversation=True)])
    assert answer.status == "answered"
    # The model cites evidence 1: the picked deed is first in the evidence, and its rank says so.
    assert answer.citations[0].document_name == "deed-d.pdf" and answer.citations[0].rank == 1
    assert [s.text for s in answer.sentences] == [TEXT]  # the answer's words are as before

    unpinned, _ = ask(pieces, [])
    assert unpinned.citations[0].document_name == "deed-a.pdf"


def test_a_pick_moves_a_hit_up_by_at_most_its_lift_in_a_steady_order() -> None:
    evidence = number_evidence(deeds())
    last = evidence[-1].hit.document_id
    ordered, moved = pinned_first(evidence, {last: {1: LIFT_SAME}}, alone)
    assert names(ordered) == ["deed-a.pdf", "deed-e.pdf", "deed-b.pdf", "deed-c.pdf", "deed-d.pdf"]
    assert [item.n for item in ordered] == [1, 2, 3, 4, 5] and moved == 1
    assert pinned_first(evidence, {last: {1: LIFT_SAME}}, alone) == (ordered, moved)  # every run
    weak, _ = pinned_first(evidence, {last: {1: LIFT_OTHER}}, alone)
    assert names(weak)[-2:] == ["deed-e.pdf", "deed-d.pdf"]  # another conversation: one place


def test_a_pick_never_lifts_a_hit_that_does_not_pass_on_its_own() -> None:
    weak = piece("jalna-note.pdf", "Jalna")  # one word of four: not enough by itself
    pieces = [*deeds(), weak]
    evidence = number_evidence(pieces)
    ordered, moved = pinned_first(evidence, {weak.document_id: {1: LIFT_SAME}}, alone)
    assert names(ordered) == names(evidence) and moved == 0

    answer, _ = ask(pieces, [Pin(weak.document_id, 1, same_conversation=True)])
    assert answer.citations[0].document_name == "deed-a.pdf"
    assert "jalna-note.pdf" not in {c.document_name for c in answer.citations}


def test_a_pick_on_another_page_or_document_or_a_neighbour_moves_nothing() -> None:
    evidence = number_evidence(deeds())
    last = evidence[-1].hit.document_id
    for picked in ({last: {2: LIFT_SAME}}, {"other-document": {1: LIFT_SAME}}, {}):
        assert pinned_first(evidence, picked, alone) == (evidence, 0)
    near = [dataclasses.replace(item, neighbour=True) for item in evidence]
    assert pinned_first(near, {last: {1: LIFT_SAME}}, alone) == (near, 0)


def test_a_hit_across_pages_moves_for_a_pick_on_any_of_them() -> None:
    long = dataclasses.replace(piece("lease.pdf", TEXT), page_from=2, page_to=4)
    evidence = number_evidence([*deeds(), long])
    ordered, moved = pinned_first(evidence, {long.document_id: {3: LIFT_SAME}}, alone)
    assert moved == 1 and names(ordered).index("lease.pdf") == 2


@pytest.mark.parametrize(
    ("earlier", "lift"),
    [
        ("Who leased the shop on Jalna road?", LIFT_OTHER),  # the same words
        ("Shop on Jalna road lease", LIFT_OTHER),
        ("What is the rent of the Pune flat?", None),  # other words: no lift
        ("", None),
    ],
)
def test_a_pick_from_another_conversation_counts_only_on_the_same_words(
    earlier: str, lift: int | None
) -> None:
    found = lifts([Pin("d", 1, same_conversation=False, question=earlier)], KEYWORDS)
    assert found == ({} if lift is None else {"d": {1: lift}})


def test_this_conversations_pick_is_the_stronger_and_two_picks_keep_the_larger() -> None:
    pins = [Pin("d", 1, same_conversation=False, question=QUESTION), Pin("d", 1, same_conversation=True)]
    assert lifts(pins, KEYWORDS) == {"d": {1: LIFT_SAME}}
    assert lifts([Pin("d", 1, same_conversation=True)], []) == {"d": {1: LIFT_SAME}}


def test_the_timing_line_counts_the_hits_a_pick_moved(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")
    pieces = deeds()
    ask(pieces, [Pin(pieces[4].document_id, 1, same_conversation=True)])
    ask(pieces, [])
    lines = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert [" pinned=1 " in lines[0], " pinned=0 " in lines[1]] == [True, True]
    assert "deed" not in " ".join(lines)
