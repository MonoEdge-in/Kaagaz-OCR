"""PRD 6 package K (#644) end to end: through the API, with the real AnswerService and a fake model
whose rewrite never resolves "this" (production's refused rewrite, #626). A person reads one land
record, switches the scope to All documents and asks about "this"; asks for any other or related
document; asks four things at once in English, Marathi and Hindi; pastes 3,000 characters; asks
while the word list times out; switches scope; or asks about a document trashed or replaced since.
Every reply is a 200 with an answer, never a 503, and never names what the asker cannot see."""

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import draft, rewrite
from kaagaz.answering.focus import PgFocus
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.scope import EVERYTHING
from tests.land_support import add_land
from tests.settings_factory import make_settings
from tests.test_answering_golden import evidence_blocks
from tests.test_api_support import CSRF, Api, Signed
from tests.test_search_db import SLOW_WORD, add_pieces, axis

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
LAND: dict[str, Any] = {"doc_type": "land_record"}
_WORD = re.compile(r"\w+")


class Model:
    """Embeds a text about a rent agreement on its own axis (nothing is near it), every other on
    axis 0; the rewrite returns the question as asked; the draft quotes the MAX_SENTENCES evidence
    blocks sharing the most words with the question (then by text), so it never depends on ties."""

    chat_model, embed_model = "fake-chat", "fake-embed"

    def __init__(self) -> None:
        self.drafts = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [axis(5) if "rent" in t.casefold() else axis(0) for t in texts]

    def chat_json(self, *, system: str, user: str, schema: Any, **_: Any) -> dict[str, Any]:
        if system == rewrite.SYSTEM_PROMPT:
            return {"question": user.split("<question>\n", 1)[1].split("\n</question>", 1)[0], "aspects": []}
        assert system == draft.SYSTEM_PROMPT
        self.drafts += 1
        asked = set(_WORD.findall(user.split("<question>\n", 1)[1].split("\n</question>", 1)[0].casefold()))

        def shared(block: tuple[int, str]) -> tuple[int, str]:
            return -len(asked & set(_WORD.findall(block[1].casefold()))), block[1]

        quoted = sorted(evidence_blocks(user).items(), key=shared)[: draft.MAX_SENTENCES]
        return {"status": "answered", "sentences": [{"text": t, "evidence": [n]} for n, t in quoted]}


class World:
    """One organisation's land records and an invoice, each with its pieces and its land values."""

    def __init__(self, db: Database, *, settings_changes: dict[str, Any] | None = None) -> None:
        self.db = db
        settings = make_settings(
            model_embed="test-embed",
            answer_whole_doc_tokens=0,
            query_aspects_max=0,
            **(settings_changes or {}),
        )
        self.model = Model()
        answers = AnswerService(db=db, model=self.model, settings=settings, clock=lambda: NOW)
        self.api = Api(db, settings=settings, model=self.model, answers=answers)
        self.customer, _ = self.api.customer()
        self.signed: Signed = self.api.sign_in(self.customer)
        c = self.customer
        wagholi = [("village", "Wagholi")]
        self.deed = add_land(
            db,
            c,
            "Sale Deed Wagholi.pdf",
            sellers=["Ramesh Patil"],
            buyers=["Suresh Jadhav"],
            places=wagholi,
            survey="61",
            **LAND,
        )
        self.extract = add_land(
            db, c, "7-12 Extract Wagholi.pdf", [("Suresh Jadhav", None)], places=wagholi, survey="61", **LAND
        )
        self.kesnand = add_land(
            db,
            c,
            "Sale Deed Kesnand.pdf",
            buyers=["Suresh Jadhav"],
            places=[("village", "Kesnand")],
            survey="99",
            **LAND,
        )
        self.lonikand = add_land(
            db,
            c,
            "7-12 Extract Lonikand.pdf",
            [("Anil More", None)],
            places=[("village", "Lonikand")],
            survey="61",
            **LAND,
        )
        self.trashed = add_land(
            db,
            c,
            "Old Deed Wagholi.pdf",
            buyers=["Suresh Jadhav"],
            places=wagholi,
            survey="61",
            deleted_at=NOW,
            **LAND,
        )
        self.invoice = self.api.add_document(c, name="Invoice INV-7.pdf", doc_type="invoice")
        pieces = {
            self.deed: "Sale deed of Gat Number 61 at Wagholi, measurement 0.60 hectare, sold by Ramesh Patil"
            " to Suresh Jadhav",
            self.extract: "7/12 extract of Gat Number 61, Wagholi, measurement 0.60 hectare,"
            " owner Suresh Jadhav",
            self.kesnand: "Sale deed of Gat Number 99 at Kesnand, bought by Suresh Jadhav",
            self.lonikand: "7/12 extract of Gat Number 61, Lonikand, measurement 2.10 hectare,"
            " owner Anil More",
            self.trashed: "Old sale deed of Gat Number 61 at Wagholi, measurement 0.60 hectare",
            self.invoice: "Invoice INV-7 for cement bags, amount Rs. 12,000",
        }
        for document, said in pieces.items():
            add_pieces(db, c, document, [(said, axis(0))])
        self.names = {
            self.deed: "Sale Deed Wagholi",
            self.extract: "7-12 Extract Wagholi",
            self.kesnand: "Sale Deed Kesnand",
            self.lonikand: "7-12 Extract Lonikand",
            self.trashed: "Old Deed Wagholi",
            self.invoice: "Invoice INV-7",
        }

    def ask(self, question: str, **body: Any) -> dict[str, Any]:
        reply = self.signed.client.post("/api/chat", json={"question": question, **body}, headers=CSRF)
        assert reply.status_code == 200, reply.text  # never "Kaagaz is busy", never a 500
        return dict(reply.json())

    def first(self, document: str) -> dict[str, Any]:
        """A first question asked in ``document`` alone, citing it."""
        reply = self.ask("What is this document about?", scope={"document_ids": [document]})
        assert {c["document_id"] for c in reply["answer"]["answer"]["citations"]} == {document}
        return reply


def cited(answer: dict[str, Any]) -> set[str]:
    return {c["document_id"] for c in answer["citations"]}


def listed(answer: dict[str, Any]) -> list[str]:
    return [d["document_id"] for d in answer.get("documents", [])]


def never_named(answer: dict[str, Any], world: World, document: str) -> None:
    """Nothing of ``document`` in the reply: no citation, listing, name or id anywhere in it."""
    shown = json.dumps(answer, ensure_ascii=False)
    assert document not in shown
    assert world.names[document] not in shown


def test_after_a_switch_to_all_documents_this_is_the_earlier_document_and_its_facts(db: Database) -> None:
    world = World(db)
    first = world.first(world.deed)

    second = world.ask(
        "What is the measurement of this land?", conversation_id=first["conversation_id"], scope={}
    )["answer"]["answer"]

    assert second["status"] == "answered"
    assert "Survey 61, Wagholi" in (second["rewritten_question"] or "")  # the rewrite left "this"
    assert (
        second["assumptions"][0]
        == "Searching all your documents for Survey 61, Wagholi (from Sale Deed Wagholi)."
    )
    assert world.extract in cited(second)  # outside the first question's scope: the new one decides
    assert "suggestions" not in second  # no Home "Try one of these" mid-conversation


@pytest.mark.parametrize(
    "question",
    [
        "Is this measurement in any other document?",
        "याचे क्षेत्र इतर कागदपत्रात आहे का?",
        "iska area dusre document mein?",
    ],
)
def test_any_other_document_leaves_the_earlier_document_out(db: Database, question: str) -> None:
    world = World(db)
    first = world.first(world.deed)
    searched: list[str] = []
    lists = hybrid._lists

    def recorded(conn: Any, customer_id: str, query_text: str, *args: Any) -> Any:
        searched.append(query_text)
        return lists(conn, customer_id, query_text, *args)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(hybrid, "_lists", recorded)
        second = world.ask(question, conversation_id=first["conversation_id"], scope={})["answer"]["answer"]

    assert second["status"] == "answered"
    assert searched and all("61" in said for said in searched)  # its facts, not the raw sentence
    assert world.extract in cited(second)
    assert world.deed not in cited(second) | set(listed(second))  # it matches, but it is no other
    assert world.deed not in {d["document_id"] for d in second["related_documents"]}
    assert world.deed not in {d["id"] for d in (second.get("mentions") or {}).get("documents", [])}
    assert second["assumptions"][0].startswith("Searching your other documents for Survey 61, Wagholi")


def test_related_documents_by_the_same_land_or_owner_ranked_each_with_why(db: Database) -> None:
    world = World(db)
    first = world.first(world.extract)

    second = world.ask(
        "Find documents related to this land", conversation_id=first["conversation_id"], scope={}
    )["answer"]["answer"]

    assert second["status"] == "answered" and second["route"] == "fixed_query"
    assert listed(second) == [world.deed, world.kesnand]  # the same land first, then the same owner
    why = {d["document_id"]: d["why"] for d in second["documents"]}
    assert why[world.deed] == "Same Survey 61, Wagholi; same person Suresh Jadhav; same place Wagholi"
    assert why[world.kesnand] == "Same person Suresh Jadhav"
    # Each reason is a value as printed, on its page, in the related document itself.
    for document in second["documents"]:
        refs = [c for c in second["citations"] if c["n"] in document["citations"]]
        assert refs and all(c["document_id"] == document["document_id"] and c["regions"] for c in refs)
    for left_out in (world.extract, world.lonikand, world.trashed, world.invoice):  # Gat 61 of Lonikand is
        assert left_out not in cited(second) | set(listed(second))  # another land
    never_named(second, world, world.trashed)
    assert "mentions" not in second and "suggestions" not in second  # no other feature's words


def test_a_related_question_lists_only_documents_inside_the_new_scope(db: Database) -> None:
    world = World(db)
    first = world.first(world.extract)
    scope = {"document_ids": [world.kesnand, world.lonikand, world.invoice]}

    second = world.ask(
        "Find documents related to this land", conversation_id=first["conversation_id"], scope=scope
    )["answer"]["answer"]

    assert listed(second) == [world.kesnand]
    assert cited(second) == {world.kesnand}  # the deed shares more, but is outside the chosen scope
    assert second["assumptions"][0].startswith("Searching the documents you chose for Survey 61, Wagholi")


def test_a_scope_switch_never_reuses_the_old_scope_nor_narrows_to_the_focus(db: Database) -> None:
    world = World(db)
    first = world.first(world.deed)
    asked = {"conversation_id": first["conversation_id"]}

    narrower = world.ask(
        "What is the measurement of this land?",
        **asked,
        scope={"document_ids": [world.extract, world.invoice]},
    )["answer"]["answer"]
    everything = world.ask("Which documents mention cement bags?", **asked, scope={})["answer"]["answer"]

    assert cited(narrower) <= {world.extract, world.invoice} and world.extract in cited(narrower)
    assert cited(everything) == {world.invoice}  # the focus never narrows a question that names its own
    assert not everything["assumptions"]


def test_four_asks_in_english_marathi_and_hindi_are_answered_together_part_by_part(db: Database) -> None:
    world = World(db)
    asked = (
        "Who sold Gat 61 at Wagholi? गट ६१ वाघोलीचे क्षेत्र किती आहे?\n"
        "इनवॉइस INV-7 में कितनी राशि है? Is there any rent agreement for the godown?"
    )

    answer = world.ask(asked)["answer"]["answer"]

    assert answer["status"] == "answered"
    heads = [s["text"] for s in answer["sentences"] if s["text"].startswith('For "')]
    assert heads == [
        'For "Who sold Gat 61 at Wagholi?":',
        'For "गट ६१ वाघोलीचे क्षेत्र किती आहे?":',
        'For "इनवॉइस INV-7 में कितनी राशि है?":',
        'For "Is there any rent agreement for the godown?": I couldn\'t find this in your documents.',
    ]
    assert world.invoice in cited(answer) and world.deed in cited(answer)
    assert [c["n"] for c in answer["citations"]] == list(range(1, len(answer["citations"]) + 1))
    by_n = {c["n"]: c["document_id"] for c in answer["citations"]}
    texts = [s["text"] for s in answer["sentences"]]
    after = answer["sentences"][texts.index(heads[2]) + 1]  # the INV-7 part's first sentence
    assert [by_n[n] for n in after["citations"]] == [world.invoice]
    assert world.model.drafts == 3  # one draft a part found, none for the part that found nothing


def test_a_word_list_that_times_out_still_answers_every_part(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(db)
    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 100)  # production's word list timed out
    monkeypatch.setattr(hybrid, "_WORD", text(SLOW_WORD))

    answer = world.ask("Who sold Gat 61 at Wagholi? What is the amount of invoice INV-7?")["answer"]["answer"]

    assert answer["status"] == "answered"
    heads = [s["text"] for s in answer["sentences"] if s["text"].startswith('For "')]
    assert heads == ['For "Who sold Gat 61 at Wagholi?":', 'For "What is the amount of invoice INV-7?":']


def test_a_question_of_3000_characters_is_cut_keeping_its_question_and_answered(db: Database) -> None:
    world = World(db)
    pasted = "This land has been in the family for a long time and the papers are old. " * 42
    asked = pasted + "What is the measurement of Gat 61 at Wagholi?"
    assert len(asked) > 3000

    reply = world.ask(asked)

    assert reply["question"]["text"] == "What is the measurement of Gat 61 at Wagholi?"
    answer = reply["answer"]["answer"]
    assert answer["status"] == "answered" and world.extract in cited(answer)


@pytest.mark.parametrize("gone", ["deleted_at = now()", "is_current = false"])
def test_a_focus_trashed_or_replaced_since_is_never_named(db: Database, gone: str) -> None:
    world = World(db)
    questions = ("Is this measurement in any other document?", "Find documents related to this land")
    firsts = [world.first(world.deed) for _ in questions]  # a conversation each
    with db.customer(world.customer) as conn:
        statement = f"UPDATE documents SET {gone} WHERE customer_id = :c AND id = :d"  # noqa: S608 - fixed text
        conn.execute(text(statement), {"c": world.customer, "d": world.deed})

    for question, first in zip(questions, firsts, strict=True):
        answer = world.ask(question, conversation_id=first["conversation_id"], scope={})["answer"]["answer"]
        assert answer["status"] in ("answered", "not_found")
        never_named(answer, world, world.deed)
        assert not answer["assumptions"]  # no focus the asker can no longer see


def test_a_not_found_answer_in_one_document_leaves_that_document_as_the_focus(db: Database) -> None:
    world = World(db)
    first = world.ask("What rent is paid for the godown?", scope={"document_ids": [world.deed]})
    assert first["answer"]["answer"]["status"] == "not_found"

    second = world.ask(
        "Is this measurement in any other document?", conversation_id=first["conversation_id"], scope={}
    )["answer"]["answer"]

    assert second["assumptions"][0].startswith("Searching your other documents for Survey 61, Wagholi")
    assert world.deed not in cited(second) and world.extract in cited(second)


def test_the_profile_statement_reads_live_current_documents_only(db: Database) -> None:
    world = World(db)
    focus = PgFocus(db)
    assert focus.profile(world.customer, world.trashed) is None  # in Trash
    with db.customer(world.customer) as conn:
        conn.execute(
            text("UPDATE documents SET is_current = false WHERE customer_id = :c AND id = :d"),
            {"c": world.customer, "d": world.kesnand},
        )
    assert focus.profile(world.customer, world.kesnand) is None  # replaced by a newer version
    live = focus.profile(world.customer, world.extract)
    assert live is not None and {f.kind for f in live.facts} == {"property", "person", "place"}
    found = {f.value.document_id for f in focus.matches(world.customer, live, EVERYTHING)}
    # Every shared key, Gat 61 of Lonikand too (ranking leaves it out); never the trashed or replaced.
    assert found == {world.deed, world.lonikand}
